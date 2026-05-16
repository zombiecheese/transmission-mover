from datetime import datetime
from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator


DEFAULT_TRANSMISSION_RPC_PATH = "/transmission/rpc"
DEFAULT_TRANSMISSION_RPC_PORT = 9091
DEFAULT_TRANSMISSION_TLS_RPC_PORT = 443


def default_transmission_rpc_port(verify_tls: bool) -> int:
    return DEFAULT_TRANSMISSION_TLS_RPC_PORT if verify_tls else DEFAULT_TRANSMISSION_RPC_PORT


def normalize_transmission_rpc_port(port: int | str | None, verify_tls: bool) -> int:
    if port is None or str(port).strip() == "":
        return default_transmission_rpc_port(verify_tls)
    normalized = int(port)
    if normalized < 1 or normalized > 65535:
        raise ValueError("rpc_port must be between 1 and 65535")
    return normalized


def normalize_transmission_rpc_path(path: str | None) -> str:
    cleaned = (path or "").strip()
    if not cleaned:
        return DEFAULT_TRANSMISSION_RPC_PATH
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    return cleaned


def split_transmission_rpc_url(rpc_url: str, verify_tls: bool = True) -> tuple[str, int, str]:
    parsed = urlsplit((rpc_url or "").strip())
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("rpc_url must include scheme and host")
    domain = f"{parsed.scheme}://{parsed.hostname}"
    port = normalize_transmission_rpc_port(parsed.port, verify_tls)
    path = normalize_transmission_rpc_path(parsed.path)
    return domain, port, path


def build_transmission_rpc_url(rpc_domain: str, rpc_port: int | str | None, rpc_path: str | None, verify_tls: bool) -> str:
    parsed = urlsplit((rpc_domain or "").strip())
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("rpc_domain must include scheme and host")
    port = normalize_transmission_rpc_port(rpc_port if rpc_port is not None else parsed.port, verify_tls)
    domain = f"{parsed.scheme}://{parsed.hostname}:{port}"
    path = normalize_transmission_rpc_path(rpc_path)
    return f"{domain}{path}"


class TransmissionConfigIn(BaseModel):
    rpc_url: Optional[str] = None
    rpc_domain: Optional[str] = None
    rpc_port: Optional[int] = None
    rpc_path: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    verify_tls: bool = True

    @model_validator(mode="after")
    def validate_and_normalize_rpc(self) -> "TransmissionConfigIn":
        has_url = bool((self.rpc_url or "").strip())
        has_domain = bool((self.rpc_domain or "").strip())

        if not has_url and not has_domain:
            raise ValueError("Either rpc_url or rpc_domain is required")

        if has_domain:
            self.rpc_domain = (self.rpc_domain or "").strip()
            self.rpc_port = normalize_transmission_rpc_port(self.rpc_port, self.verify_tls)
            self.rpc_path = normalize_transmission_rpc_path(self.rpc_path)
            self.rpc_url = build_transmission_rpc_url(self.rpc_domain, self.rpc_port, self.rpc_path, self.verify_tls)
        else:
            domain, port, path = split_transmission_rpc_url(self.rpc_url or "", verify_tls=self.verify_tls)
            self.rpc_domain = domain
            self.rpc_port = normalize_transmission_rpc_port(self.rpc_port if self.rpc_port is not None else port, self.verify_tls)
            self.rpc_path = path
            self.rpc_url = build_transmission_rpc_url(domain, self.rpc_port, path, self.verify_tls)

        return self


class TransmissionConfigOut(BaseModel):
    rpc_url: str
    rpc_domain: str
    rpc_port: int = DEFAULT_TRANSMISSION_RPC_PORT
    rpc_path: str = DEFAULT_TRANSMISSION_RPC_PATH
    username: Optional[str] = None
    verify_tls: bool = True
    has_password: bool = False


class AppSettingsIn(BaseModel):
    transmission_in_container: bool = False
    transfer_mode: str = "move"
    transfer_schedule: str = "auto"
    transfer_interval_seconds: int = 300
    remove_torrent_on_complete: bool = True
    watch_source_kind: str = "local"  # local | ssh (remote ssh negotiation)
    watch_base_path: Optional[str] = None
    watch_host: Optional[str] = None
    watch_port: int = 22
    watch_username: Optional[str] = None
    watch_password: Optional[str] = None
    watch_private_key: Optional[str] = None
    watch_key_passphrase: Optional[str] = None
    # Auto-detected transfer methods (read-only, set by server)
    watch_detected_methods: str = ""
    watch_detected_preferred_method: Optional[str] = None
    watch_detected_sftp_port: Optional[int] = None
    watch_detected_scp_port: Optional[int] = None
    watch_detected_rsync_port: Optional[int] = None
    ignored_labels: str = ""
    remap_download_path: bool = False
    remap_source_prefix: Optional[str] = None
    remap_target_prefix: Optional[str] = None


