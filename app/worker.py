"""One detached task executor. Internal entry point, never an interactive CLI."""
from __future__ import annotations

import os
import signal
import threading
import time

from .auth import AuthenticationCancelled, AutoAuthProvider, validate_automatic_credential
from .credential_store import CredentialStore
from .database import Database
from .models import Direction, HistoryRecord, RunStatus, SyncMode, SyncRequest, utc_now
from .rsync_command import build_command, preflight
from .rsync_runner import RsyncRunner, RunnerCallbacks
from .runtime import CliError, TaskStore, TransferLease, process_identity, session_alive
from .sync_service import parse_progress, summarize
from .totp import TotpReplayGuard


def _install_signal_handlers(runner: RsyncRunner) -> dict[int, object]:
    """Install signal handlers that only publish cancellation intent.

    Python may run a signal handler between any two bytecodes on the main
    thread. It must not call ``RsyncRunner.cancel()`` or ``Event.set()`` here:
    both can acquire locks, so a signal interrupting a critical section could
    make the handler wait on the interrupted thread. The normal runner loop
    observes the flag and performs process cleanup.
    """
    previous_handlers = {}
    if threading.current_thread() is threading.main_thread():
        def request_cancel(_signum, _frame):
            runner.request_signal_cancel()

        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, request_cancel)
    return previous_handlers


def _save_history(record: dict, request: SyncRequest, result) -> tuple[int | None, list[str]]:
    try:
        with Database() as db:
            history_id = db.add_history(HistoryRecord(
                record["profile_name"], request.local_path, request.remote_host, request.remote_path,
                request.direction.value, request.mode.value, request.dry_run, result.status.value,
                result.exit_code, result.duration, result.output,
                start_time=result.start_time, end_time=result.end_time,
            ))
            db.cleanup_history()
        return history_id, []
    except Exception:
        return None, ["HISTORY_WRITE_FAILED: 传输结果已单独保存，历史写入失败；不要因此重复传输。"]


