from __future__ import annotations

from fastapi import HTTPException
from sqlmodel import Session

from app import crud
from app.schemas import AppSettingsIn, DestinationIn, LabelRuleIn


def validate_app_settings_payload(payload: AppSettingsIn) -> None:
    if payload.watch_source_kind not in {"local", "sftp"}:
        raise HTTPException(status_code=400, detail="watch_source_kind must be local or sftp")
    if payload.watch_source_kind == "sftp":
        if not payload.watch_host:
            raise HTTPException(status_code=400, detail="watch_host is required for sftp watch source")
        if not payload.watch_username:
            raise HTTPException(status_code=400, detail="watch_username is required for sftp watch source")
        if not payload.watch_base_path:
            raise HTTPException(status_code=400, detail="watch_base_path is required for sftp watch source")


def normalize_destination_kind(kind: str) -> str:
    lowered = (kind or "").strip().lower()
    if lowered in {"local", "remote"}:
        return lowered
    if lowered == "sftp":
        return "remote"
    raise HTTPException(status_code=400, detail="kind must be local or remote")


def normalize_transfer_method_preference(value: str | None) -> str:
    lowered = (value or "auto").strip().lower()
    if lowered not in {"auto", "rsync", "scp", "sftp"}:
        raise HTTPException(status_code=400, detail="transfer_method_preference must be auto, rsync, scp, or sftp")
    return lowered


def normalize_destination_payload(payload: DestinationIn) -> DestinationIn:
    payload.kind = normalize_destination_kind(payload.kind)
    payload.transfer_method_preference = normalize_transfer_method_preference(payload.transfer_method_preference)
    if payload.kind == "local":
        payload.transfer_method_preference = "auto"
        payload.detected_preferred_method = None
        payload.detected_sftp_port = None
        payload.detected_scp_port = None
        payload.detected_rsync_port = None
    return payload


def validate_rule_payload(session: Session, payload: LabelRuleIn) -> None:
    if not crud.get_destination(session, payload.destination_id):
        raise HTTPException(status_code=400, detail="Destination does not exist")
    if payload.transfer_mode not in {"move", "copy"}:
        raise HTTPException(status_code=400, detail="transfer_mode must be move or copy")
    if payload.transfer_schedule not in {"auto", "interval", "manual"}:
        raise HTTPException(status_code=400, detail="transfer_schedule must be auto, interval, or manual")
    if payload.transfer_schedule == "interval" and payload.transfer_interval_seconds < 10:
        raise HTTPException(status_code=400, detail="transfer_interval_seconds must be at least 10")
