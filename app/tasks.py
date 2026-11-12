"""Submit, observe and cancel independent per-task workers."""
from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .credential_store import CredentialStore
from .auth import validate_automatic_credential
from .database import Database
from .models import SyncRequest
from .paths import normalize_directory_path, normalize_local_path
from .rsync_command import preflight
from .runtime import (BusyError, CliError, TaskStore, TransferLease,
                      process_matches, session_alive, stop_session)


def _observe(record: dict) -> dict:
    if not record["terminal"] and not process_matches(record.get("process")):
        owner = TransferLease.held_owner()
        held = owner is not None and owner.get("task_id") == record["task_id"]
        if held and not record["accepted"] and not record.get("child_process"):
            # The inherited lease outlives the client during worker startup.
            # Preserve this live task so a cancellation request is not dropped.
            return record
        remaining = session_alive(record.get("child_process"))
        return {**record, "state": "interrupted", "terminal": not (remaining or held), "transfer_success": None,
                "cli_exit_code": 8, "error": {"code": "RECOVERY_REQUIRED",
                "message": "执行进程已退出，无法确认传输结果。请检查任务日志及残留进程。"},
                "requires_cleanup": remaining}
    return record


def get_task(task_id: str | None = None) -> dict:
    return _observe(TaskStore().resolve(task_id))


def _recover_and_cleanup(store: TaskStore) -> None:
    """Called only while owning the submission lock, never by query commands."""
    with Database() as db:
        days = db.get_log_retention_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for record in store.records():
        if session_alive(record.get("child_process")):
            raise CliError("RECOVERY_REQUIRED", "旧任务仍有传输进程，未提交新任务。", 8,
                           task_id=record["task_id"])
        if not record["terminal"]:
            if process_matches(record.get("process")):
                raise CliError("RECOVERY_REQUIRED", "旧任务状态与运行锁不一致。", 8, task_id=record["task_id"])
            store.update(record["task_id"], state="interrupted", terminal=True, transfer_success=None,
                         cli_exit_code=8, error={"code": "WORKER_INTERRUPTED", "message": "执行进程意外退出。"})
        elif datetime.fromisoformat(record.get("end_time") or record["created_at"]) < cutoff:
            shutil.rmtree(store.task_dir(record["task_id"]))


def _worker_command(task_id: str, lease_fd: int, ready_fd: int) -> list[str]:
    args = [task_id, str(lease_fd), str(ready_fd)]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--_worker", *args]
    return [sys.executable, "-m", "app.worker", *args]


def submit_task(request: SyncRequest, credential_name: str, profile_name: str = "") -> dict:
    request = SyncRequest(normalize_directory_path(str(Path(normalize_local_path(request.local_path)).absolute())),
                          request.remote_host, normalize_directory_path(request.remote_path),
                          request.direction, request.mode, request.dry_run)
    check = preflight(request)
    if not check.ok:
        raise CliError("PREFLIGHT_FAILED", check.message, 4)
    credential = CredentialStore().load().get(credential_name)
    if credential is None or not credential.static_password or not credential.totp_secret:
        raise CliError("CREDENTIAL_INVALID", "请选择已有且包含固定密码和 TOTP 密钥的凭据。")
    try:
        validate_automatic_credential(credential)
    except (ValueError, TypeError):
        raise CliError("CREDENTIAL_INVALID", "所选凭据的 TOTP 参数或密钥格式无效。") from None
    store = TaskStore()
    try:
        lease = TransferLease.acquire({"owner": "starting"})
    except BusyError as exc:
        owner = exc.details["owner"]
        if owner.get("process") and not process_matches(owner["process"]):
            raise CliError("RECOVERY_REQUIRED", "运行锁仍被旧传输占用，需检查残留进程。", 8, owner=owner) from exc
        raise
    read_fd = write_fd = None
    child = None
    record = None
    try:
        _recover_and_cleanup(store)
        record = store.create(asdict(request), credential_name, profile_name)
        task_id = record["task_id"]
        lease.set_owner({"owner": "cli", "task_id": task_id})
        read_fd, write_fd = os.pipe()
        child = subprocess.Popen(
            _worker_command(task_id, lease.fd, write_fd),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True, pass_fds=(lease.fd, write_fd),
            cwd=str(Path(__file__).resolve().parent.parent) if not getattr(sys, "frozen", False) else None,
        )
        # Reap normally in long-lived callers, without tying the worker lifetime to this thread.
        threading.Thread(target=child.wait, daemon=True).start()
        os.close(write_fd)
        write_fd = None
        if select.select([read_fd], [], [], 15)[0]:
            acknowledged = os.read(read_fd, 64).startswith(b"ready")
        else:
            acknowledged = False
        current = store.read(task_id)
        if not acknowledged and not current.get("accepted"):
            if current["terminal"]:
                error = current.get("error") or {}
                raise CliError(error.get("code", "START_FAILED"), error.get("message", "无法启动任务。"),
                               current.get("cli_exit_code", 8), task_id=task_id, task=current)
            raise CliError("START_UNCERTAIN", "后台任务启动未确认，请查询任务后再决定是否重试。", 8, task_id=task_id)
        return _observe(current)
    except Exception:
        if record is not None and child is None:
            store.update(record["task_id"], state="failed", terminal=True, accepted=False,
                         cli_exit_code=4, error={"code": "START_FAILED", "message": "无法创建执行进程。"})
        raise
    finally:
        for fd in (read_fd, write_fd):
            if fd is not None:
                os.close(fd)
        lease.close()


def wait_task(task_id: str | None = None, timeout: float | None = None,
              on_progress: Callable[[dict], None] | None = None) -> dict:
    selected = get_task(task_id)
    task_id = selected["task_id"]
    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        record = get_task(task_id)
        if record["terminal"] or record["state"] == "interrupted":
            return record
        if on_progress:
            on_progress(record)
        if deadline is not None and time.monotonic() >= deadline:
            raise CliError("WAIT_TIMEOUT", "等待超时，后台任务继续执行。", 124, task=record, task_id=task_id)
        time.sleep(.1)


def cancel_task(task_id: str | None = None, timeout: float = 10) -> dict:
    store = TaskStore()
    record = _observe(store.resolve(task_id, active_only=task_id is None))
    if record["state"] == "interrupted" and record.get("requires_cleanup"):
        stopped = stop_session(record.get("child_process"))
        owner = TransferLease.held_owner(store.root)
        held = owner is not None and owner.get("task_id") == record["task_id"]
        finished = stopped and not held
        return store.update(record["task_id"], state="interrupted", terminal=finished,
                            transfer_success=None, requires_cleanup=not stopped, cli_exit_code=8,
                            error={"code": "WORKER_INTERRUPTED" if finished else "RECOVERY_REQUIRED",
                                   "message": "残留传输已停止，原传输结果不确定。" if finished else
                                   "该任务的运行锁仍被占用，无法确认全部传输进程已经停止。" if held else
                                   "无法安全停止全部残留传输进程。"})
    if record["terminal"]:
        return {**record, "cancel_requested": False}
    task_id = record["task_id"]
    store.request_cancel(task_id)
    try:
        return {**wait_task(task_id, timeout), "cancel_requested": True}
    except CliError as exc:
        if exc.exit_code != 124:
            raise
        return {**get_task(task_id), "state": "cancelling", "cancel_requested": True}
