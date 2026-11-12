"""Rsync output interpretation shared by headless and graphical clients."""
from __future__ import annotations

import re


def stat_value(output: str, name: str) -> int | None:
    match = re.search(rf"(?im)^\s*{re.escape(name)}:\s*([\d,]+)\b", output)
    return int(match[1].replace(",", "")) if match else None


def itemized_change(line: str, *, include_metadata: bool = False) -> tuple[str, str] | None:
    line = line.rstrip()
    if line.startswith("*deleting"):
        name = line[len("*deleting"):].strip()
        return ("delete", name) if name else None
    if len(line) < 13 or line[11] != " ":
        return None
    code, name = line[:11], line[12:].strip()
    if include_metadata:
        if not name or code[0] not in "<>ch." or code[1] not in "fdLDS" or set(code[2:]) == {"."}:
            return None
        return ("add" if code[2] == "+" else "update", name)
    if not name or code[0] not in "<>" or code[1] == "d":
        return None
    return ("add" if code[2] == "+" else "update", name)


def changes(output: str, *, include_metadata: bool = False) -> list[tuple[str, str]]:
    return [change for line in output.splitlines()
            if (change := itemized_change(line, include_metadata=include_metadata))]


def sync_counts(output: str) -> tuple[int | None, int | None]:
    deleted = stat_value(output, "Number of deleted files")
    if deleted is None:
        deleted = sum(1 for line in output.splitlines() if line.strip().lower().startswith("deleting "))
    return stat_value(output, "Number of regular files transferred"), deleted


def parse_progress(line: str) -> dict | None:
    match = re.match(r"^\s*([\d,]+)\s+(\d{1,3})%\s+([\d.]+[kMGT]?B/s)\s+"
                     r"(?:\d+|--):(?:\d+|--):(?:\d+|--)", line, re.I)
    if not match:
        return None
    return {"transferred_bytes": int(match[1].replace(",", "")),
            "file_percent": int(match[2]), "speed": match[3]}


def summarize(output: str) -> dict:
    entries = changes(output, include_metadata=True)
    deleted = stat_value(output, "Number of deleted files")
    transferred = stat_value(output, "Number of regular files transferred")
    return {"summary": {"transferred_files": transferred,
                        "added_entries": sum(kind == "add" for kind, _ in entries) if transferred is not None else None,
                        "updated_entries": sum(kind == "update" for kind, _ in entries) if transferred is not None else None,
                        "deleted_entries": deleted,
                        "transferred_file_bytes": stat_value(output, "Total transferred file size"),
                        "total_file_bytes": stat_value(output, "Total file size")},
            "changes": [{"kind": kind, "path": path} for kind, path in entries[:100]],
            "changes_truncated": len(entries) > 100}
