from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import os
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable
from pathlib import Path

from .runtime import atomic_json, data_root, private_directory, read_json

try:
    import pyotp
except ImportError:  # pragma: no cover - only used before dependencies are installed
    pyotp = None


_HASHES = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


@dataclass
class TotpConfig:
    secret: str
    algorithm: str = "SHA1"
    period: int = 30
    digits: int = 6

    def validate(self) -> None:
        if not self.secret:
            raise ValueError("TOTP Secret 不能为空")
        if self.algorithm.upper() not in _HASHES:
            raise ValueError("不支持的 TOTP 算法")
        if self.period <= 0 or self.digits <= 0:
            raise ValueError("TOTP 周期和位数必须为正数")


def generate_code(config: TotpConfig, for_time: float | None = None) -> str:
    config.validate()
    now = time.time() if for_time is None else for_time
    algorithm = config.algorithm.upper()
    if pyotp is not None:
        digest = getattr(hashlib, algorithm.lower())
        return pyotp.TOTP(config.secret, digits=config.digits, interval=config.period, digest=digest).at(now)
    try:
        key = base64.b32decode(config.secret.strip().replace(" ", "").upper() + "=" * ((8 - len(config.secret.strip().replace(" ", "")) % 8) % 8), casefold=True)
    except Exception as exc:
        raise ValueError("TOTP Secret 不是有效的 Base32 字符串") from exc
    counter = int(now // config.period)
    digest = hmac.new(key, struct.pack(">Q", counter), _HASHES[algorithm]).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10 ** config.digits)
    return str(number).zfill(config.digits)


def remaining_seconds(period: int = 30, now: float | None = None) -> int:
    current = time.time() if now is None else now
    remainder = period - (current % period)
    return max(1, int(remainder + 0.999999))


class TotpReplayGuard:
    """Prevent separate authentication sessions from reusing a TOTP window."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_used_counters: dict[tuple[bytes, str, int, int], int] = {}

    def claim(self, config: TotpConfig, counter: int) -> bool:
        key = _window_identity(config)
        with self._lock:
            if self._last_used_counters.get(key) == counter:
                return False
            self._last_used_counters[key] = counter
            return True


def _window_identity(config: TotpConfig) -> tuple[bytes, str, int, int]:
    text = config.secret.strip().replace(" ", "").upper()
    secret = base64.b32decode(text + "=" * (-len(text) % 8), casefold=True)
    return secret, config.algorithm.upper(), config.period, config.digits


class TotpCancelled(Exception):
    """Raised when a pending TOTP authentication is cancelled."""


class PersistentTotpReplayGuard:
    """Claim windows atomically across CLI workers and GUI processes."""

    def __init__(self, path: Path | None = None):
        self.path = path or data_root() / "totp-windows.json"

    def claim(self, config: TotpConfig, counter: int) -> bool:
        secret, algorithm, period, digits = _window_identity(config)
        key = hashlib.sha256(secret + f"\0{algorithm}:{period}:{digits}".encode()).hexdigest()
        private_directory(self.path.parent)
        fd = os.open(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            claims = read_json(self.path) if self.path.exists() else {}
            previous = claims.get(key, -1)
            if not isinstance(previous, int):
                raise ValueError("验证码时间窗口记录无效")
            if previous >= counter:
                return False
            claims[key] = counter
            atomic_json(self.path, claims)
            return True
        finally:
            os.close(fd)


class TotpSession:
    """Generate passwords without reusing a TOTP time window."""

    def __init__(self, static_password: str, config: TotpConfig, safety_seconds: int = 3, clock: Callable[[], float] = time.time, sleeper: Callable[[float], None] = time.sleep, replay_guard: TotpReplayGuard | None = None, cancel_event: threading.Event | None = None):
        self.static_password = static_password
        self.config = config
        self.safety_seconds = safety_seconds
        self.clock = clock
        self.sleeper = sleeper
        self.replay_guard = replay_guard
        self.cancel_event = cancel_event
        self.last_used_counter: int | None = None
        self.last_used_totp: str | None = None

    def next_password(self) -> str:
        while True:
            self._check_cancelled()
            now = self.clock()
            counter = int(now // self.config.period)
            remaining = self.config.period - (now % self.config.period)
            if remaining <= self.safety_seconds or counter == self.last_used_counter:
                wait_for = max(0.05, remaining + 0.05 if counter == self.last_used_counter else remaining + 0.05)
                self._wait(wait_for)
                continue
            code = generate_code(self.config, now)
            if self.replay_guard is not None and not self.replay_guard.claim(self.config, counter):
                self._wait(max(0.05, remaining + 0.05))
                continue
            self.last_used_counter = counter
            self.last_used_totp = code
            self._check_cancelled()
            return f"{self.static_password} {code}"

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise TotpCancelled()

    def _wait(self, delay: float) -> None:
        if self.cancel_event is None:
            self.sleeper(delay)
        elif self.cancel_event.wait(delay):
            raise TotpCancelled()

    def current(self) -> tuple[str, int]:
        now = self.clock()
        return generate_code(self.config, now), remaining_seconds(self.config.period, now)