class AppSettingsOut(AppSettingsIn):
    id: int


class AppSettingsSafeOut(BaseModel):
    id: int
    transmission_in_container: bool = False
    transfer_mode: str = "move"
    transfer_schedule: str = "auto"
    transfer_interval_seconds: int = 300
    remove_torrent_on_complete: bool = True
    watch_source_kind: str = "local"  # local | ssh (remote ssh negotiation)
    watch_base_path: Optional[str] = None
    watch_host: Optional[str] = None
    watch_port: int = 22
    watch_username: Optional[str] = None
    remap_download_path: bool = False
    remap_source_prefix: Optional[str] = None
    remap_target_prefix: Optional[str] = None
    has_watch_password: bool = False
    has_watch_private_key: bool = False
    # Auto-detected transfer methods for remote sources
    watch_detected_methods: str = ""
    watch_detected_preferred_method: Optional[str] = None


class TorrentOut(BaseModel):
    id: int
    name: str
    labels: list[str] = Field(default_factory=list)
    status: int
    percent_done: float


class TransmissionTorrentsOut(BaseModel):
    torrents: list[TorrentOut]
    labels: list[str]


class TorrentLabelAssignIn(TransmissionConfigIn):
    torrent_id: int
    label: str


class TorrentLabelRemoveIn(TransmissionConfigIn):
    torrent_id: int
    label: str


class DestinationIn(BaseModel):
    name: str
    kind: str
    base_path: str
    host: Optional[str] = None
    port: int = 22
    username: Optional[str] = None
    password: Optional[str] = None
    private_key: Optional[str] = None
    key_passphrase: Optional[str] = None
    transfer_method_preference: str = "auto"
    detected_methods: str = ""
    detected_preferred_method: Optional[str] = None
    detected_sftp_port: Optional[int] = None
    detected_scp_port: Optional[int] = None
    detected_rsync_port: Optional[int] = None


class DestinationOut(DestinationIn):
    id: int


class DestinationSafeOut(BaseModel):
    id: int
    name: str
    kind: str
    base_path: str
    host: Optional[str] = None
    port: int = 22
    username: Optional[str] = None
    transfer_method_preference: str = "auto"
    detected_methods: str = ""
    detected_preferred_method: Optional[str] = None
    detected_sftp_port: Optional[int] = None
    detected_scp_port: Optional[int] = None
    detected_rsync_port: Optional[int] = None
    has_password: bool = False
    has_private_key: bool = False


class LabelRuleIn(BaseModel):
    label: str
    destination_id: int
    enabled: bool = True
    transfer_mode: str = "move"
    transfer_schedule: str = "auto"
    transfer_interval_seconds: int = 300
    transfer_method_preference: str = "auto"  # auto | rsync | scp | sftp
    remove_from_client: bool = True
    trash_data_on_remove: bool = False


class LabelRuleOut(LabelRuleIn):
    id: int
    destination_name: Optional[str] = None


class MoveLogOut(BaseModel):
    id: int
    created_at: datetime
    torrent_id: Optional[int] = None
    torrent_name: str
    label: Optional[str] = None
    destination_name: Optional[str] = None
    status: str
    message: str


class HealthOut(BaseModel):
    status: str


class TransferProgressOut(BaseModel):
    torrent_id: Optional[int] = None
    torrent_name: str
    destination_name: Optional[str] = None
    mode: str
    method: Optional[str] = None
    transferred_bytes: int
    total_bytes: int
    speed_bytes_per_sec: float
    percent: float


class SftpTestIn(BaseModel):
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    private_key: Optional[str] = None
    key_passphrase: Optional[str] = None
    base_path: Optional[str] = None
    role: str = "destination"  # destination | source


class MessageOut(BaseModel):
    message: str


class TransmissionContainerModeIn(BaseModel):
    transmission_in_container: bool


class SetupAuthRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str
