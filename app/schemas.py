from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TransmissionConfigIn(BaseModel):
    rpc_url: str
    username: Optional[str] = None
    password: Optional[str] = None
    verify_tls: bool = True


class TransmissionConfigOut(BaseModel):
    rpc_url: str
    username: Optional[str] = None
    verify_tls: bool = True
    has_password: bool = False


class AppSettingsIn(BaseModel):
    transmission_in_container: bool = False
    transfer_mode: str = "move"
    transfer_schedule: str = "auto"
    transfer_interval_seconds: int = 300
    remove_torrent_on_complete: bool = True
    watch_source_kind: str = "local"
    watch_base_path: Optional[str] = None
    watch_host: Optional[str] = None
    watch_port: int = 22
    watch_username: Optional[str] = None
    watch_password: Optional[str] = None
    watch_private_key: Optional[str] = None
    watch_key_passphrase: Optional[str] = None
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
    watch_source_kind: str = "local"
    watch_base_path: Optional[str] = None
    watch_host: Optional[str] = None
    watch_port: int = 22
    watch_username: Optional[str] = None
    ignored_labels: str = ""
    remap_download_path: bool = False
    remap_source_prefix: Optional[str] = None
    remap_target_prefix: Optional[str] = None
    has_watch_password: bool = False
    has_watch_private_key: bool = False


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


class SetupAuthRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str
