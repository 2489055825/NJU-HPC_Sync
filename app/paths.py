from __future__ import annotations

import os
import re
from pathlib import Path


def normalize_local_path(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("本地路径不能为空")
    path = Path(os.path.expanduser(value))
    return str(path)


def normalize_directory_path(value: str) -> str:
    """Return a directory path with exactly one trailing slash for rsync."""
    value = value.strip()
    if not value:
        raise ValueError("路径不能为空")
    if value == "/":
        return "/"
    return value.rstrip("/") + "/"


def validate_remote_host(value: str) -> str:
    host = value.strip()
    if not host:
        raise ValueError("远程 Host 不能为空")
    if any(ch.isspace() for ch in host) or ":" in host or "/" in host:
        raise ValueError("远程 Host 应为 SSH Host 别名或主机名")
    return host


def validate_remote_path(value: str) -> str:
    path = value.strip()
    if not path:
        raise ValueError("远程路径不能为空")
    if "\x00" in path:
        raise ValueError("路径包含非法字符")
    return normalize_directory_path(path)


def remote_spec(host: str, path: str) -> str:
    return f"{validate_remote_host(host)}:{validate_remote_path(path)}"


def local_source_path(path: str) -> str:
    return normalize_directory_path(normalize_local_path(path))


def local_destination_path(path: str) -> str:
    return normalize_directory_path(normalize_local_path(path))


def redact_text(text: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    result = text
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    # A prompt or command line should never reveal a complete dynamic password.
    result = re.sub(r"(?i)(password\s*[:=]\s*)([^\r\n]+)", r"\1[REDACTED]", result)
    return result
