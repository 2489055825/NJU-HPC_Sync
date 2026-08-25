from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from .models import Direction, SyncMode, SyncRequest
from .paths import local_destination_path, local_source_path, normalize_local_path, remote_spec, validate_remote_host, validate_remote_path


@dataclass
class Preflight:
    ok: bool
    message: str = ""


def build_command(request: SyncRequest) -> list[str]:
    """Build an argv list; no shell is involved and paths remain one argument."""
    host = validate_remote_host(request.remote_host)
    remote = remote_spec(host, request.remote_path)
    local = normalize_local_path(request.local_path)
    if request.direction is Direction.UPLOAD:
        source, destination = local_source_path(local), remote
    else:
        source, destination = remote, local_destination_path(local)

    args = ["rsync", "-avzP", "--stats", "--itemize-changes"]
    if request.mode is SyncMode.MIRROR:
        args.append("--delete")
    if request.dry_run:
        args.append("--dry-run")
    # Stop option parsing before user-controlled paths (including paths that
    # begin with a dash). The two paths remain separate argv entries.
    args.extend(["--", source, destination])
    return args


def preflight(request: SyncRequest, command: list[str] | None = None) -> Preflight:
    if shutil.which("rsync") is None:
        return Preflight(False, "找不到 rsync，请先安装 rsync")
    if shutil.which("ssh") is None:
        return Preflight(False, "找不到 ssh，请先安装 OpenSSH client")
    try:
        local = normalize_local_path(request.local_path)
        validate_remote_host(request.remote_host)
        validate_remote_path(request.remote_path)
    except ValueError as exc:
        return Preflight(False, str(exc))
    if request.direction is Direction.UPLOAD and not os.path.isdir(local):
        return Preflight(False, f"本地源目录不存在：{local}")
    if request.direction is Direction.DOWNLOAD and not os.path.exists(local):
        # rsync can create a missing destination directory, so this is valid.
        pass
    return Preflight(True)
