import secrets
import sys
import threading
import time

from app.models import RunStatus
from app.rsync_runner import RsyncRunner, RunnerCallbacks
from app.totp import TotpConfig, TotpReplayGuard, TotpSession


def test_cancel_before_run_never_starts_subprocess(tmp_path):
    marker = tmp_path / "must-not-exist"
    runner = RsyncRunner()
    runner.cancel()

    result = runner.run([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)], preflight=False)

    assert result.status is RunStatus.CANCELLED
    assert not marker.exists()


def test_totp_wait_is_interrupted_by_cancellation():
    from app.totp import TotpCancelled

    event = threading.Event()
    guard = TotpReplayGuard()
    config = TotpConfig("JBSWY3DPEHPK3PXP")
    guard.claim(config, 3)
    session = TotpSession(secrets.token_hex(8), config, clock=lambda: 100.0, replay_guard=guard, cancel_event=event)
    outcomes = []
    started = threading.Event()

    def generate():
        started.set()
        try:
            session.next_password()
        except TotpCancelled:
            outcomes.append("cancelled")

    thread = threading.Thread(target=generate, daemon=True)
    thread.start()
    assert started.wait(1)
    event.set()
    thread.join(1)
    assert not thread.is_alive()
    assert outcomes == ["cancelled"]


def test_cancel_during_authentication_never_sends_response():
    runner = RsyncRunner()
    statuses = []
    token = secrets.token_hex(16)

    def answer(_prompt):
        runner.cancel()
        return token

    script = "import sys; print('Password:',flush=True); sys.stdin.readline(); print('response-received',flush=True)"
    result = runner.run([sys.executable, "-c", script], RunnerCallbacks(on_prompt=answer, on_status=statuses.append), preflight=False)

    assert result.status is RunStatus.CANCELLED
    assert RunStatus.TRANSFERRING not in statuses
    assert "response-received" not in result.output


def test_cancel_during_host_key_confirmation_never_accepts():
    runner = RsyncRunner()
    statuses = []

    def answer(_prompt):
        runner.cancel()
        return True

    script = "import sys; print('Are you sure you want to continue connecting (yes/no)? ',end='',flush=True); sys.stdin.readline(); print('host-accepted',flush=True)"
    result = runner.run([sys.executable, "-c", script], RunnerCallbacks(on_host_key=answer, on_status=statuses.append), preflight=False)

    assert result.status is RunStatus.CANCELLED
    assert statuses.count(RunStatus.CONNECTING) == 1
    assert "host-accepted" not in result.output


def test_cancelled_prompt_broker_never_opens_prompt():
    from app.main_window import PromptBroker

    event = threading.Event()
    event.set()

    class Prompt:
        def emit(self, value):
            raise AssertionError("Cancelled task requested a new prompt")

    broker = PromptBroker(Prompt(), event)
    assert broker.request("Password:") is None
