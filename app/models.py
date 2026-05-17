from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class TransmissionConfig(SQLModel, table=True):
    id: Optional[int] = Field(default=1, primary_key=True)
    rpc_url: str = Field(default="http://transmission:9091/transmission/rpc")
    username: Optional[str] = None
    password: Optional[str] = None
    verify_tls: bool = Field(default=True)


class AppConfig(SQLModel, table=True):
    id: Optional[int] = Field(default=1, primary_key=True)
    transmission_in_container: bool = Field(default=False)
    transfer_mode: str = Field(default="move")  # move | copy
    transfer_schedule: str = Field(default="auto")  # auto | interval | manual
    transfer_interval_seconds: int = Field(default=300)
    max_parallel_transfers: int = Field(default=1)
    remove_torrent_on_complete: bool = Field(default=True)
    watch_source_kind: str = Field(default="local")  # local | ssh
    watch_base_path: Optional[str] = None
    watch_host: Optional[str] = None
    watch_port: int = Field(default=22)
    watch_username: Optional[str] = None
    watch_password: Optional[str] = None
    watch_private_key: Optional[str] = None
    watch_key_passphrase: Optional[str] = None
    # Auto-detected transfer methods for watch source (remote only)
    watch_detected_methods: str = Field(default="")  # comma-separated list of available methods (sftp, scp, rsync)
    watch_detected_preferred_method: Optional[str] = None  # auto-detected best method
    watch_detected_sftp_port: Optional[int] = None
    watch_detected_scp_port: Optional[int] = None
    watch_detected_rsync_port: Optional[int] = None
    ignored_labels: str = Field(default="")  # comma-separated labels to hide from overview
    remap_download_path: bool = Field(default=False)  # remap Transmission's reported download dir
    remap_source_prefix: Optional[str] = None  # prefix Transmission reports, e.g. /downloads
    remap_target_prefix: Optional[str] = None  # prefix as seen inside this container, e.g. /watch


class Destination(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    kind: str = Field(default="local")  # local | remote (legacy: sftp)
    base_path: str
    host: Optional[str] = None
    port: int = Field(default=22)
    username: Optional[str] = None
    password: Optional[str] = None
    private_key: Optional[str] = None
    key_passphrase: Optional[str] = None
    transfer_method_preference: str = Field(default="auto")  # auto | rsync | scp | sftp
    detected_methods: str = Field(default="")  # comma-separated methods (rsync,scp,sftp)
    detected_preferred_method: Optional[str] = None
    detected_sftp_port: Optional[int] = None
    detected_scp_port: Optional[int] = None
    detected_rsync_port: Optional[int] = None


class LabelRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    label: str = Field(index=True, unique=True)
    destination_id: int = Field(foreign_key="destination.id")
    enabled: bool = Field(default=True)

    # Data handling options
    transfer_mode: str = Field(default="move")  # move | copy
    transfer_schedule: str = Field(default="auto")  # auto | interval | manual
    transfer_interval_seconds: int = Field(default=300)
    transfer_method_preference: str = Field(default="auto")  # auto | rsync | scp | sftp
    conflict_policy: str = Field(default="overwrite")  # overwrite | rename | skip
    parallelism_mode: str = Field(default="sequential")  # sequential | parallel
    remove_from_client: bool = Field(default=True)
    trash_data_on_remove: bool = Field(default=False)


class MoveLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    torrent_id: Optional[int] = Field(default=None, index=True)
    torrent_name: str
    label: Optional[str] = None
    destination_name: Optional[str] = None
    status: str  # success | skipped | error
    message: str


class WebAuth(SQLModel, table=True):
    """Stores hashed web UI credentials."""
    id: Optional[int] = Field(default=1, primary_key=True)
    username_hash: str  # bcrypt hash of username
    password_hash: str  # bcrypt hash of password
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AuthAuditLog(SQLModel, table=True):
    """Audit trail of authentication attempts."""
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    username: Optional[str] = None
    ip_address: Optional[str] = None
    success: bool
    message: str
