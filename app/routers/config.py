from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from requests import HTTPError, RequestException
from sqlmodel import Session

from app import crud
from app.api_serializers import to_safe_app_settings, to_safe_transmission
from app.api_validators import validate_source_app_settings_payload
from app.db import get_session
from app.runtime import log_activity_error
from app.schemas import (
    AppSettingsSafeOut,
    AppSettingsIgnoredLabelsIn,
    AppSettingsRemapIn,
    AppSettingsSourceIn,
    SftpTestIn,
    TransmissionConfigIn,
    TransmissionConfigOut,
    TransmissionContainerModeIn,
)
from app.settings import settings
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


@router.put("/app-settings/source", response_model=AppSettingsSafeOut)
def put_app_settings_source(payload: AppSettingsSourceIn, session: Session = Depends(get_session)) -> AppSettingsSafeOut:
    validate_source_app_settings_payload(payload)

    if payload.watch_source_kind == "ssh":
        has_remote_destination = any(
            (dest.kind or "").lower() in {"remote", "sftp"}
            for dest in crud.list_destinations(session)
        )
        if has_remote_destination:
            raise HTTPException(
                status_code=400,
                detail="Remote-to-remote transfers are not supported. Switch source to local or convert remote destinations to local.",
            )

    existing_cfg = crud.get_app_config(session)
    effective_watch_password = payload.watch_password
    effective_watch_private_key = payload.watch_private_key
    effective_watch_key_passphrase = payload.watch_key_passphrase
    if existing_cfg is not None:
        if effective_watch_password is None:
            effective_watch_password = existing_cfg.watch_password
        if effective_watch_private_key is None:
            effective_watch_private_key = existing_cfg.watch_private_key
        if effective_watch_key_passphrase is None:
            effective_watch_key_passphrase = existing_cfg.watch_key_passphrase

    updates: dict[str, object] = payload.model_dump()

    # Auto-discover transfer methods if remote SSH source is configured
    if payload.watch_source_kind == "ssh" and payload.watch_host and payload.watch_username and payload.watch_base_path:
        transport = None
        try:
            transport = connect_ssh_transport(
                host=payload.watch_host,
                port=payload.watch_port,
                username=payload.watch_username,
                password=effective_watch_password,
                private_key=effective_watch_private_key,
                key_passphrase=effective_watch_key_passphrase,
            )
            caps = detect_remote_transfer_capabilities(
                transport,
                payload.watch_host,
                payload.watch_port,
            )
            available_methods = caps["available_methods"]

            if "sftp" not in available_methods:
                raise RuntimeError("SFTP subsystem is required for watch source")

            updates["watch_detected_methods"] = ",".join(available_methods)
            updates["watch_detected_preferred_method"] = caps["preferred_method"]
            updates["watch_detected_sftp_port"] = caps["service_ports"]["sftp"]
            updates["watch_detected_scp_port"] = caps["service_ports"]["scp"]
            updates["watch_detected_rsync_port"] = caps["service_ports"]["rsync"]
        except Exception as exc:
            logger.exception("Failed to auto-discover transfer methods for watch source")
            raise HTTPException(status_code=400, detail=f"Failed to auto-discover transfer methods for watch source: {exc}")
        finally:
            if transport is not None:
                try:
                    transport.close()
                except Exception:
                    pass
    else:
        # Clear stale auto-detected metadata when source is local or incomplete.
        updates["watch_detected_methods"] = ""
        updates["watch_detected_preferred_method"] = None
        updates["watch_detected_sftp_port"] = None
        updates["watch_detected_scp_port"] = None
        updates["watch_detected_rsync_port"] = None

    cfg = crud.update_app_config_fields(session, updates)
    return to_safe_app_settings(cfg)


@router.put("/app-settings/remap", response_model=AppSettingsSafeOut)
def put_app_settings_remap(payload: AppSettingsRemapIn, session: Session = Depends(get_session)) -> AppSettingsSafeOut:
    cfg = crud.update_app_config_fields(session, payload.model_dump())
    return to_safe_app_settings(cfg)


@router.put("/app-settings/ignored-labels", response_model=AppSettingsSafeOut)
def put_app_settings_ignored_labels(payload: AppSettingsIgnoredLabelsIn, session: Session = Depends(get_session)) -> AppSettingsSafeOut:
    normalized = ",".join(label.strip() for label in payload.ignored_labels.split(",") if label.strip())
    cfg = crud.update_app_config_fields(session, {"ignored_labels": normalized})
    return to_safe_app_settings(cfg)


@router.post("/app-settings/reseed-static")
def reseed_static_assets() -> dict[str, object]:
    """Restore web UI files from the image-baked default copy, overwriting user edits."""
    source_dir = Path(settings.default_static_dir)
    target_dir = Path(settings.static_dir)
    if not source_dir.is_dir():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Default static assets directory '{source_dir}' is not available. "
                "Reseed is only supported when image-baked defaults are present (e.g. inside the Docker image)."
            ),
        )
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for src_path in source_dir.rglob("*"):
            rel = src_path.relative_to(source_dir)
            dest_path = target_dir / rel
            if src_path.is_dir():
                dest_path.mkdir(parents=True, exist_ok=True)
                continue
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            # Use copy (not copy2) so the destination gets a fresh mtime/ctime.
            # That guarantees the StaticFiles ETag/Last-Modified differ from any
            # previously served user-edited version, so browsers re-download.
            shutil.copy(src_path, dest_path)
            copied.append(str(rel).replace("\\", "/"))
    except Exception as exc:
        logger.exception("Failed to reseed static assets")
        raise HTTPException(status_code=500, detail=f"Failed to reseed static assets: {exc}") from exc

    logger.info("Reseeded %d static files from %s to %s", len(copied), source_dir, target_dir)
    return {"ok": True, "files": copied, "count": len(copied)}


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
        )
        if not validation["ok"]:
            raise HTTPException(status_code=400, detail=validation)

        caps = detect_remote_transfer_capabilities(
            transport,
            payload.host,
            payload.port,
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
        if "SSH session closed unexpectedly while running remote command" in str(exc):
            logger.warning("SFTP test failed due to SSH session/channel closure for %s:%s: %s", payload.host, payload.port, exc)
            detail = (
                "SFTP test failed: SSH session closed unexpectedly while validating remote path. "
                "Check SSH server stability, shell command execution permissions, and retry."
            )
        else:
            logger.exception("SFTP test failed")
            detail = f"SFTP test failed: {exc}"
        log_activity_error(session, "sftp-test", f"SFTP test failed for {payload.host}:{payload.port}: {exc}")
        raise HTTPException(status_code=400, detail=detail) from exc
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
        if isinstance(exc, HTTPError) and exc.response is not None and exc.response.status_code == 401:
            logger.warning("Transmission test unauthorized for %s", payload.rpc_url)
            detail = (
                f"Transmission test failed: unauthorized at {payload.rpc_url}. "
                "Check Transmission username/password."
            )
        elif isinstance(exc, RequestException):
            logger.warning("Transmission test failed for %s: %s", payload.rpc_url, exc)
            detail = (
                f"Transmission test failed: unable to reach RPC endpoint at {payload.rpc_url}. "
                "Check host, port, network reachability, and TLS settings."
            )
        else:
            logger.exception("Transmission test failed")
            detail = f"Transmission test failed: {exc}"
        log_activity_error(session, "transmission-test", f"Transmission test failed for {payload.rpc_url}: {exc}")
        raise HTTPException(status_code=400, detail=detail) from exc
    return {"ok": True}
