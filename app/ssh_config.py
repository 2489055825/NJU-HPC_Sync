"""Read explicit SSH aliases without evaluating commands or connection options."""
from __future__ import annotations

import glob
import re
from pathlib import Path

from .paths import validate_remote_host


def _split_config_values(text: str) -> list[str]:
    """Follow OpenSSH argv_split rules for comments, quotes and basic escapes."""
    values: list[str] = []
    index = 0
    while index < len(text):
        if text[index] in " \t":
            index += 1
            continue
        if text[index] == "#":
            break
        token: list[str] = []
        quote = ""
        while index < len(text):
            char = text[index]
            following = text[index + 1:index + 2]
            if char == "\\" and following and (following in "\\\"'" or (not quote and following == " ")):
                token.append(following)
                index += 2
                continue
            if not quote and char in " \t":
                break
            if not quote and char in "\"'":
                quote = char
            elif quote and char == quote:
                quote = ""
            else:
                token.append(char)
            index += 1
        if quote:
            raise ValueError("Unclosed SSH configuration quote")
        values.append("".join(token))
    return values


def read_ssh_hosts(config_path: Path | None = None) -> tuple[list[str], list[str]]:
    """Return aliases in declaration order and sanitized configuration warnings.

    This is static discovery, including files in conditional blocks. OpenSSH
    remains responsible for evaluating those conditions when connecting.
    """
    ssh_dir = Path.home() / ".ssh"
    hosts: dict[str, None] = {}
    warnings: list[str] = []
    visited: set[Path] = set()

    def read_file(path: Path, depth: int = 0) -> None:
        if depth > 32:
            warnings.append(f"SSH 配置 Include 层级过深：{path}")
            return
        try:
            resolved = path.resolve()
            if resolved in visited:
                return
            visited.add(resolved)
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, RuntimeError):
            warnings.append(f"无法读取 SSH 配置：{path}，请检查文件是否存在、读取权限及 UTF-8 编码。")
            return

        for number, line in enumerate(content.splitlines(), 1):
            directive = re.match(r"^\s*(Host|Include)(?:\s*=\s*|\s+)(.*)$", line, re.IGNORECASE)
            if directive is None:
                continue
            try:
                values = _split_config_values(directive[2])
            except ValueError:
                warnings.append(f"SSH 配置格式有误：{path} 第 {number} 行，请检查引号。")
                continue
            if directive[1].casefold() == "host":
                for value in values:
                    if value != value.strip() or value.startswith(("!", "-")) or any(char in value for char in "*?\\\x00"):
                        continue
                    try:
                        host = validate_remote_host(value)
                    except ValueError:
                        continue
                    hosts[host] = None
            else:
                for value in values:
                    try:
                        pattern = Path(value).expanduser()
                    except RuntimeError:
                        warnings.append(f"无法展开 SSH Include 的用户目录：{path} 第 {number} 行。")
                        continue
                    if not pattern.is_absolute():
                        pattern = ssh_dir / pattern
                    for included in sorted(glob.glob(str(pattern))):
                        read_file(Path(included), depth + 1)

    read_file(config_path if config_path is not None else ssh_dir / "config")
    return list(hosts), warnings
