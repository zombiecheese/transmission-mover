from __future__ import annotations

from app.models import AppConfig, Destination, LabelRule, TransmissionConfig
from app.schemas import (
    AppSettingsSafeOut,
    DestinationSafeOut,
    LabelRuleOut,
    TorrentOut,
    TransmissionConfigOut,
)


def to_safe_destination(destination: Destination) -> DestinationSafeOut:
    payload = destination.model_dump()
    payload["kind"] = "remote" if payload.get("kind") == "sftp" else payload.get("kind")
    payload.pop("password", None)
    payload.pop("private_key", None)
    payload.pop("key_passphrase", None)
    payload["has_password"] = bool(destination.password)
    payload["has_private_key"] = bool(destination.private_key)
    return DestinationSafeOut.model_validate(payload)


def to_safe_app_settings(cfg: AppConfig) -> AppSettingsSafeOut:
    payload = cfg.model_dump()
    payload.pop("watch_password", None)
    payload.pop("watch_private_key", None)
    payload.pop("watch_key_passphrase", None)
    payload["has_watch_password"] = bool(cfg.watch_password)
    payload["has_watch_private_key"] = bool(cfg.watch_private_key)
    return AppSettingsSafeOut.model_validate(payload)


def to_safe_transmission(cfg: TransmissionConfig) -> TransmissionConfigOut:
    return TransmissionConfigOut(
        rpc_url=cfg.rpc_url,
        username=cfg.username,
        verify_tls=cfg.verify_tls,
        has_password=bool(cfg.password),
    )


def to_rule_out(rule: LabelRule, destination_name: str | None = None) -> LabelRuleOut:
    return LabelRuleOut(
        id=rule.id or 0,
        label=rule.label,
        destination_id=rule.destination_id,
        enabled=rule.enabled,
        transfer_mode=rule.transfer_mode,
        transfer_schedule=rule.transfer_schedule,
        transfer_interval_seconds=rule.transfer_interval_seconds,
        destination_name=destination_name,
    )


def to_torrent_out(torrent: dict) -> TorrentOut:
    return TorrentOut(
        id=int(torrent["id"]),
        name=str(torrent.get("name", "")),
        labels=list(torrent.get("labels") or []),
        status=int(torrent.get("status", 0)),
        percent_done=float(torrent.get("percentDone", 0.0)),
    )
