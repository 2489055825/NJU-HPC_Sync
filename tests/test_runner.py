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


def test_headless_authentication_timeout_does_not_wait_forever():
    script = "import time; print('unrecognized challenge:',flush=True); time.sleep(20)"
    result = RsyncRunner().run([sys.executable, "-c", script], authentication_timeout=.1)
    assert result.status is RunStatus.FAILED
    assert "SSH_AUTH_TIMEOUT" in result.output
    assert result.duration < 3


@pytest.mark.parametrize("banner_delay", [None, 0, .35])
def test_transfer_filenames_never_request_authentication(banner_delay):
    import textwrap
    script = textwrap.dedent(f"""
        import sys, time
        delay = {banner_delay!r}
        if delay is not None:
            print('sending incremental file list', flush=True)
            time.sleep(delay)
        for word in ('password', 'passphrase', 'verification code', 'otp'):
            print('>f+++++++++ 1-' + word + ':notes.txt', flush=True)
        print('*deleting   password:old.txt', flush=True)
        print('Number of regular files transferred: 4', flush=True)
    """)
    prompts = []
    result = RsyncRunner().run([sys.executable, "-c", script], RunnerCallbacks(
        on_prompt=lambda prompt: prompts.append(prompt)), authentication_timeout=2)
    assert result.status is RunStatus.SUCCESS
    assert prompts == []
    assert "Number of regular files transferred: 4" in result.output


def test_standard_encrypted_key_passphrase_prompt_and_password_retry():
    import textwrap
    script = textwrap.dedent("""
        import sys
        for prompt in ("Enter passphrase for key '/tmp/synthetic-key': ",
                       "user@host's password: ", "user@host's password: "):
            print(prompt, end='', flush=True)
            if sys.stdin.readline().strip() != 'synthetic-response':
                sys.exit(2)
        print('sending incremental file list', flush=True)
    """)
    prompts = []
    result = RsyncRunner().run([sys.executable, "-c", script], RunnerCallbacks(
        on_prompt=lambda prompt: prompts.append(prompt) or "synthetic-response"), authentication_timeout=.5)
    assert result.status is RunStatus.SUCCESS
    assert len(prompts) == 3
    assert "Enter passphrase for key" in prompts[0]


@pytest.mark.parametrize("keyword", ["password", "passphrase", "verification code", "otp"])
def test_path_diagnostics_are_never_authentication_prompts(keyword):
    diagnostic = f'rsync: [sender] change_dir "/missing/{keyword}:archive" failed: No such file or directory (2)'
    script = f"import sys; print({diagnostic!r},flush=True); sys.exit(23)"
    prompts = []
    result = RsyncRunner().run([sys.executable, "-c", script], RunnerCallbacks(
        on_prompt=lambda prompt: prompts.append(prompt)), authentication_timeout=1)
    assert prompts == []
    assert result.status is RunStatus.FAILED and result.exit_code == 23
    assert "No such file or directory" in result.output


@pytest.mark.parametrize("prompt", ["Password: ", "user@host's password: ",
                                     "(user@host) Password: ", "Verification code: ", "OTP: "])
def test_fragmented_standard_authentication_prompts_are_recognized(prompt):
    cut = len(prompt) // 2
    script = (f"import sys,time; print({prompt[:cut]!r},end='',flush=True); time.sleep(.3); "
              f"print({prompt[cut:]!r},end='',flush=True); "
              "sys.exit(0 if sys.stdin.readline().strip()=='synthetic-response' else 2)")
    prompts = []
    result = RsyncRunner().run([sys.executable, "-c", script], RunnerCallbacks(
        on_prompt=lambda value: prompts.append(value) or "synthetic-response"), authentication_timeout=1)
    assert result.status is RunStatus.SUCCESS
    assert len(prompts) == 1
