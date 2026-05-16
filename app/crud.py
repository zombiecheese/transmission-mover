from typing import Optional

from sqlmodel import Session, select
from sqlalchemy import desc

from app.models import AppConfig, AuthAuditLog, Destination, LabelRule, MoveLog, TransmissionConfig, WebAuth
from app.secret_crypto import decrypt_secret, encrypt_secret, is_encrypted_secret
from app.schemas import AppSettingsIn, DestinationIn, LabelRuleIn, TransmissionConfigIn


def _detached_copy[T](obj: T) -> T:
    return obj.model_copy(deep=True)


def _decrypt_transmission_config(cfg: Optional[TransmissionConfig]) -> Optional[TransmissionConfig]:
    if not cfg:
        return cfg
    copy = _detached_copy(cfg)
    copy.password = decrypt_secret(cfg.password)
    return copy


def _decrypt_app_config(cfg: Optional[AppConfig]) -> Optional[AppConfig]:
    if not cfg:
        return cfg
    copy = _detached_copy(cfg)
    copy.watch_password = decrypt_secret(cfg.watch_password)
    copy.watch_private_key = decrypt_secret(cfg.watch_private_key)
    copy.watch_key_passphrase = decrypt_secret(cfg.watch_key_passphrase)
    return copy


def _decrypt_destination(obj: Optional[Destination]) -> Optional[Destination]:
    if not obj:
        return obj
    copy = _detached_copy(obj)
    copy.password = decrypt_secret(obj.password)
    copy.private_key = decrypt_secret(obj.private_key)
    copy.key_passphrase = decrypt_secret(obj.key_passphrase)
    return copy


def get_transmission_config(session: Session) -> Optional[TransmissionConfig]:
    return _decrypt_transmission_config(session.get(TransmissionConfig, 1))


def upsert_transmission_config(session: Session, payload: TransmissionConfigIn) -> TransmissionConfig:
    cfg = session.get(TransmissionConfig, 1)
    if not cfg:
        cfg = TransmissionConfig(id=1)

    cfg.rpc_url = payload.rpc_url or ""
    cfg.username = payload.username
    if payload.password is not None:
        cfg.password = encrypt_secret(payload.password)
    cfg.verify_tls = payload.verify_tls

    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    result = _decrypt_transmission_config(cfg)
    if result is None:
        raise ValueError("Failed to decrypt transmission config")
    return result


def get_app_config(session: Session) -> Optional[AppConfig]:
    return _decrypt_app_config(session.get(AppConfig, 1))


def upsert_app_config(session: Session, payload: AppSettingsIn) -> AppConfig:
    cfg = session.get(AppConfig, 1)
    if not cfg:
        cfg = AppConfig(id=1)

    updates = payload.model_dump()
    for key, value in updates.items():
        if key in {"watch_password", "watch_private_key", "watch_key_passphrase"} and value is None:
            continue
        if key in {"watch_password", "watch_private_key", "watch_key_passphrase"}:
            value = encrypt_secret(value)
        setattr(cfg, key, value)

    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    result = _decrypt_app_config(cfg)
    if result is None:
        raise ValueError("Failed to decrypt app config")
    return result


def update_transmission_in_container(session: Session, transmission_in_container: bool) -> AppConfig:
    cfg = session.get(AppConfig, 1)
    if not cfg:
        cfg = AppConfig(id=1)
    cfg.transmission_in_container = bool(transmission_in_container)
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    result = _decrypt_app_config(cfg)
    if result is None:
        raise ValueError("Failed to decrypt app config")
    return result


def list_destinations(session: Session) -> list[Destination]:
    items = list(session.exec(select(Destination).order_by(Destination.name)))
    return [
        d for d in (_decrypt_destination(item) for item in items)
        if d is not None
    ]


def get_destination(session: Session, destination_id: int) -> Optional[Destination]:
    return _decrypt_destination(session.get(Destination, destination_id))


def create_destination(session: Session, payload: DestinationIn) -> Destination:
    model_payload = payload.model_dump()
    model_payload["password"] = encrypt_secret(model_payload.get("password"))
    model_payload["private_key"] = encrypt_secret(model_payload.get("private_key"))
    model_payload["key_passphrase"] = encrypt_secret(model_payload.get("key_passphrase"))
    obj = Destination.model_validate(model_payload)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    result = _decrypt_destination(obj)
    if result is None:
        raise ValueError("Failed to decrypt destination")
    return result


def update_destination(session: Session, destination_id: int, payload: DestinationIn) -> Optional[Destination]:
    obj = session.get(Destination, destination_id)
    if not obj:
        return None

    updates = payload.model_dump()
    for key, value in updates.items():
        if key in {"password", "private_key", "key_passphrase"} and value is None:
            continue
        if key in {"password", "private_key", "key_passphrase"}:
            value = encrypt_secret(value)
        setattr(obj, key, value)

    session.add(obj)
    session.commit()
    session.refresh(obj)
    return _decrypt_destination(obj)


