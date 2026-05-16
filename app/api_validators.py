from __future__ import annotations

from fastapi import HTTPException
from sqlmodel import Session

from app import crud
from app.schemas import AppSettingsIn, DestinationIn, LabelRuleIn


def validate_app_settings_payload(payload: AppSettingsIn) -> None:
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
        payload.attempt_sudo = False
        payload.transfer_method_preference = "auto"
        payload.detected_methods = ""
        payload.detected_preferred_method = None
        payload.detected_sftp_port = None
        payload.detected_scp_port = None
        payload.detected_rsync_port = None
    return payload


def validate_rule_payload(session: Session, payload: LabelRuleIn) -> None:
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
    
    # Validate source-destination compatibility
    app_config = crud.get_app_config(session)
    if not app_config:
        raise HTTPException(status_code=400, detail="Application configuration not found")
    
    # Check if source is remote and has detected methods
    source_methods = set()
    if app_config.watch_source_kind == "ssh" and app_config.watch_detected_methods:
        source_methods = set(app_config.watch_detected_methods.split(","))
    
    # Check if destination is remote and has detected methods
    dest_methods = set()
    if destination.kind in {"remote", "sftp"}:
        if destination.detected_methods:
            dest_methods = {m.strip().lower() for m in destination.detected_methods.split(",") if m.strip()}
        elif destination.detected_preferred_method:
            dest_methods.add(destination.detected_preferred_method.lower())
    elif destination.kind == "local":
        # Local destinations support all methods
        dest_methods = {"local"}
    
    # For remote-to-remote, check compatibility
    if app_config.watch_source_kind == "ssh" and destination.kind in {"remote", "sftp"}:
        # Both are remote, check for common methods
        if not source_methods or not dest_methods:
            raise HTTPException(status_code=400, detail="Source and destination transfer methods not detected. Please reconfigure them.")
        # SFTP should always be available as fallback
        common_methods = source_methods & dest_methods
        if not common_methods and "sftp" not in source_methods:
            raise HTTPException(status_code=400, detail=f"No compatible transfer methods between source and destination. Source: {source_methods}, Destination: {dest_methods}")
    
    # For local source to remote destination, SFTP is required
    if app_config.watch_source_kind == "local" and destination.kind in {"remote", "sftp"}:
        if not dest_methods:
            raise HTTPException(status_code=400, detail="Destination transfer methods not detected. Please reconfigure the destination.")
