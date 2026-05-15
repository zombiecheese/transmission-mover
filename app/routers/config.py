from __future__ import annotations

import logging
import socket

import paramiko
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app import crud
from app.api_serializers import to_safe_app_settings, to_safe_transmission
from app.api_validators import validate_app_settings_payload
from app.db import get_session
from app.runtime import log_activity_error
from app.schemas import AppSettingsIn, AppSettingsSafeOut, SftpTestIn, TransmissionConfigIn, TransmissionConfigOut
from app.ssh_utils import (
    parse_private_key,
    remote_can_list_directory,
    remote_can_write_directory,
    remote_has_cmd,
    remote_has_sftp,
    remote_is_directory,
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


@router.post("/sftp/test")
def test_sftp(payload: SftpTestIn, session: Session = Depends(get_session)) -> dict[str, object]:
    if not payload.base_path:
        raise HTTPException(status_code=400, detail="Base path is required for SFTP test")
    role = (payload.role or "destination").strip().lower()
    if role not in {"destination", "source"}:
        raise HTTPException(status_code=400, detail="role must be destination or source")

    def is_tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    transport = None
    try:
        sock = socket.create_connection((payload.host, payload.port), timeout=10)
        transport = paramiko.Transport(sock)
        pkey = parse_private_key(payload.private_key, payload.key_passphrase) if payload.private_key else None
        transport.connect(username=payload.username, password=payload.password, pkey=pkey)

        if not remote_is_directory(transport, payload.base_path):
            raise RuntimeError(f"base path is not a directory: {payload.base_path}")

        if role == "source":
            if not remote_can_list_directory(transport, payload.base_path):
                raise RuntimeError(f"base path is not readable over SSH: {payload.base_path}")
        else:
            if not remote_can_write_directory(transport, payload.base_path):
                raise RuntimeError(f"base path is not writable over SSH: {payload.base_path}")

        has_rsync = remote_has_cmd(transport, "rsync")
        has_scp = remote_has_cmd(transport, "scp")
        has_sftp = remote_has_sftp(transport)
        rsync_daemon_port = 873 if has_rsync and is_tcp_open(payload.host, 873) else None

        available_methods: list[str] = []
        if has_rsync:
            available_methods.append("rsync")
        if has_scp:
            available_methods.append("scp")
        if has_sftp:
            available_methods.append("sftp")

        if role == "source" and not has_sftp:
            raise RuntimeError("SFTP subsystem is not available for this watch source")
        if not available_methods:
            raise RuntimeError("No supported transfer methods are available for this remote host")

        preferred_method = available_methods[0]
        service_ports = {
            "sftp": payload.port if has_sftp else None,
            "scp": payload.port if has_scp else None,
            "rsync": rsync_daemon_port if rsync_daemon_port is not None else (payload.port if has_rsync else None),
        }
        rsync_mode = "daemon" if rsync_daemon_port is not None else ("ssh" if has_rsync else None)

        return {
            "ok": True,
            "available_methods": available_methods,
            "preferred_method": preferred_method,
            "service_ports": service_ports,
            "rsync_mode": rsync_mode,
        }
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
        rpc_url=payload.rpc_url,
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
