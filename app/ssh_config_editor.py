"""Safe read/validate/write helpers for the user's main SSH config file."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path

from .ssh_config import _split_config_values


_DIRECTIVE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)\s*(?:=\s*|\s+)(.*)$")
_UNCHECKED = object()


def ssh_config_path() -> Path:
    return Path.home() / ".ssh" / "config"


def load_ssh_config(path: Path | None = None) -> str:
    return load_ssh_config_state(path)[0]


def load_ssh_config_state(path: Path | None = None) -> tuple[str, str | None]:
    """Return text and a snapshot from the same read, preserving line endings."""
    config_path = path or ssh_config_path()
    try:
        data = config_path.read_bytes()
    except FileNotFoundError:
        return "", None
    return data.decode("utf-8"), hashlib.sha256(data).hexdigest()


def config_snapshot(path: Path | None = None) -> str | None:
    config_path = path or ssh_config_path()
    try:
        data = config_path.read_bytes()
    except FileNotFoundError:
        return None
    return hashlib.sha256(data).hexdigest()


def validate_ssh_config(text: str) -> list[str]:
    """Check basic OpenSSH directive/argument structure without executing it."""
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if "\x00" in line:
            errors.append(f"第 {number} 行：包含非法空字符。")
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _DIRECTIVE_RE.match(stripped)
        if match is None:
            errors.append(f"第 {number} 行：缺少 OpenSSH 指令和值。")
            continue
        try:
            values = _split_config_values(match.group(2))
        except ValueError:
            errors.append(f"第 {number} 行：引号没有闭合。")
            continue
        if not values:
            errors.append(f"第 {number} 行：指令缺少值。")
    return errors


def _write_temporary(path: Path, data: bytes, mode: int) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def save_ssh_config(
    text: str,
    path: Path | None = None,
    *,
    expected_snapshot: str | None | object = _UNCHECKED,
) -> Path | None:
    """Validate and atomically save text, returning the backup path if created."""
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("无法保存 SSH 配置：文本不是有效 UTF-8。") from exc
    errors = validate_ssh_config(text)
    if errors:
        raise ValueError("无法保存 SSH 配置：\n" + "\n".join(errors))

    config_path = path or ssh_config_path()
    if config_path.is_symlink():
        raise ValueError("主配置是符号链接，为保留链接及其目标，本编辑器不覆盖该文件。请使用外部编辑器。")
    if expected_snapshot is _UNCHECKED:
        expected_snapshot = config_snapshot(config_path)

    def check_unchanged() -> None:
        if config_path.is_symlink() or config_snapshot(config_path) != expected_snapshot:
            raise ValueError("SSH 配置文件已变化，请重新加载后再保存。")

    check_unchanged()
    original = config_path.read_bytes() if expected_snapshot is not None else None
    if original is not None and hashlib.sha256(original).hexdigest() != expected_snapshot:
        raise ValueError("SSH 配置文件已变化，请重新加载后再保存。")
    parent = config_path.parent
    parent_existed = parent.exists()
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not parent_existed:
        os.chmod(parent, 0o700)

    original_mode = stat.S_IMODE(config_path.stat().st_mode) if original is not None else 0o600
    backup_path: Path | None = None
    temporary = _write_temporary(config_path, data, original_mode)
    try:
        check_unchanged()
        if original is not None:
            backup_path = config_path.with_name(config_path.name + ".bak")
            backup_temporary = _write_temporary(backup_path, original, original_mode)
            try:
                os.replace(backup_temporary, backup_path)
            finally:
                backup_temporary.unlink(missing_ok=True)
        check_unchanged()
        os.replace(temporary, config_path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup_path
