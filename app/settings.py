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
    # Directories used to seed and serve the web UI static assets.
    static_dir: str = "static"
    default_static_dir: str = "/opt/default-static"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = AppSettings()
