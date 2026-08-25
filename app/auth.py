from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .models import Credential
from .totp import TotpConfig, TotpReplayGuard, TotpSession


class AuthenticationCancelled(Exception):
    """Raised when the user closes or cancels a password prompt."""


class PasswordProvider(Protocol):
    def __call__(self, prompt: str) -> str | None: ...


@dataclass
class ManualAuthProvider:
    ask: PasswordProvider

    def __call__(self, prompt: str) -> str:
        password = self.ask(prompt)
        if password is None:
            raise AuthenticationCancelled("用户取消了认证")
        if not password:
            raise AuthenticationCancelled("未输入认证密码")
        return password


class AutoAuthProvider:
    def __init__(self, credential: Credential, wait_notice: Callable[[str], None] | None = None, replay_guard: TotpReplayGuard | None = None):
        if not credential.static_password:
            raise ValueError("自动认证需要固定密码")
        if not credential.totp_secret:
            raise ValueError("自动认证需要 TOTP Secret")
        self.credential = credential
        self.wait_notice = wait_notice or (lambda _message: None)
        self.session = TotpSession(
            credential.static_password,
            TotpConfig(credential.totp_secret, credential.totp_algorithm, credential.totp_period, credential.totp_digits),
            replay_guard=replay_guard,
        )
        self._prompt_count = 0

    def __call__(self, _prompt: str) -> str:
        # TotpSession blocks only at the instant SSH asks for a password, so the
        # code has the longest possible validity window and is never cached.
        self._prompt_count += 1
        notice = "正在生成当前验证码…" if self._prompt_count == 1 else "检测到新的认证提示，正在生成新验证码…"
        self.wait_notice(notice)
        return self.session.next_password()

    def sensitive_values(self) -> list[str]:
        return [self.credential.static_password, self.credential.totp_secret]


def make_provider(mode: str, credential: Credential | None, ask: PasswordProvider, wait_notice: Callable[[str], None] | None = None, replay_guard: TotpReplayGuard | None = None) -> PasswordProvider:
    if mode == "auto":
        if credential is None:
            raise ValueError("未找到所选凭据")
        return AutoAuthProvider(credential, wait_notice, replay_guard)
    return ManualAuthProvider(ask)
