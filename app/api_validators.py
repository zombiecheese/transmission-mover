from __future__ import annotations

from fastapi import HTTPException
from sqlmodel import Session

from app import crud
from app.schemas import AppSettingsIn, AppSettingsSourceIn, DestinationIn, LabelRuleIn


def validate_app_settings_payload(payload: AppSettingsIn) -> None:
    validate_source_app_settings_payload(payload)


def validate_source_app_settings_payload(payload: AppSettingsSourceIn) -> None:
    if payload.max_parallel_transfers < 1 or payload.max_parallel_transfers > 8:
        raise HTTPException(status_code=400, detail="max_parallel_transfers must be between 1 and 8")
    if payload.watch_source_kind not in {"local", "ssh"}:
        raise HTTPException(status_code=400, detail="watch_source_kind must be local or ssh")
    if payload.watch_source_kind == "ssh":
        if not payload.watch_host:
            raise HTTPException(status_code=400, detail="watch_host is required for remote SSH watch source")
        if not payload.watch_username:
            raise HTTPException(status_code=400, detail="watch_username is required for remote SSH watch source")
        if not payload.watch_base_path:
            raise HTTPException(status_code=400, detail="watch_base_path is required for remote SSH watch source")


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
        payload.detected_methods = ""
        payload.detected_preferred_method = None
        payload.detected_sftp_port = None
        payload.detected_scp_port = None
        payload.detected_rsync_port = None
    return payload


def validate_rule_payload(session: Session, payload: LabelRuleIn) -> None:
    payload.conflict_policy = (payload.conflict_policy or "overwrite").strip().lower()
    payload.parallelism_mode = (payload.parallelism_mode or "sequential").strip().lower()
    destination = crud.get_destination(session, payload.destination_id)
    if not destination:
        raise HTTPException(status_code=400, detail="Destination does not exist")
    if payload.transfer_mode not in {"move", "copy"}:
        raise HTTPException(status_code=400, detail="transfer_mode must be move or copy")
    if payload.transfer_schedule not in {"auto", "interval", "manual"}:
        raise HTTPException(status_code=400, detail="transfer_schedule must be auto, interval, or manual")
    if payload.transfer_schedule == "interval" and payload.transfer_interval_seconds < 10:
        raise HTTPException(status_code=400, detail="transfer_interval_seconds must be at least 10")
    if payload.transfer_method_preference not in {"auto", "rsync", "scp", "sftp"}:
        raise HTTPException(status_code=400, detail="transfer_method_preference must be auto, rsync, scp, or sftp")
    if payload.conflict_policy not in {"overwrite", "rename", "skip"}:
        raise HTTPException(status_code=400, detail="conflict_policy must be overwrite, rename, or skip")
    if payload.parallelism_mode not in {"parallel", "sequential"}:
        raise HTTPException(status_code=400, detail="parallelism_mode must be parallel or sequential")
    
    # Validate source-destination compatibility
    app_config = crud.get_app_config(session)
    if not app_config:
        raise HTTPException(status_code=400, detail="Application configuration not found")
    
    if app_config.watch_source_kind == "ssh" and destination.kind in {"remote", "sftp"}:
        raise HTTPException(
            status_code=400,
            detail="Remote-to-remote transfers are not supported. Configure either a local source or a local destination.",
        )
