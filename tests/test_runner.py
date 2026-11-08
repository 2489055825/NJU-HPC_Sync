import sys

import pytest

from app.models import RunStatus
from app.rsync_runner import RunnerCallbacks, RsyncRunner, friendly_exit_message


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


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [
        (0, "同步成功。"),
        (23, "部分文件传输失败（rsync exit code 23）。请查看详细日志。"),
        (24, "部分源文件在传输过程中消失（rsync exit code 24）。"),
        (255, "SSH 连接或认证失败（rsync exit code 255）。请检查 Host、网络和认证信息。"),
        (42, "同步失败（rsync exit code 42）。"),
    ],
)
def test_friendly_exit_message_covers_known_and_unknown_codes(exit_code, expected):
    assert friendly_exit_message(exit_code) == expected