def delete_destination(session: Session, destination_id: int) -> bool:
    obj = session.get(Destination, destination_id)
    if not obj:
        return False
    session.delete(obj)
    session.commit()
    return True


def list_rules(session: Session) -> list[LabelRule]:
    return list(session.exec(select(LabelRule).order_by(LabelRule.label)))


def get_rule(session: Session, rule_id: int) -> Optional[LabelRule]:
    return session.get(LabelRule, rule_id)


def create_rule(session: Session, payload: LabelRuleIn) -> LabelRule:
    obj = LabelRule.model_validate(payload)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj


def update_rule(session: Session, rule_id: int, payload: LabelRuleIn) -> Optional[LabelRule]:
    obj = get_rule(session, rule_id)
    if not obj:
        return None

    updates = payload.model_dump()
    for key, value in updates.items():
        setattr(obj, key, value)

    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj


def delete_rule(session: Session, rule_id: int) -> bool:
    obj = get_rule(session, rule_id)
    if not obj:
        return False
    session.delete(obj)
    session.commit()
    return True


def create_log(
    session: Session,
    torrent_name: str,
    status: str,
    message: str,
    torrent_id: Optional[int] = None,
    label: Optional[str] = None,
    destination_name: Optional[str] = None,
) -> MoveLog:
    log = MoveLog(
        torrent_id=torrent_id,
        torrent_name=torrent_name,
        label=label,
        destination_name=destination_name,
        status=status,
        message=message,
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


def list_logs(session: Session, limit: int = 100) -> list[MoveLog]:
    stmt = select(MoveLog).order_by(desc(getattr(MoveLog, 'created_at'))).limit(limit)
    return list(session.exec(stmt))


def migrate_plaintext_secrets(session: Session) -> None:
    changed = False

    cfg = session.get(TransmissionConfig, 1)
    if cfg and cfg.password and not is_encrypted_secret(cfg.password):
        cfg.password = encrypt_secret(cfg.password)
        session.add(cfg)
        changed = True

    app_cfg = session.get(AppConfig, 1)
    if app_cfg:
        if app_cfg.watch_password and not is_encrypted_secret(app_cfg.watch_password):
            app_cfg.watch_password = encrypt_secret(app_cfg.watch_password)
            changed = True
        if app_cfg.watch_private_key and not is_encrypted_secret(app_cfg.watch_private_key):
            app_cfg.watch_private_key = encrypt_secret(app_cfg.watch_private_key)
            changed = True
        if app_cfg.watch_key_passphrase and not is_encrypted_secret(app_cfg.watch_key_passphrase):
            app_cfg.watch_key_passphrase = encrypt_secret(app_cfg.watch_key_passphrase)
            changed = True
        if changed:
            session.add(app_cfg)

    destinations = list(session.exec(select(Destination)))
    for item in destinations:
        item_changed = False
        if item.password and not is_encrypted_secret(item.password):
            item.password = encrypt_secret(item.password)
            item_changed = True
        if item.private_key and not is_encrypted_secret(item.private_key):
            item.private_key = encrypt_secret(item.private_key)
            item_changed = True
        if item.key_passphrase and not is_encrypted_secret(item.key_passphrase):
            item.key_passphrase = encrypt_secret(item.key_passphrase)
            item_changed = True
        if item_changed:
            session.add(item)
            changed = True

    if changed:
        session.commit()


def get_web_auth(session: Session) -> Optional[WebAuth]:
    """Get the web authentication credentials."""
    return session.get(WebAuth, 1)


def create_or_update_web_auth(session: Session, username_hash: str, password_hash: str) -> WebAuth:
    """Create or update web authentication credentials."""
    from datetime import datetime

    existing = session.get(WebAuth, 1)
    if existing:
        existing.username_hash = username_hash
        existing.password_hash = password_hash
        existing.updated_at = datetime.utcnow()
        session.add(existing)
    else:
        web_auth = WebAuth(id=1, username_hash=username_hash, password_hash=password_hash)
        session.add(web_auth)
    session.commit()
    result = session.get(WebAuth, 1)
    if result is None:
        raise ValueError("WebAuth not found")
    return result


def log_auth_attempt(
    session: Session, username: Optional[str], ip_address: Optional[str], success: bool, message: str
) -> AuthAuditLog:
    """Log an authentication attempt."""
    log = AuthAuditLog(username=username, ip_address=ip_address, success=success, message=message)
    session.add(log)
    session.commit()
    return log
