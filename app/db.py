from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.settings import settings


def _ensure_sqlite_parent_dir(db_url: str) -> None:
    if not db_url.startswith("sqlite:///"):
        return

    path_str = db_url.replace("sqlite:///", "", 1)
    path = Path(path_str)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_parent_dir(settings.database_url)

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    _run_migrations()


def _run_migrations() -> None:
    """Apply any column additions that create_all won't handle on existing tables."""
    import sqlalchemy

    with engine.connect() as conn:
        for column_def in [
            ("appconfig", "ignored_labels", "TEXT DEFAULT ''"),
            ("appconfig", "remap_download_path", "INTEGER DEFAULT 0"),
            ("appconfig", "remap_source_prefix", "TEXT"),
            ("appconfig", "remap_target_prefix", "TEXT"),
            ("appconfig", "transmission_in_container", "INTEGER DEFAULT 0"),
            ("appconfig", "transfer_schedule", "TEXT DEFAULT 'auto'"),
            ("appconfig", "transfer_interval_seconds", "INTEGER DEFAULT 300"),
            ("labelrule", "transfer_mode", "TEXT DEFAULT 'move'"),
            ("labelrule", "transfer_schedule", "TEXT DEFAULT 'auto'"),
            ("labelrule", "transfer_interval_seconds", "INTEGER DEFAULT 300"),
            ("destination", "transfer_method_preference", "TEXT DEFAULT 'auto'"),
            ("destination", "detected_preferred_method", "TEXT"),
            ("destination", "detected_sftp_port", "INTEGER"),
            ("destination", "detected_scp_port", "INTEGER"),
            ("destination", "detected_rsync_port", "INTEGER"),
        ]:
            table, col, defn = column_def
            try:
                conn.execute(sqlalchemy.text(f"ALTER TABLE {table} ADD COLUMN {col} {defn}"))
                conn.commit()
            except Exception:
                # Column already exists or table not yet created; both are fine.
                pass


def get_session() -> Session:
    with Session(engine) as session:
        yield session
