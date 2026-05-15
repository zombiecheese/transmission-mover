from __future__ import annotations

import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.settings import settings

logger = logging.getLogger(__name__)

_ENCRYPTION_PREFIX = "enc:v1:"
_fernet: Fernet | None = None


def _fernet_from_key(raw_key: str) -> Fernet:
    key = (raw_key or "").strip()
    if not key:
        raise RuntimeError("Encryption key is required")
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise RuntimeError("Encryption key must be a valid Fernet key") from exc


def _load_or_generate_key() -> str:
    """Load encryption key from settings, or generate and persist one if not provided."""
    key = (settings.secret_encryption_key or "").strip()
    if key:
        return key
    
    # Try to load from persisted key file in data directory
    key_file = Path("/data/.encryption_key")
    if key_file.exists():
        try:
            key = key_file.read_text().strip()
            if key:
                logger.info("Loaded persisted encryption key from /data/.encryption_key")
                return key
        except Exception as exc:
            logger.warning(f"Failed to load persisted key: {exc}")
    
    # Generate a new key
    generated_key = Fernet.generate_key().decode("utf-8")
    
    # Try to persist it for future container restarts
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(generated_key)
        logger.warning(
            "⚠️  Generated new encryption key and saved to /data/.encryption_key\n"
            f"   For deployment, set SECRET_ENCRYPTION_KEY={generated_key}\n"
            "   This key will persist across container restarts."
        )
    except Exception as exc:
        logger.warning(
            f"Could not persist key to /data/.encryption_key: {exc}\n"
            f"Generated key (save this for deployment): {generated_key}"
        )
    
    return generated_key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    key = _load_or_generate_key()
    _fernet = _fernet_from_key(key)
    return _fernet


def ensure_secret_crypto_ready() -> None:
    _get_fernet()


def is_encrypted_secret(value: str | None) -> bool:
    return bool(value and value.startswith(_ENCRYPTION_PREFIX))


def encrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "":
        return ""
    if value.startswith(_ENCRYPTION_PREFIX):
        return value

    token = _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{_ENCRYPTION_PREFIX}{token}"


def encrypt_secret_with_key(value: str | None, raw_key: str) -> str | None:
    if value is None:
        return None
    if value == "":
        return ""
    if value.startswith(_ENCRYPTION_PREFIX):
        return value

    token = _fernet_from_key(raw_key).encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{_ENCRYPTION_PREFIX}{token}"


def decrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "":
        return ""
    if not value.startswith(_ENCRYPTION_PREFIX):
        return value

    token = value[len(_ENCRYPTION_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Unable to decrypt stored secret with current SECRET_ENCRYPTION_KEY") from exc


def decrypt_secret_with_key(value: str | None, raw_key: str) -> str | None:
    if value is None:
        return None
    if value == "":
        return ""
    if not value.startswith(_ENCRYPTION_PREFIX):
        return value

    token = value[len(_ENCRYPTION_PREFIX):]
    try:
        return _fernet_from_key(raw_key).decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Unable to decrypt stored secret with provided key") from exc