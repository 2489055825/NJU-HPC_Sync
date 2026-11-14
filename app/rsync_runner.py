from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

import pexpect

from .models import RunResult, RunStatus
from .auth import AuthenticationCancelled
from .paths import StreamingRedactor, redact_text
from .runtime import process_identity, stop_session


# OpenSSH commonly prints `user@host's password:` on one line, while
# keyboard-interactive gateways may use `Verification code:` or `OTP:`.
PROMPT_PATTERN = r"(?im)(?:^|[\r\n])[^\r\n]{0,160}?(?:password|passphrase|verification code|otp)\s*[:：]\s*"
HOST_KEY_PATTERN = r"(?im)(?:^|[\r\n])Are you sure you want to continue connecting\s*\(yes/no(?:/\[fingerprint\])?\)\?\s*"


class RunnerCancelled(Exception):
    pass


@dataclass
class RunnerCallbacks:
    on_output: Callable[[str], None] | None = None
    on_status: Callable[[RunStatus], None] | None = None
    on_prompt: Callable[[str], str | None] | None = None
    on_host_key: Callable[[str], bool] | None = None
    on_started: Callable[[int], None] | None = None


class _LeaseSpawn(pexpect.spawn):
    """Keep a transfer lease held even if its owning worker dies."""

    def __init__(self, *args, lease_fd: int, **kwargs):
        self._lease_fd = lease_fd
        super().__init__(*args, **kwargs)

    def _spawnpty(self, args, **kwargs):
        from ptyprocess import PtyProcess
        return PtyProcess.spawn(args, pass_fds=(self._lease_fd,), **kwargs)


