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
from .paths import redact_text


# OpenSSH commonly prints `user@host's password:` on one line, while
# keyboard-interactive gateways may use `Verification code:` or `OTP:`.
PROMPT_PATTERN = r"(?im)(?:^|[\r\n])[^\r\n]{0,160}?(?:password|passphrase|verification code|otp)\s*[:：]\s*"


class RunnerCancelled(Exception):
    pass


@dataclass
class RunnerCallbacks:
    on_output: Callable[[str], None] | None = None
    on_status: Callable[[RunStatus], None] | None = None
    on_prompt: Callable[[str], str | None] | None = None


class RsyncRunner:
    """Run one rsync process under a PTY without invoking a shell."""

    def __init__(self) -> None:
        self._child: pexpect.spawn | None = None
        self._lock = threading.Lock()
        self._cancel_requested = False
        self._secrets: list[str] = []

    @property
    def pid(self) -> int | None:
        return self._child.pid if self._child is not None else None

    def cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True
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

    def run(self, command: list[str], callbacks: RunnerCallbacks | None = None, secrets: Iterable[str] = (), preflight: bool = True) -> RunResult:
        callbacks = callbacks or RunnerCallbacks()
        self._secrets = [secret for secret in secrets if secret]
        self._cancel_requested = False
        started = time.monotonic()
        start_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
        output_parts: list[str] = []

        def emit_status(status: RunStatus) -> None:
            if callbacks.on_status:
                callbacks.on_status(status)

        def emit_output(value: str) -> None:
            safe = redact_text(value, self._secrets)
            if safe:
                output_parts.append(safe)
                if callbacks.on_output:
                    callbacks.on_output(safe)

        emit_status(RunStatus.CONNECTING)
        try:
            self._child = pexpect.spawn(command[0], command[1:], encoding="utf-8", codec_errors="replace", timeout=0.25)
            child = self._child
            emitted_buffer = ""
            while True:
                with self._lock:
                    cancelled = self._cancel_requested
                if cancelled:
                    raise RunnerCancelled()
                index = child.expect([PROMPT_PATTERN, pexpect.EOF, pexpect.TIMEOUT])
                before = child.before or ""
                if before:
                    # pexpect keeps an unmatched PTY buffer across TIMEOUTs.
                    # Emit only the part that was not sent on the previous read.
                    if before.startswith(emitted_buffer):
                        emit_output(before[len(emitted_buffer):])
                    else:
                        emit_output(before)
                    emitted_buffer = before
                if index == 0:
                    # A successful match consumes `before` and the prompt.
                    emitted_buffer = ""
                    prompt = child.after or "Password:"
                    emit_status(RunStatus.AUTHENTICATING)
                    if callbacks.on_prompt is None:
                        raise RuntimeError("检测到 SSH 认证提示，但没有提供认证回调")
                    password = callbacks.on_prompt(prompt)
                    if password is None:
                        raise RunnerCancelled()
                    self._secrets.append(password)
                    child.sendline(password)
                    del password
                    emit_status(RunStatus.TRANSFERRING)
                    continue
                if index == 1:
                    break
                if not child.isalive():
                    break
            child.close(force=False)
            code = child.exitstatus
            if code is None:
                code = child.signalstatus and 128 + child.signalstatus or 0
            with self._lock:
                cancelled = self._cancel_requested
            status = RunStatus.CANCELLED if cancelled else (RunStatus.SUCCESS if code == 0 else RunStatus.FAILED)
        except RunnerCancelled:
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
            emit_status(status)
            end_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._child = None
            self._secrets = []
        return RunResult(status, code, "".join(output_parts), start_time, end_time, time.monotonic() - started, list(command))


def friendly_exit_message(exit_code: int | None) -> str:
    messages = {
        0: "同步成功。",
        23: "部分文件传输失败（rsync exit code 23）。请查看详细日志。",
        24: "部分源文件在传输过程中消失（rsync exit code 24）。",
        255: "SSH 连接或认证失败（rsync exit code 255）。请检查 Host、网络和认证信息。",
    }
    return messages.get(exit_code, f"同步失败（rsync exit code {exit_code}）。")
