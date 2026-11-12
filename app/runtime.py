"""Private task records and Linux process ownership, with no Qt dependency."""
from __future__ import annotations

import fcntl
import ctypes
import errno
import json
import os
import platform
import re
import signal
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import utc_now


class CliError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 2, **details: Any):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.details = details


class BusyError(CliError):
    def __init__(self, owner: dict):
        super().__init__("BUSY", "当前已有同步任务，未提交新任务。", 3, owner=owner)


def data_root() -> Path:
    return Path.home() / ".local/share/nju-hpc-sync"


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def atomic_json(path: Path, value: dict) -> None:
    private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        raise CliError("RECORD_INVALID", f"无法读取任务或运行记录：{path}", 8) from exc


def process_identity(pid: int) -> dict:
    if type(pid) is not int or pid <= 0:
        return {}
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return {}
        return {"pid": pid, "start_ticks": stat[19], "session": int(stat[3]),
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    except (OSError, ValueError, IndexError):
        return {}


def process_matches(identity: dict | None) -> bool:
    return bool(isinstance(identity, dict) and identity and process_identity(identity.get("pid", -1)) == identity)


def valid_identity(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if not value:
        return True
    return (type(value.get("pid")) is int and value["pid"] > 0
            and type(value.get("session")) is int and value["session"] > 0
            and isinstance(value.get("start_ticks"), str) and value["start_ticks"].isdigit()
            and isinstance(value.get("boot_id"), str) and bool(value["boot_id"]))


def session_processes(identity: dict | None) -> list[dict]:
    """Conservatively detect descendants of a recorded PTY session, even after its leader dies."""
    if not valid_identity(identity) or not identity:
        return []
    if identity.get("boot_id") != process_identity(os.getpid()).get("boot_id"):
        return []
    leader = process_identity(identity["pid"])
    if leader and leader != identity:
        return []  # Its PID now denotes an unrelated session; never signal it.
    members = []
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            current = process_identity(int(path.name))
            if current and current.get("session") == identity.get("session"):
                members.append(current)
    return members


def session_alive(identity: dict | None) -> bool:
    return bool(session_processes(identity))


def _pidfd_syscall(number: int, *args: int) -> int:
    # Some Conda Python builds omit the pidfd wrappers. These are the Linux
    # x86_64 UAPI numbers (Ubuntu amd64); do not guess on another architecture.
    if platform.machine() != "x86_64":
        raise OSError(errno.ENOSYS, "pidfd support is unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    value = libc.syscall(ctypes.c_long(number), *(ctypes.c_long(arg) for arg in args))
    if value == -1:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    return value


def _pidfd_open(pid: int) -> int:
    return os.pidfd_open(pid) if hasattr(os, "pidfd_open") else _pidfd_syscall(434, pid, 0)


def _pidfd_signal(fd: int, signum: int) -> None:
    if hasattr(signal, "pidfd_send_signal"):
        signal.pidfd_send_signal(fd, signum)
    else:
        _pidfd_syscall(424, fd, signum, 0, 0)


def stop_session(identity: dict | None) -> bool:
    """Bounded cleanup, including descendants after the PTY leader has exited.

    pidfds bind each signal to the verified process instance, not a reusable PID.
    If the kernel cannot provide that guarantee, leave recovery explicitly pending.
    """
    for signum, grace in ((signal.SIGTERM, .3), (signal.SIGKILL, 1.0)):
        for member in session_processes(identity):
            try:
                fd = _pidfd_open(member["pid"])
                try:
                    if process_matches(member):
                        _pidfd_signal(fd, signum)
                finally:
                    os.close(fd)
            except ProcessLookupError:
                pass
            except (OSError, AttributeError):
                return False
        deadline = time.monotonic() + grace
        while session_alive(identity) and time.monotonic() < deadline:
            time.sleep(.02)
        if not session_alive(identity):
            return True
    return False


def valid_task_record(record: dict, task_id: str) -> bool:
    try:
        timestamps = ("created_at", "updated_at", "start_time", "end_time")
        return (record.get("task_id") == task_id and record.get("schema_version") == 1
                and record["state"] in {"starting", "running", "cancelling", "succeeded", "failed", "cancelled", "interrupted"}
                and type(record["terminal"]) is bool and type(record["accepted"]) is bool
                and (record["transfer_success"] is None or type(record["transfer_success"]) is bool)
                and isinstance(record["request"], dict)
                and isinstance(record["profile_name"], str) and isinstance(record["credential_name"], str)
                and valid_identity(record["process"]) and valid_identity(record["child_process"])
                and type(record["created_ns"]) is int and record["created_ns"] >= 0
                and isinstance(record["warnings"], list) and all(isinstance(item, str) for item in record["warnings"])
                and (record["error"] is None or (isinstance(record["error"], dict)
                     and isinstance(record["error"]["code"], str) and isinstance(record["error"]["message"], str)))
                and "created_at" in record and "updated_at" in record
                and all(isinstance(record[field], str) and datetime.fromisoformat(record[field]).tzinfo is not None
                        for field in timestamps if field in record))
    except (KeyError, TypeError, ValueError):
        return False


class TransferLease:
    def __init__(self, fd: int, root: Path):
        self.fd = fd
        self.root = root

    @classmethod
    def held_owner(cls, root: Path | None = None) -> dict | None:
        """Read the owner only while the existing kernel lease is actually held.

        A dead submitting client can leave its open lock description with a
        worker that has not published its identity yet. Metadata alone cannot
        distinguish that handoff from a stale, already released lease.
        """
        root = root or data_root()
        try:
            fd = os.open(root / "transfer.lock", os.O_RDONLY)
        except FileNotFoundError:
            return None
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return read_json(root / "transfer-owner.json")
            return None
        finally:
            os.close(fd)

    @classmethod
    def acquire(cls, owner: dict, root: Path | None = None) -> "TransferLease":
        root = root or data_root()
        private_directory(root)
        fd = os.open(root / "transfer.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            path = root / "transfer-owner.json"
            raise BusyError(read_json(path) if path.exists() else {"owner": "starting"}) from None
        try:
            owner_path = root / "transfer-owner.json"
            previous = read_json(owner_path) if owner_path.exists() else {}
            if session_alive(previous.get("child_process")):
                raise CliError("RECOVERY_REQUIRED", "旧传输会话仍有残留进程。", 8, owner=previous)
            atomic_json(root / "transfer-owner.json", {**owner, "process": process_identity(os.getpid())})
        except BaseException:
            os.close(fd)
            raise
        return cls(fd, root)

    @classmethod
    def adopt(cls, fd: int, root: Path | None = None) -> "TransferLease":
        root = root or data_root()
        actual, expected = os.fstat(fd), (root / "transfer.lock").stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise CliError("LEASE_INVALID", "后台任务没有有效的运行锁。", 8)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return cls(fd, root)

    def set_owner(self, owner: dict) -> None:
        atomic_json(self.root / "transfer-owner.json", {**owner, "process": process_identity(os.getpid())})

    def close(self) -> None:
        if self.fd >= 0:
            # Do not LOCK_UN: another process may hold the inherited open description.
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "TransferLease":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class TaskStore:
    def __init__(self, root: Path | None = None):
        self.root = root or data_root()

    def task_dir(self, task_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise CliError("TASK_NOT_FOUND", "任务编号无效。")
        return self.root / "tasks" / task_id

    def create(self, request: dict, credential_name: str, profile_name: str) -> dict:
        task_id = uuid.uuid4().hex
        record = {"schema_version": 1, "task_id": task_id, "state": "starting",
                  "terminal": False, "accepted": False, "transfer_success": None,
                  "request": request, "credential_name": credential_name,
                  "profile_name": profile_name, "created_at": utc_now(), "updated_at": utc_now(),
                  "created_ns": time.time_ns(), "process": process_identity(os.getpid()),
                  "child_process": {}, "warnings": [], "error": None}
        atomic_json(self.task_dir(task_id) / "task.json", record)
        return record

    def read(self, task_id: str) -> dict:
        path = self.task_dir(task_id) / "task.json"
        if not path.exists():
            raise CliError("TASK_NOT_FOUND", "没有找到该任务。")
        record = read_json(path)
        if not valid_task_record(record, task_id):
            raise CliError("RECORD_INVALID", f"任务记录字段无效或不完整：{task_id}", 8) from None
        return record

    def update(self, task_id: str, **changes: Any) -> dict:
        record = self.read(task_id)
        record.update(changes, updated_at=utc_now())
        atomic_json(self.task_dir(task_id) / "task.json", record)
        return record

    def records(self) -> list[dict]:
        folder = self.root / "tasks"
        if not folder.exists():
            return []
        records = [self.read(path.name) for path in folder.iterdir() if path.is_dir()]
        return sorted(records, key=lambda item: (item["created_at"], item.get("created_ns", 0)), reverse=True)

    def resolve(self, task_id: str | None, *, active_only: bool = False) -> dict:
        if task_id is not None:
            return self.read(task_id)
        records = self.records()
        active = [item for item in records if not item["terminal"]]
        selected = active or ([] if active_only else records)
        if not selected:
            code = "NO_ACTIVE_TASK" if active_only else "TASK_NOT_FOUND"
            raise CliError(code, "没有当前任务。" if active_only else "没有任务记录。")
        return {**selected[0], "selected_by": "active" if active else "latest"}

    def request_cancel(self, task_id: str) -> None:
        self.read(task_id)
        atomic_json(self.task_dir(task_id) / "cancel.request", {"task_id": task_id})

    def cancel_requested(self, task_id: str) -> bool:
        return (self.task_dir(task_id) / "cancel.request").exists()