def execute_task(task_id: str, lease_fd: int, ready_fd: int | None) -> int:
    store = TaskStore()
    lease = TransferLease.adopt(lease_fd)
    cancel_event = threading.Event()
    stop_monitor = threading.Event()
    runner = RsyncRunner(cancel_event)
    monitor = None
    previous_handlers = {}
    child_identity = {}
    accepted = False
    log = None
    try:
        record = store.read(task_id)
        # Publish worker ownership before preflight or credential IO. The
        # client's lifetime no longer determines whether this task is active.
        lease.set_owner({"owner": "cli", "task_id": task_id})
        store.update(task_id, process=process_identity(os.getpid()))

        def check_startup_cancelled():
            if store.cancel_requested(task_id):
                raise AuthenticationCancelled("启动期间已请求取消")

        check_startup_cancelled()
        value = record["request"]
        request = SyncRequest(value["local_path"], value["remote_host"], value["remote_path"],
                              Direction(value["direction"]), SyncMode(value["mode"]), value["dry_run"])
        check = preflight(request)
        check_startup_cancelled()
        if not check.ok:
            raise CliError("PREFLIGHT_FAILED", check.message, 4)
        credential = CredentialStore().load().get(record["credential_name"])
        if credential is None:
            raise CliError("CREDENTIAL_INVALID", "找不到选定的自动认证凭据。")
        try:
            validate_automatic_credential(credential)
        except (ValueError, TypeError):
            raise CliError("CREDENTIAL_INVALID", "自动认证凭据的格式无效。") from None
        provider = AutoAuthProvider(credential, replay_guard=TotpReplayGuard(), cancel_event=cancel_event,
                                    cancel_check=runner._cancel_requested)
        check_startup_cancelled()
        secrets = provider.sensitive_values()
        fd = os.open(store.task_dir(task_id) / "output.log", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        log = os.fdopen(fd, "w", encoding="utf-8", buffering=1)
        check_startup_cancelled()
        store.update(task_id, accepted=True, process=process_identity(os.getpid()),
                     state="running", phase="connecting", start_time=utc_now(),
                     direction=request.direction.value, mode=request.mode.value, dry_run=request.dry_run,
                     log_path=str(store.task_dir(task_id) / "output.log"))
        accepted = True
        if ready_fd is not None:
            try:
                os.write(ready_fd, b"ready\n")
            except BrokenPipeError:
                pass  # The client can disappear; the accepted task still runs.
            os.close(ready_fd)
            ready_fd = None

        def monitor_cancel():
            while not stop_monitor.is_set():
                if store.cancel_requested(task_id):
                    runner.cancel()
                    return
                stop_monitor.wait(.1)

        monitor = threading.Thread(target=monitor_cancel, daemon=True)
        monitor.start()
        previous_handlers = _install_signal_handlers(runner)

        last_progress = 0.0

        def on_output(text):
            nonlocal last_progress
            log.write(text)
            if time.monotonic() - last_progress >= .25:
                lines = text.replace("\r", "\n").splitlines()
                progress = next((value for line in reversed(lines) if (value := parse_progress(line))), None)
                if progress:
                    store.update(task_id, progress=progress)
                last_progress = time.monotonic()

        def on_status(status):
            if status not in {RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.CANCELLED}:
                store.update(task_id, phase=status.value.lower(),
                             state="cancelling" if cancel_event.is_set() else "running")

        def on_started(pid):
            nonlocal child_identity
            child_identity = process_identity(pid)
            store.update(task_id, child_process=child_identity)
            lease.set_owner({"owner": "cli", "task_id": task_id, "child_process": child_identity})

        auth_prompts = 0

        def on_prompt(prompt):
            nonlocal auth_prompts
            auth_prompts += 1
            if auth_prompts > 3:
                raise CliError("AUTH_FAILED", "SSH 认证失败，已停止重复尝试。", 5)
            return provider(prompt)

        def host_key(_prompt):
            raise CliError("HOST_KEY_REQUIRED", "需要先在终端核对 SSH 主机指纹，软件没有接受该指纹。", 5)

        if store.cancel_requested(task_id):
            runner.cancel()
        result = runner.run(build_command(request), RunnerCallbacks(
            on_output=on_output, on_status=on_status, on_prompt=on_prompt,
            on_host_key=host_key, on_started=on_started,
        ), secrets=secrets, preflight=False, lease_fd=lease.fd, authentication_timeout=120)
        log.flush()
        os.fsync(log.fileno())
        error = None
        exit_code = 0
        if result.status is RunStatus.CANCELLED:
            state, exit_code = "cancelled", 7
        elif result.status is RunStatus.SUCCESS:
            state = "succeeded"
        else:
            state, exit_code = "failed", 6
            code = "TRANSFER_FAILED"
            if "HOST_KEY_REQUIRED" in result.output or "需要先在终端核对 SSH 主机指纹" in result.output:
                code, exit_code = "HOST_KEY_REQUIRED", 5
            elif result.exit_code == 255 or any(text in result.output.lower() for text in (
                    "permission denied (", "host key verification failed", "remote host identification has changed",
                    "ssh_auth_timeout", "ssh 认证失败")):
                code, exit_code = "SSH_AUTH_FAILED", 5
            error = {"code": code, "message": "同步未完成，请查看任务日志。",
                     "next_action": "检查连接和认证；根据任务日志决定是否重新提交。"}
        remaining = session_alive(child_identity)
        if remaining:
            state, exit_code = "interrupted", 8
            error = {"code": "RECOVERY_REQUIRED", "message": "传输会话仍有残留进程，未释放任务所有权。"}
        history_id, warnings = _save_history(record, request, result)
        final = store.update(task_id, state=state, terminal=not remaining,
                             transfer_success=None if state == "interrupted" else state == "succeeded",
                             rsync_exit_code=result.exit_code, cli_exit_code=exit_code,
                             start_time=result.start_time, end_time=result.end_time, duration=result.duration,
                             history_id=history_id, history_saved=history_id is not None,
                             warnings=warnings, error=error, **summarize(result.output))
        return final["cli_exit_code"]
    except AuthenticationCancelled:
        store.update(task_id, state="cancelled", terminal=True, accepted=accepted,
                     transfer_success=False, cli_exit_code=7, rsync_exit_code=None, end_time=utc_now(),
                     error={"code": "CANCELLED", "message": "任务在启动期间已取消，未启动传输。"})
        return 7
    except Exception as exc:
        runner.cancel()
        code = exc.exit_code if isinstance(exc, CliError) else 8
        error_code = exc.code if isinstance(exc, CliError) else "WORKER_FAILED"
        # Do not include exception values: dependencies can embed private data.
        try:
            store.update(task_id, state="failed", terminal=True, accepted=accepted,
                         transfer_success=None, cli_exit_code=code, end_time=utc_now(),
                         error={"code": error_code, "message": "无法可靠完成后台任务，请检查本地配置、存储和任务日志。"})
        except Exception:
            pass  # Readers detect the dead worker as an uncertain interrupted task.
        return code
    finally:
        stop_monitor.set()
        if monitor is not None:
            monitor.join(1)
        if ready_fd is not None:
            os.close(ready_fd)
        if log is not None:
            log.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        lease.close()


if __name__ == "__main__":
    import sys
    raise SystemExit(execute_task(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))
