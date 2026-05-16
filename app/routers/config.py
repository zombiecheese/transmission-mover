from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app import crud
from app.api_serializers import to_safe_app_settings, to_safe_transmission
from app.api_validators import validate_app_settings_payload
from app.db import get_session
from app.runtime import log_activity_error
from app.schemas import (
    AppSettingsIn,
    AppSettingsSafeOut,
    SftpTestIn,
    TransmissionConfigIn,
    TransmissionConfigOut,
    TransmissionContainerModeIn,
)
from app.ssh_utils import (
    connect_ssh_transport,
    detect_remote_transfer_capabilities,
    validate_remote_base_path,
)
from app.transmission import TransmissionClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["config"])


@router.get("/app-settings", response_model=AppSettingsSafeOut | None)
def get_app_settings(session: Session = Depends(get_session)) -> AppSettingsSafeOut | None:
    cfg = crud.get_app_config(session)
    if not cfg:
        return None
    return to_safe_app_settings(cfg)


@router.put("/app-settings", response_model=AppSettingsSafeOut)
def put_app_settings(payload: AppSettingsIn, session: Session = Depends(get_session)) -> AppSettingsSafeOut:
    validate_app_settings_payload(payload)

    # Auto-discover transfer methods if remote SSH source is configured
    if payload.watch_source_kind == "ssh" and payload.watch_host and payload.watch_username and payload.watch_base_path:
        transport = None
        try:
            transport = connect_ssh_transport(
                host=payload.watch_host,
                port=payload.watch_port,
                username=payload.watch_username,
                password=payload.watch_password,
                private_key=payload.watch_private_key,
                key_passphrase=payload.watch_key_passphrase,
            )
            caps = detect_remote_transfer_capabilities(
                transport,
                payload.watch_host,
                payload.watch_port,
                attempt_sudo=payload.watch_attempt_sudo,
            )
            available_methods = caps["available_methods"]

            if "sftp" not in available_methods:
                raise RuntimeError("SFTP subsystem is required for watch source")

            payload.watch_detected_methods = ",".join(available_methods)
            payload.watch_detected_preferred_method = caps["preferred_method"]
            payload.watch_detected_sftp_port = caps["service_ports"]["sftp"]
            payload.watch_detected_scp_port = caps["service_ports"]["scp"]
            payload.watch_detected_rsync_port = caps["service_ports"]["rsync"]
        except Exception as exc:
            logger.exception("Failed to auto-discover transfer methods for watch source")
            raise HTTPException(status_code=400, detail=f"Failed to auto-discover transfer methods for watch source: {exc}")
        finally:
            if transport is not None:
                try:
                    transport.close()
                except Exception:
                    pass

    cfg = crud.upsert_app_config(session, payload)
    return to_safe_app_settings(cfg)


@router.get("/transmission", response_model=TransmissionConfigOut | None)
def get_transmission(session: Session = Depends(get_session)) -> TransmissionConfigOut | None:
    cfg = crud.get_transmission_config(session)
    if not cfg:
        return None
    return to_safe_transmission(cfg)


@router.put("/transmission", response_model=TransmissionConfigOut)
def put_transmission(payload: TransmissionConfigIn, session: Session = Depends(get_session)) -> TransmissionConfigOut:
    cfg = crud.upsert_transmission_config(session, payload)
    return to_safe_transmission(cfg)


@router.put("/app-settings/transmission-container", response_model=AppSettingsSafeOut)
def put_transmission_container_mode(
    payload: TransmissionContainerModeIn,
    session: Session = Depends(get_session),
) -> AppSettingsSafeOut:
    cfg = crud.update_transmission_in_container(session, payload.transmission_in_container)
    return to_safe_app_settings(cfg)


@router.post("/sftp/test")
def test_sftp(payload: SftpTestIn, session: Session = Depends(get_session)) -> dict[str, object]:
    if not payload.base_path:
        raise HTTPException(status_code=400, detail="Base path is required for SFTP test")

    # Check if the destination is local
    is_local = not payload.host and not payload.username
    if is_local:
        if not os.path.exists(payload.base_path):
            raise HTTPException(status_code=400, detail=f"Base path does not exist: {payload.base_path}")
        if not os.access(payload.base_path, os.W_OK):
            raise HTTPException(status_code=400, detail=f"Base path is not writable: {payload.base_path}")
        return {"ok": True, "message": "Local path validation succeeded."}

    role = (payload.role or "destination").strip().lower()
    if role not in {"destination", "source"}:
        raise HTTPException(status_code=400, detail="role must be destination or source")

    transport = None
    try:
        transport = connect_ssh_transport(
            host=payload.host,
            port=payload.port,
            username=payload.username,
            password=payload.password,
            private_key=payload.private_key,
            key_passphrase=payload.key_passphrase,
        )

        validation = validate_remote_base_path(
            transport,
            payload.base_path,
            role,
            attempt_sudo=payload.attempt_sudo,
        )
        if not validation["ok"]:
            raise HTTPException(status_code=400, detail=validation)

        caps = detect_remote_transfer_capabilities(
            transport,
            payload.host,
            payload.port,
            attempt_sudo=payload.attempt_sudo,
        )
        available_methods = caps["available_methods"]

        if role == "source" and "sftp" not in available_methods:
            raise RuntimeError("SFTP subsystem is not available for this watch source")
        if not available_methods:
            raise RuntimeError("No supported transfer methods are available for this remote host")

        return {
            "ok": True,
            "validation": validation,
            "available_methods": available_methods,
            "preferred_method": caps["preferred_method"],
            "service_ports": caps["service_ports"],
            "rsync_mode": caps["rsync_mode"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("SFTP test failed")
        log_activity_error(session, "sftp-test", f"SFTP test failed for {payload.host}:{payload.port}: {exc}")
        raise HTTPException(status_code=400, detail=f"SFTP test failed: {exc}") from exc
    finally:
        try:
            if transport:
                transport.close()
        except Exception:
            pass


@router.post("/transmission/test")
def test_transmission(payload: TransmissionConfigIn, session: Session = Depends(get_session)) -> dict[str, bool]:
    client = TransmissionClient(
        rpc_url=payload.rpc_url or "",
        username=payload.username,
        password=payload.password,
        verify_tls=payload.verify_tls,
    )
    try:
        client.ping()
    except Exception as exc:
        logger.exception("Transmission test failed")
        log_activity_error(session, "transmission-test", f"Transmission test failed for {payload.rpc_url}: {exc}")
        raise HTTPException(status_code=400, detail=f"Transmission test failed: {exc}") from exc
    return {"ok": True}
