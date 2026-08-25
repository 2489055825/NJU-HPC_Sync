import sys

from app.models import RunStatus
from app.rsync_runner import RunnerCallbacks, RsyncRunner


def test_runner_handles_password_prompt_without_shell():
    script = "import sys; print(\"user@host's password:\", flush=True); value=sys.stdin.readline().strip(); print('received', value, flush=True); sys.exit(0 if value == 'secret' else 2)"
    output = []
    prompts = []
    runner = RsyncRunner()
    result = runner.run([sys.executable, "-c", script], RunnerCallbacks(on_output=output.append, on_prompt=lambda prompt: prompts.append(prompt) or "secret"), preflight=False)
    assert result.status is RunStatus.SUCCESS
    assert prompts
    assert "received [REDACTED]" in result.output
    assert "secret" not in result.output


def test_runner_does_not_repeat_output_across_timeouts():
    script = "import time; print('sending incremental file list', flush=True); time.sleep(.35); print('./', flush=True); time.sleep(.35); print('unique-file.txt', flush=True)"
    result = RsyncRunner().run([sys.executable, "-c", script], preflight=False)

    assert result.status is RunStatus.SUCCESS
    assert result.output.count("sending incremental file list") == 1
    assert result.output.count("./") == 1
    assert result.output.count("unique-file.txt") == 1
