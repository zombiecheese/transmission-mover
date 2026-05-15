    """
    Orchestrates scheduling, polling, and rule-based selection of torrents to move/copy.
    Delegates all transfer execution to transfer_executor.process_torrent.
    """
from __future__ import annotations

import logging
import threading
import time

from sqlmodel import Session, select

from app import crud
from app.crud import create_log
from app.db import engine
from app.models import AppConfig, Destination, LabelRule, TransmissionConfig
from app.movers import InsufficientSpaceError
from app.transfer_executor import process_torrent
from app.transmission import TransmissionClient

    def _process_torrent(self, **kwargs) -> dict[str, object]:
            # Delegates to transfer_executor.process_torrent
            return process_torrent(**kwargs)
        return process_torrent(**kwargs)
    ) -> dict[str, bool | str]:
        with Session(engine) as session:
            cfg = crud.get_transmission_config(session)
            if not cfg:
                return {"ok": False, "message": "Transmission settings are not configured"}

            app_config = crud.get_app_config(session) or AppConfig(id=1)
            rules = list(session.exec(select(LabelRule).where(LabelRule.enabled.is_(True))))
            destination_by_id = {d.id: d for d in crud.list_destinations(session) if d.id is not None}
            label_to_rule = {
                rule.label: rule
                for rule in rules
                if destination_by_id.get(rule.destination_id)
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
                if result.get("processed"):
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
                    if eligible_labels is not None and rule.label in eligible_labels:
                        self._last_rule_run_at[rule.id] = now

            return {"ok": processed > 0, "message": f"Processed {processed} torrent(s)" if processed else last_message}


    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._run_scheduled()
            self._stop_event.wait(self.poll_seconds)

    def _run_scheduled(self) -> None:
        self._run_cycle(specific_torrent_id=None, log_skip_reasons=False, respect_rule_schedule=True)



