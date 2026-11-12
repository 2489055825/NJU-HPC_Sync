from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass

from .models import Direction, SyncMode, SyncRequest
from .paths import local_destination_path, local_source_path, normalize_local_path, validate_remote_host, validate_remote_path


@dataclass
class Preflight:
    ok: bool
    message: str = ""


def build_command(request: SyncRequest) -> list[str]:
    """Keep directory names literal through both SSH and remote rsync parsing."""
    host = validate_remote_host(request.remote_host)
    remote_path = validate_remote_path(request.remote_path)
    # Prefix relative paths before adding host:, so a leading colon cannot
    # turn an SSH directory into rsync's host::daemon-module syntax.
    directory = remote_path if remote_path.startswith("/") else "./" + remote_path
    if request.direction is Direction.UPLOAD:
        # A protected-args receiver also glob-expands the destination. Escaping
        # that glob is unsafe for a new directory: rsync keeps the escapes when
        # nothing matches. Give the remote shell one fully quoted literal path
        # instead, and disable rsync's second round of shell quoting explicitly.
        remote_path = shlex.quote(directory)
        argument_options = ["--old-args"]
    else:
        # Escaped protected source args can fall back to a different literal
        # directory if no glob matches. Enter the exact source first and send
        # only ./ through the protocol, retaining rsync's sender safety checks.
        # ./ also keeps relative names out of cd's options and CDPATH search.
        argument_options = ["--protect-args",
                            f"--rsync-path=CDPATH= cd -P {shlex.quote(directory)} && exec rsync"]
        remote_path = "./"
    # Normalize before encoding: appending a slash outside shell quotes would
    # change the encoded argument rather than the user's directory path.
    remote = f"{host}:{remote_path}"
    local = normalize_local_path(request.local_path)
    if request.direction is Direction.UPLOAD:
        source, destination = local_source_path(local), remote
    else:
        source, destination = remote, local_destination_path(local)

    args = ["rsync", "-avzP", "--stats", "--itemize-changes", *argument_options]
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
