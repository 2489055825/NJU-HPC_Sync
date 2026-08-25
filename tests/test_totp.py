import time

from app.totp import TotpConfig, TotpReplayGuard, TotpSession, generate_code, remaining_seconds


def test_rfc6238_known_code():
    # Base32 for the RFC 6238 SHA1 test secret.
    config = TotpConfig("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "SHA1", 30, 8)
    assert generate_code(config, 59) == "94287082"


def test_remaining_seconds_is_at_least_one():
    assert remaining_seconds(30, 59.9) == 1


def test_session_records_counter_and_changes_after_window():
    clock_value = [100.0]
    sleeps = []

    def clock():
        return clock_value[0]

    def sleeper(value):
        sleeps.append(value)
        clock_value[0] = 120.0

    session = TotpSession("static", TotpConfig("JBSWY3DPEHPK3PXP"), safety_seconds=0, clock=clock, sleeper=sleeper)
    first = session.next_password()
    second = session.next_password()
    assert first != second
    assert session.last_used_counter == 4
    assert sleeps


def test_replay_guard_prevents_reuse_across_separate_sessions():
    clock_value = [100.0]
    sleeps = []

    def clock():
        return clock_value[0]

    def sleeper(value):
        sleeps.append(value)
        clock_value[0] = 120.0

    config = TotpConfig("JBSWY3DPEHPK3PXP")
    guard = TotpReplayGuard()
    preview = TotpSession("static", config, safety_seconds=0, clock=clock, sleeper=sleeper, replay_guard=guard)
    sync = TotpSession("static", config, safety_seconds=0, clock=clock, sleeper=sleeper, replay_guard=guard)

    preview_password = preview.next_password()
    sync_password = sync.next_password()

    assert preview_password != sync_password
    assert preview.last_used_counter == 3
    assert sync.last_used_counter == 4
    assert sleeps
