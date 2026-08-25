from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional


class Direction(StrEnum):
    UPLOAD = "upload"
    DOWNLOAD = "download"

    @property
    def label(self) -> str:
        return "Local → NJU-HPC" if self is Direction.UPLOAD else "NJU-HPC → Local"


class SyncMode(StrEnum):
    NORMAL = "normal"
    MIRROR = "mirror"

    @property
    def label(self) -> str:
        return "普通同步" if self is SyncMode.NORMAL else "强制镜像"


class RunStatus(StrEnum):
    WAITING = "Waiting"
    CONNECTING = "Connecting"
    AUTHENTICATING = "Authenticating"
    PREVIEWING = "Previewing"
    TRANSFERRING = "Transferring"
    SUCCESS = "Success"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass
class Profile:
    name: str
    local_path: str
    remote_host: str
    remote_path: str
    credential_name: str = ""
    default_direction: str = Direction.UPLOAD.value
    default_mode: str = SyncMode.NORMAL.value
    id: Optional[int] = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def direction(self) -> Direction:
        return Direction(self.default_direction)

    @property
    def mode(self) -> SyncMode:
        return SyncMode(self.default_mode)


@dataclass
class SyncRequest:
    local_path: str
    remote_host: str
    remote_path: str
    direction: Direction = Direction.UPLOAD
    mode: SyncMode = SyncMode.NORMAL
    dry_run: bool = False


@dataclass
class RunResult:
    status: RunStatus
    exit_code: Optional[int]
    output: str
    start_time: str
    end_time: str
    duration: float
    command: list[str] = field(default_factory=list)


@dataclass
class HistoryRecord:
    profile_name: str
    local_path: str
    remote_host: str
    remote_path: str
    direction: str
    mode: str
    dry_run: bool
    status: str
    exit_code: Optional[int]
    duration: float
    log: str
    id: Optional[int] = None
    start_time: str = ""
    end_time: str = ""


@dataclass
class Credential:
    name: str
    static_password: str = field(default="", repr=False)
    totp_secret: str = field(default="", repr=False)
    totp_algorithm: str = "SHA1"
    totp_period: int = 30
    totp_digits: int = 6

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("凭据名称不能为空")
        if self.totp_algorithm.upper() not in {"SHA1", "SHA256", "SHA512"}:
            raise ValueError("TOTP 算法必须是 SHA1、SHA256 或 SHA512")
        if not 5 <= int(self.totp_period) <= 3600:
            raise ValueError("TOTP 周期必须在 5 到 3600 秒之间")
        if not 4 <= int(self.totp_digits) <= 10:
            raise ValueError("TOTP 位数必须在 4 到 10 位之间")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_local_time(value: str) -> str:
    if not value:
        return "未知"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone()
    offset = local.strftime("%z")
    timezone_label = f"UTC{offset[:3]}:{offset[3:]}" if offset else "本地时间"
    return f"{local:%Y-%m-%d %H:%M:%S} {timezone_label}"