class RsyncRunner:
    """Run one rsync process under a PTY without invoking a shell."""

    def __init__(self, cancel_event: threading.Event | None = None) -> None:
        self._child: pexpect.spawn | None = None
        self._lock = threading.Lock()
        self._cancel_event = cancel_event if cancel_event is not None else threading.Event()
        # Signal handlers may interrupt this thread while it owns _lock or an
        # Event condition lock. They set this plain flag only; normal runner
        # control flow converts it into the full, lock-taking cancellation.
        self._signal_cancelled = False
        self._secrets: list[str] = []

    @property
    def pid(self) -> int | None:
        return self._child.pid if self._child is not None else None

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._lock:
            child = self._child
        if child is None or not child.isalive():
            return
        try:
            os.killpg(os.getpgid(child.pid), signal.SIGINT)
        except (OSError, ProcessLookupError):
            try:
                child.kill(signal.SIGINT)
            except Exception:
                pass

    def request_signal_cancel(self) -> None:
        """Publish a signal cancellation without acquiring any Python lock."""
        self._signal_cancelled = True

    def _cancel_requested(self) -> bool:
        return self._signal_cancelled or self._cancel_event.is_set()

    def run(self, command: list[str], callbacks: RunnerCallbacks | None = None, secrets: Iterable[str] = (), preflight: bool = True, *, lease_fd: int | None = None, authentication_timeout: float | None = None) -> RunResult:
        callbacks = callbacks or RunnerCallbacks()
        self._secrets = [secret for secret in secrets if secret]
        started = time.monotonic()
        start_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
        output_parts: list[str] = []
        redactor = StreamingRedactor(self._secrets)
        child_identity: dict = {}
        auth_started = started
        status, code = RunStatus.FAILED, None

        def emit_status(status: RunStatus) -> None:
            if callbacks.on_status and (not self._cancel_requested() or status is RunStatus.CANCELLED):
                callbacks.on_status(status)

        def check_cancelled() -> None:
            if self._cancel_requested():
                raise RunnerCancelled()

        def send_response(value: str) -> None:
            # Keep pexpect's usual echo-disable grace period cancellable.
            self._cancel_event.wait(0.05)
            if self._cancel_requested():
                raise RunnerCancelled()
            child.sendline(value)

        def emit_output(value: str, *, final: bool = False) -> None:
            safe = redactor.feed(value, final=final)
            if safe:
                output_parts.append(safe)
                if callbacks.on_output:
                    callbacks.on_output(safe)

        emit_status(RunStatus.CONNECTING)
        try:
            check_cancelled()
            options = dict(encoding="utf-8", codec_errors="replace", timeout=0.25)
            if lease_fd is None:
                child = pexpect.spawn(command[0], command[1:], **options)
            else:
                os.set_inheritable(lease_fd, True)
                child = _LeaseSpawn(command[0], command[1:], lease_fd=lease_fd, **options)
            child_identity = process_identity(child.pid)
            child.delaybeforesend = 0
            with self._lock:
                self._child = child
            if callbacks.on_started:
                callbacks.on_started(child.pid)
            check_cancelled()
            emitted_buffer = ""
            while True:
                check_cancelled()
                index = child.expect([HOST_KEY_PATTERN, PROMPT_PATTERN, pexpect.EOF, pexpect.TIMEOUT])
                before = child.before or ""
                if before:
                    if re_transfer_started(before):
                        authentication_timeout = None
                    # pexpect keeps an unmatched PTY buffer across TIMEOUTs.
                    # Emit only the part that was not sent on the previous read.
                    if before.startswith(emitted_buffer):
                        emit_output(before[len(emitted_buffer):])
                    else:
                        emit_output(before)
                    emitted_buffer = before
                if index in {0, 1}:
                    # A successful match consumes `before` and the prompt.
                    emitted_buffer = ""
                    check_cancelled()
                    if index == 0:
                        prompt = redact_text(before[-4096:] + (child.after or ""), self._secrets)
                        if callbacks.on_host_key is None or not callbacks.on_host_key(prompt):
                            raise RunnerCancelled()
                        send_response("yes")
                        emit_status(RunStatus.CONNECTING)
                        continue
                    prompt = child.after or "Password:"
                    emit_status(RunStatus.AUTHENTICATING)
                    if callbacks.on_prompt is None:
                        raise RuntimeError("检测到 SSH 认证提示，但没有提供认证回调")
                    password = callbacks.on_prompt(prompt)
                    if password is None:
                        raise RunnerCancelled()
                    check_cancelled()
                    self._secrets.append(password)
                    send_response(password)
                    auth_started = time.monotonic()
                    del password
                    emit_status(RunStatus.TRANSFERRING)
                    continue
                if index == 2:
                    break
                if not child.isalive():
                    break
                if authentication_timeout is not None and time.monotonic() - auth_started > authentication_timeout:
                    raise RuntimeError("SSH_AUTH_TIMEOUT: 未能完成非交互认证")
            child.close(force=False)
            code = child.exitstatus
            if code is None:
                code = child.signalstatus and 128 + child.signalstatus or 0
            status = RunStatus.CANCELLED if self._cancel_requested() else (RunStatus.SUCCESS if code == 0 else RunStatus.FAILED)
        except (RunnerCancelled, AuthenticationCancelled):
            self.cancel()
            if self._child is not None:
                try:
                    self._child.close(force=True)
                except Exception:
                    pass
            code = self._child.exitstatus if self._child is not None else None
            status = RunStatus.CANCELLED
        except pexpect.ExceptionPexpect as exc:
            emit_output(f"\nRunner error: {exc}\n")
            status, code = RunStatus.FAILED, None
        except Exception as exc:
            emit_output(f"\n{type(exc).__name__}: {exc}\n")
            status, code = RunStatus.FAILED, None
        finally:
            try:
                emit_output("", final=True)
                safe_output = redact_text("".join(output_parts), self._secrets)
            finally:
                if status is not RunStatus.SUCCESS or self._cancel_requested():
                    stop_session(child_identity)
                if self._child is not None and self._child.isalive():
                    self._child.close(force=True)
            if self._cancel_requested():
                status = RunStatus.CANCELLED
            emit_status(status)
            end_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._child = None
            self._secrets = []
        return RunResult(status, code, safe_output, start_time, end_time, time.monotonic() - started, list(command))


def re_transfer_started(output: str) -> bool:
    return any(value in output for value in ("sending incremental file list", "receiving incremental file list", "building file list"))


def friendly_exit_message(exit_code: int | None) -> str:
    messages = {
        0: "同步成功。",
        23: "部分文件传输失败（rsync exit code 23）。请查看详细日志。",
        24: "部分源文件在传输过程中消失（rsync exit code 24）。",
        255: "SSH 连接或认证失败（rsync exit code 255）。请检查 Host、网络和认证信息。",
    }
    return messages.get(exit_code, f"同步失败（rsync exit code {exit_code}）。")
