from __future__ import annotations

import argparse

from sqlmodel import Session, create_engine, select

from app.models import AppConfig, Destination, TransmissionConfig
from app.secret_crypto import (
    decrypt_secret_with_key,
    encrypt_secret_with_key,
    is_encrypted_secret,
)
from app.settings import settings


def _rotate_value(value: str | None, old_key: str, new_key: str) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    if value == "":
        return "", False

    plaintext = value
    if is_encrypted_secret(value):
        plaintext = decrypt_secret_with_key(value, old_key)

    rotated = encrypt_secret_with_key(plaintext, new_key)
    return rotated, rotated != value


def _rotate_field(obj, field_name: str, old_key: str, new_key: str) -> bool:
    current = getattr(obj, field_name)
    rotated, changed = _rotate_value(current, old_key, new_key)
    if changed:
        setattr(obj, field_name, rotated)
    return changed


def rotate_all_secrets(database_url: str, old_key: str, new_key: str) -> dict[str, int]:
    # Validate both keys before touching storage.
    encrypt_secret_with_key("probe", old_key)
    encrypt_secret_with_key("probe", new_key)

    engine = create_engine(database_url, connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {})

    counts = {
        "transmission": 0,
        "app_config": 0,
        "destinations": 0,
    }

    with Session(engine) as session:
        changed = False

        transmission = session.get(TransmissionConfig, 1)
        if transmission and _rotate_field(transmission, "password", old_key, new_key):
            session.add(transmission)
            counts["transmission"] += 1
            changed = True

        app_cfg = session.get(AppConfig, 1)
        if app_cfg:
            app_cfg_changed = False
            for field_name in ["watch_password", "watch_private_key", "watch_key_passphrase"]:
                app_cfg_changed = _rotate_field(app_cfg, field_name, old_key, new_key) or app_cfg_changed
            if app_cfg_changed:
                session.add(app_cfg)
                counts["app_config"] += 1
                changed = True

        for dest in session.exec(select(Destination)):
            dest_changed = False
            for field_name in ["password", "private_key", "key_passphrase"]:
                dest_changed = _rotate_field(dest, field_name, old_key, new_key) or dest_changed
            if dest_changed:
                session.add(dest)
                counts["destinations"] += 1
                changed = True

        if changed:
            session.commit()

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Rotate encrypted secrets to a new Fernet key")
    parser.add_argument("--old-key", required=True, help="Old Fernet key currently used to decrypt values")
    parser.add_argument("--new-key", required=True, help="New Fernet key to encrypt values with")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="Database URL (defaults to DATABASE_URL setting)",
    )

    args = parser.parse_args()

    counts = rotate_all_secrets(args.database_url, args.old_key, args.new_key)
    total = sum(counts.values())

    print("Secret rotation complete")
    print(f"- transmission rows updated: {counts['transmission']}")
    print(f"- app config rows updated: {counts['app_config']}")
    print(f"- destination rows updated: {counts['destinations']}")
    print(f"- total rows updated: {total}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
