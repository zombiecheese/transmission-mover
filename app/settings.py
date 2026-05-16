from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    app_name: str = "Transmission Mover"
    poll_seconds: int = 20
    database_url: str = "sqlite:///./data/app.db"
    log_level: str = "INFO"
    # Initial web auth setup (only used on first startup if no credentials exist in database)
    web_auth_username: str | None = None
    web_auth_password: str | None = None
    secret_encryption_key: str | None = None
    # Dedicated staging path for remote-to-remote transfers
    staging_path: str = "/staging"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = AppSettings()

from pathlib import Path
import os
import shutil

def get_staging_path() -> Path:
    """Return the validated staging path as a Path object. Raises if not present or not writable."""
    path = Path(settings.staging_path)
    if not path.exists():
        raise RuntimeError(f"Staging path does not exist: {path}")
    if not path.is_dir():
        raise RuntimeError(f"Staging path is not a directory: {path}")
    if not os.access(path, os.W_OK):
        raise RuntimeError(f"Staging path is not writable: {path}")
    # Optionally check for free space (e.g., at least 1MB)
    min_bytes = 1024 * 1024
    free_bytes = shutil.disk_usage(path).free
    if free_bytes < min_bytes:
        raise RuntimeError(f"Staging path has insufficient free space: {free_bytes} bytes")
    return path
