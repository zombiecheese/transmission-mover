from __future__ import annotations

from sqlmodel import Session
from app.models import AppConfig, Destination, LabelRule
from app.transmission import TransmissionClient
from app.movers import transfer_to_destination, InsufficientSpaceError, RemotePathAccessError
from app.crud import create_log
import logging

logger = logging.getLogger(__name__)

def process_torrent(
    *,
    session: Session,
    client: TransmissionClient,
    app_config: AppConfig,
    label_to_rule: dict[str, LabelRule],
    destination_by_id: dict[int, Destination],
    eligible_labels: set[str] | None,
    respect_rule_schedule: bool,
    torrent: dict,
    log_skip_reasons: bool,
    progress_callback=None,
    method_update_callback=None,
) -> dict[str, object]:
    torrent_name = torrent.get("name", "<unknown>")
    torrent_id = torrent.get("id")

    def _log_activity(message: str, status: str = "info") -> None:
        create_log(
            session,
            torrent_name=torrent_name,
            torrent_id=torrent_id,
            label=matched_label if 'matched_label' in locals() else None,
            destination_name=destination.name if 'destination' in locals() and destination else None,
            status=status,
            message=message,
        )

    if not _is_finished(torrent):
        message = "Torrent is not complete yet"
        if log_skip_reasons:
            create_log(session, torrent_name=torrent_name, torrent_id=torrent_id, status="skipped", message=message)
        return {"processed": False, "message": message}

    labels = torrent.get("labels") or []
    matched_label = next((label for label in labels if label in label_to_rule), None)
    if not matched_label:
        message = "No enabled rule matched this torrent labels"
        if log_skip_reasons:
            create_log(session, torrent_name=torrent_name, torrent_id=torrent_id, status="skipped", message=message)
        return {"processed": False, "message": message}

    rule = label_to_rule[matched_label]
    if eligible_labels is not None and matched_label not in eligible_labels:
        message = "Rule is not due to run at this time"
        if log_skip_reasons:
            create_log(session, torrent_name=torrent_name, torrent_id=torrent_id, status="skipped", message=message)
        return {"processed": False, "message": message}

    destination = destination_by_id.get(rule.destination_id)
    if not destination:
        message = "Destination not found for rule"
        if log_skip_reasons:
            create_log(session, torrent_name=torrent_name, torrent_id=torrent_id, status="skipped", message=message)
        return {"processed": False, "message": message}

    try:
        result = transfer_to_destination(
            torrent_name=torrent_name,
            download_dir=torrent.get("downloadDir"),
            destination=destination,
            app_config=app_config,
            transfer_mode_override=rule.transfer_mode,
            transfer_method_preference_override=rule.transfer_method_preference,
            progress_callback=progress_callback,
            method_update_callback=method_update_callback,
            activity_log_callback=_log_activity,
        )
        create_log(
            session,
            torrent_name=torrent_name,
            torrent_id=torrent_id,
            label=matched_label,
            destination_name=destination.name,
            status="moved",
            message=result,
        )
        return {"processed": True, "message": result}
    except InsufficientSpaceError as exc:
        logger.warning(f"Insufficient space for {torrent_name}: {exc}")
        create_log(
            session,
            torrent_name=torrent_name,
            torrent_id=torrent_id,
            label=matched_label,
            destination_name=destination.name,
            status="skipped",
            message=f"Insufficient space: {exc}",
        )
        return {"processed": False, "message": f"Insufficient space: {exc}"}
    except FileNotFoundError as exc:
        logger.warning("Source path missing for %s: %s", torrent_name, exc)
        create_log(
            session,
            torrent_name=torrent_name,
            torrent_id=torrent_id,
            label=matched_label,
            destination_name=destination.name,
            status="skipped",
            message=f"Source path missing: {exc}",
        )
        return {"processed": False, "message": f"Source path missing: {exc}"}
    except RemotePathAccessError as exc:
        logger.warning("Remote destination path access issue for %s: %s", torrent_name, exc)
        create_log(
            session,
            torrent_name=torrent_name,
            torrent_id=torrent_id,
            label=matched_label,
            destination_name=destination.name,
            status="skipped",
            message=f"Destination path access failed: {exc}",
        )
        return {"processed": False, "message": f"Destination path access failed: {exc}"}
    except Exception as exc:
        logger.exception(f"Failed to move/copy {torrent_name}")
        create_log(
            session,
            torrent_name=torrent_name,
            torrent_id=torrent_id,
            label=matched_label,
            destination_name=destination.name,
            status="error",
            message=f"Failed to move/copy: {exc}",
        )
        return {"processed": False, "message": f"Failed to move/copy: {exc}"}

def _is_finished(torrent: dict) -> bool:
    # This logic is duplicated from worker.py for now; can be unified if needed.
    return bool(torrent.get("isFinished") or torrent.get("percentDone", 0) >= 1.0)
