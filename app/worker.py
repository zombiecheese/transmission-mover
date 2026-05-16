"""Background worker for scheduled/manual torrent transfers."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlmodel import Session, select

from app import crud
from app.crud import create_log
from app.db import engine
from app.models import AppConfig, Destination, LabelRule
from app.transfer_executor import process_torrent
from app.transmission import TransmissionClient

logger = logging.getLogger(__name__)


class MoveWorker:
    def __init__(self, poll_seconds: int = 20) -> None:
        # Enforce a minimum poll interval of 1 second
        self.poll_seconds = max(int(poll_seconds), 1)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._active_transfers: dict[str, dict[str, Any]] = {}
        self._last_rule_run_at: dict[int, float] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="move-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self._thread = None

    def run_once(self) -> dict[str, bool | str]:
        return self._run_cycle(log_skip_reasons=True, respect_rule_schedule=False)

    def run_torrent_now(self, torrent_id: int) -> dict[str, bool | str]:
        return self._run_cycle(
            specific_torrent_id=torrent_id,
            log_skip_reasons=True,
            respect_rule_schedule=False,
        )

    def get_active_transfers(self) -> list[dict[str, Any]]:
        with self._state_lock:
            return [dict(item) for item in self._active_transfers.values()]

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            cycle_start = time.time()
            # Check for Transmission settings before running a cycle
            with Session(engine) as session:
                cfg = crud.get_transmission_config(session)
            if not cfg:
                logger.warning("MoveWorker: Transmission settings not configured. Skipping poll. Sleeping 30s.")
                self._stop_event.wait(30)
                continue
            result = self._run_cycle(log_skip_reasons=False, respect_rule_schedule=True)
            cycle_end = time.time()
            elapsed = cycle_end - cycle_start
            message = str(result.get("message", ""))
            logger.info(f"MoveWorker cycle complete in {elapsed:.2f}s: {message} (ok={result.get('ok')})")
            # If the cycle was very fast (e.g., due to error), always sleep at least 1 second
            sleep_time = max(self.poll_seconds - elapsed, 1)
            if elapsed < 1.0:
                benign_fast_messages = {
                    "No enabled rules with valid destinations",
                    "No rules are due to run at this time",
                    "No eligible torrents matched the enabled rules",
                }
                if message in benign_fast_messages:
                    logger.debug(
                        "MoveWorker cycle completed quickly in %.2fs with no work due; forcing 1s sleep.",
                        elapsed,
                    )
                else:
                    logger.warning(
                        "MoveWorker cycle completed in %.2fs (too fast, possible error loop). Forcing 1s sleep.",
                        elapsed,
                    )
            self._stop_event.wait(sleep_time)

    def _run_cycle(
        self,
        specific_torrent_id: int | None = None,
        log_skip_reasons: bool = False,
        respect_rule_schedule: bool = True,
    ) -> dict[str, bool | str]:
        try:
            with Session(engine) as session:
                cfg = crud.get_transmission_config(session)
                if not cfg:
                    return {"ok": False, "message": "Transmission settings are not configured"}

                app_config = crud.get_app_config(session) or AppConfig(id=1)
                rules = list(session.exec(select(LabelRule).where(LabelRule.enabled == True)))
                destination_by_id: dict[int, Destination] = {
                    d.id: d for d in crud.list_destinations(session) if d.id is not None
                }
                label_to_rule = {
                    rule.label: rule
                    for rule in rules
                    if destination_by_id.get(rule.destination_id) is not None
                }
                if not label_to_rule:
                    return {"ok": False, "message": "No enabled rules with valid destinations"}

                eligible_labels: set[str] | None = None
                if respect_rule_schedule and specific_torrent_id is None:
                    now = time.time()
                    eligible_labels = set()
                    for rule in label_to_rule.values():
                        schedule = (rule.transfer_schedule or "auto").lower()
                        if schedule == "manual":
                            continue
                        if schedule == "interval":
                            interval = max(int(rule.transfer_interval_seconds or self.poll_seconds), 10)
                            if rule.id is not None and now - self._last_rule_run_at.get(rule.id, 0.0) < interval:
                                continue
                        eligible_labels.add(rule.label)
                    if not eligible_labels:
                        return {"ok": False, "message": "No rules are due to run at this time"}

                client = TransmissionClient(
                    rpc_url=cfg.rpc_url,
                    username=cfg.username,
                    password=cfg.password,
                    verify_tls=cfg.verify_tls,
                )

                try:
                    torrents = client.get_torrents()
                except Exception as exc:
                    logger.exception("Failed to get torrents")
                    create_log(
                        session,
                        torrent_name="<rpc>",
                        status="error",
                        message=f"Transmission request failed: {exc}",
                    )
                    return {"ok": False, "message": f"Transmission request failed: {exc}"}

                if specific_torrent_id is not None:
                    torrents = [t for t in torrents if int(t.get("id", -1)) == specific_torrent_id]
                    if not torrents:
                        return {"ok": False, "message": f"Torrent {specific_torrent_id} not found"}

                processed = 0
                last_message = "No eligible torrents matched the enabled rules"
                for torrent in torrents:
                    result = self._process_torrent(
                        session=session,
                        client=client,
                        app_config=app_config,
                        label_to_rule=label_to_rule,
                        destination_by_id=destination_by_id,
                        eligible_labels=eligible_labels,
                        respect_rule_schedule=respect_rule_schedule,
                        torrent=torrent,
                        log_skip_reasons=log_skip_reasons,
                    )
                    if bool(result.get("processed")):
                        processed += 1
                    if result.get("message"):
                        last_message = str(result["message"])

                if respect_rule_schedule and specific_torrent_id is None:
                    now = time.time()
                    for rule in label_to_rule.values():
                        if rule.id is None:
                            continue
                        schedule = (rule.transfer_schedule or "auto").lower()
                        if schedule == "manual":
                            continue
                        if eligible_labels is None or rule.label in eligible_labels:
                            self._last_rule_run_at[rule.id] = now

                return {
                    "ok": processed > 0,
                    "message": f"Processed {processed} torrent(s)" if processed else last_message,
                }
        except Exception as exc:
            logger.exception("Unexpected error in run_cycle")
            return {"ok": False, "message": f"Unexpected error in run_cycle: {exc}"}

    def _process_torrent(
        self,
        *,
        session: Session,
        client: TransmissionClient,
        app_config: AppConfig,
        label_to_rule: dict[str, LabelRule],
        destination_by_id: dict[int, Destination],
        eligible_labels: set[str] | None,
        respect_rule_schedule: bool,
        torrent: dict[str, Any],
        log_skip_reasons: bool,
    ) -> dict[str, object]:
        torrent_id = int(torrent.get("id", -1)) if torrent.get("id") is not None else None
        torrent_name = str(torrent.get("name") or "<unknown>")
        transfer_key = f"{torrent_id}:{torrent_name}"
        labels = torrent.get("labels") or []
        matched_label = next((label for label in labels if label in label_to_rule), None)
        rule = label_to_rule.get(matched_label) if matched_label else None
        destination = destination_by_id.get(rule.destination_id) if rule else None
        transfer_method = self._estimate_transfer_method(rule, destination, app_config)

        started_at = time.time()

        def _progress_callback(transferred_bytes: int, total_bytes: int) -> None:
            elapsed = max(time.time() - started_at, 0.001)
            percent = (float(transferred_bytes) / float(total_bytes) * 100.0) if total_bytes > 0 else 0.0
            with self._state_lock:
                self._active_transfers[transfer_key] = {
                    "torrent_id": torrent_id,
                    "torrent_name": torrent_name,
                    "destination_name": destination.name if destination else None,
                    "mode": str((rule.transfer_mode if rule else app_config.transfer_mode) or "move"),
                    "method": transfer_method,
                    "transferred_bytes": int(transferred_bytes),
                    "total_bytes": int(total_bytes),
                    "speed_bytes_per_sec": float(transferred_bytes) / elapsed,
                    "percent": max(0.0, min(percent, 100.0)),
                }

        def _method_update_callback(method_name: str) -> None:
            nonlocal transfer_method
            normalized = str(method_name or "").strip()
            if not normalized:
                return
            transfer_method = normalized
            with self._state_lock:
                if transfer_key in self._active_transfers:
                    self._active_transfers[transfer_key]["method"] = transfer_method

        try:
            result = process_torrent(
                session=session,
                client=client,
                app_config=app_config,
                label_to_rule=label_to_rule,
                destination_by_id=destination_by_id,
                eligible_labels=eligible_labels if respect_rule_schedule else None,
                respect_rule_schedule=respect_rule_schedule,
                torrent=torrent,
                log_skip_reasons=log_skip_reasons,
                progress_callback=_progress_callback,
                method_update_callback=_method_update_callback,
            )

            if bool(result.get("processed")) and torrent_id is not None and rule and rule.remove_from_client:
                try:
                    client.remove_torrent(torrent_id, delete_local_data=bool(rule.trash_data_on_remove))
                except Exception as exc:
                    logger.warning("Failed to remove torrent %s after transfer: %s", torrent_id, exc)
                    create_log(
                        session,
                        torrent_name=torrent_name,
                        torrent_id=torrent_id,
                        status="error",
                        message=f"Transfer succeeded but torrent removal failed: {exc}",
                    )

            return result
        finally:
            with self._state_lock:
                self._active_transfers.pop(transfer_key, None)

    @staticmethod
    def _parse_method_csv(value: str | None) -> list[str]:
        return [m.strip().lower() for m in str(value or "").split(",") if m.strip()]

    @staticmethod
    def _choose_method(available_methods: set[str], rule_pref: str | None, destination_pref: str | None, detected_pref: str | None) -> str:
        ordered_fallback = ["rsync", "scp", "sftp"]
        rp = str(rule_pref or "auto").lower()
        dp = str(destination_pref or "auto").lower()
        detected = str(detected_pref or "").lower()

        if rp != "auto":
            if rp in available_methods:
                return rp
            return next((m for m in ordered_fallback if m in available_methods), "sftp")

        if dp != "auto":
            if dp in available_methods:
                return dp
            return next((m for m in ordered_fallback if m in available_methods), "sftp")

        if detected and detected in available_methods:
            return detected

        return next((m for m in ordered_fallback if m in available_methods), "sftp")

    def _estimate_transfer_method(self, rule: LabelRule | None, destination: Destination | None, app_config: AppConfig) -> str:
        if not rule or not destination:
            return ""

        source_kind = str(app_config.watch_source_kind or "local").lower()
        destination_kind = str(destination.kind or "local").lower()

        if source_kind == "local" and destination_kind == "local":
            return "local filesystem"

        if source_kind == "ssh" and destination_kind == "local":
            return "sftp (source -> local, no staging)"

        if source_kind == "local" and destination_kind in {"remote", "sftp"}:
            destination_methods = set(self._parse_method_csv(destination.detected_methods)) or {"sftp"}
            return self._choose_method(
                destination_methods,
                rule.transfer_method_preference,
                destination.transfer_method_preference,
                destination.detected_preferred_method,
            )

        if source_kind == "ssh" and destination_kind in {"remote", "sftp"}:
            source_methods = set(self._parse_method_csv(app_config.watch_detected_methods)) or {"sftp"}
            destination_methods = set(self._parse_method_csv(destination.detected_methods)) or {"sftp"}
            direct_candidates = {m for m in ["rsync", "scp", "sftp"] if m in source_methods and m in destination_methods}
            if not direct_candidates:
                return "staged via sftp"
            return self._choose_method(
                direct_candidates,
                rule.transfer_method_preference,
                destination.transfer_method_preference,
                destination.detected_preferred_method,
            )

        return "auto"



