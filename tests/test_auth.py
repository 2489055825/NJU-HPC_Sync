from app.auth import AutoAuthProvider
from app.models import Credential
from app.totp import TotpReplayGuard


class StubTotpSession:
    def next_password(self):
        return "dynamic-password"


def test_auto_auth_explains_additional_prompt_without_repeating_notice():
    notices = []
    provider = AutoAuthProvider(Credential("test", "static", "JBSWY3DPEHPK3PXP"), notices.append)
    provider.session = StubTotpSession()

    provider("Password:")
    provider("Password:")

    assert notices == ["正在生成当前验证码…", "检测到新的认证提示，正在生成新验证码…"]


def test_separate_auto_auth_providers_do_not_reuse_totp_window():
    clock_value = [100.0]
    sleeps = []

    def clock():
        return clock_value[0]

    def sleeper(value):
        sleeps.append(value)
        clock_value[0] = 120.0

    credential = Credential("test", "static", "JBSWY3DPEHPK3PXP")
    guard = TotpReplayGuard()
    preview = AutoAuthProvider(credential, replay_guard=guard)
    sync = AutoAuthProvider(credential, replay_guard=guard)
    for provider in (preview, sync):
        provider.session.clock = clock
        provider.session.sleeper = sleeper
        provider.session.safety_seconds = 0

    preview_password = preview("Password:")
    sync_password = sync("Password:")

    assert preview_password != sync_password
    assert preview.session.last_used_counter == 3
    assert sync.session.last_used_counter == 4
    assert sleeps
