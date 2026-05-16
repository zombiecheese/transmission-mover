from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app import crud
from app.api_serializers import to_safe_destination
from app.api_validators import normalize_destination_payload
from app.db import get_session
from app.schemas import DestinationIn, DestinationSafeOut
from app.ssh_utils import (
    connect_ssh_transport,
    detect_remote_transfer_capabilities,
    validate_remote_base_path,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["destinations"])


def _validate_local_destination_path(payload: DestinationIn) -> None:
    if not os.path.isdir(payload.base_path):
        raise HTTPException(status_code=400, detail=f"Local destination path is not a directory: {payload.base_path}")
    if not os.access(payload.base_path, os.W_OK):
        raise HTTPException(status_code=400, detail=f"Local destination path is not writable: {payload.base_path}")


def _validate_and_enrich_remote_destination(payload: DestinationIn) -> None:
    host = (payload.host or "").strip()
    username = (payload.username or "").strip()
    if not host or not username:
        raise HTTPException(status_code=400, detail="host and username are required for remote destination")

    transport = None
    try:
        transport = connect_ssh_transport(
            host=host,
            port=payload.port,
            username=username,
            password=payload.password,
            private_key=payload.private_key,
            key_passphrase=payload.key_passphrase,
        )

        validation = validate_remote_base_path(
            transport,
            payload.base_path,
            "destination",
            attempt_sudo=payload.attempt_sudo,
        )
        if not validation["ok"]:
            failed = next((item for item in validation["checks"] if not item["passed"]), None)
            hint = failed["hint"] if failed else None
            message = validation["message"]
            if hint:
                message = f"{message}. {hint}"
            raise HTTPException(status_code=400, detail=message)

        caps = detect_remote_transfer_capabilities(
            transport,
            host,
            payload.port,
            attempt_sudo=payload.attempt_sudo,
        )
        available_methods = caps["available_methods"]

        if not available_methods:
            raise RuntimeError("No supported transfer methods are available for this remote destination")

        payload.detected_methods = ",".join(available_methods)
        payload.detected_preferred_method = caps["preferred_method"]
        payload.detected_sftp_port = caps["service_ports"]["sftp"]
        payload.detected_scp_port = caps["service_ports"]["scp"]
        payload.detected_rsync_port = caps["service_ports"]["rsync"]
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to validate remote destination")
        raise HTTPException(status_code=400, detail=f"Failed to validate remote destination: {exc}")
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass


@router.get("/destinations", response_model=list[DestinationSafeOut])
def get_destinations(session: Session = Depends(get_session)) -> list[DestinationSafeOut]:
    return [to_safe_destination(destination) for destination in crud.list_destinations(session)]


@router.post("/destinations", response_model=DestinationSafeOut)
def post_destination(payload: DestinationIn, session: Session = Depends(get_session)) -> DestinationSafeOut:
    payload = normalize_destination_payload(payload)

    if payload.kind == "local":
        _validate_local_destination_path(payload)
    elif payload.kind in {"sftp", "remote"}:
        if not payload.host or not payload.username:
            raise HTTPException(status_code=400, detail="host and username are required for remote destination")
        _validate_and_enrich_remote_destination(payload)

    try:
        obj = crud.create_destination(session, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to create destination: {exc}") from exc
    return to_safe_destination(obj)


@router.put("/destinations/{destination_id}", response_model=DestinationSafeOut)
def put_destination(
    destination_id: int,
    payload: DestinationIn,
    session: Session = Depends(get_session),
) -> DestinationSafeOut:
    payload = normalize_destination_payload(payload)

    if payload.kind == "local":
        _validate_local_destination_path(payload)
    elif payload.kind in {"sftp", "remote"}:
        if not payload.host or not payload.username:
            raise HTTPException(status_code=400, detail="host and username are required for remote destination")
        _validate_and_enrich_remote_destination(payload)

    obj = crud.update_destination(session, destination_id, payload)
    if not obj:
        raise HTTPException(status_code=404, detail="Destination not found")
    return to_safe_destination(obj)


@router.delete("/destinations/{destination_id}")
def remove_destination(destination_id: int, session: Session = Depends(get_session)) -> dict[str, bool]:
    ok = crud.delete_destination(session, destination_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Destination not found")
    return {"deleted": True}
