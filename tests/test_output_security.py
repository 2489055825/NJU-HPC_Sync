import secrets
import sys

from app.models import RunStatus
from app.rsync_runner import RunnerCallbacks, RsyncRunner


def test_stream_redacts_arbitrary_chunks_and_final_fragment():
    from app.paths import StreamingRedactor

    token = secrets.token_hex(16)
    redactor = StreamingRedactor([token])
    outputs = [redactor.feed(char) for char in "TRACE:" + token + "\nnormal\rfinal"]
    outputs.append(redactor.flush())
    safe = token not in "".join(outputs)
    assert safe, "A sensitive value escaped stream redaction"
    assert "".join(outputs) == "TRACE:[REDACTED]\nnormal\rfinal"


def test_stream_redacts_password_label_split_across_chunks():
    from app.paths import StreamingRedactor

    redactor = StreamingRedactor([])
    output = redactor.feed("Pass") + redactor.feed("word: private") + redactor.flush()
    assert output == "Password: [REDACTED]"


def test_stream_tracks_new_secrets_and_preserves_progress_delimiters():
    from app.paths import StreamingRedactor

    known = []
    redactor = StreamingRedactor(known)
    output = redactor.feed("status\r")
    token = secrets.token_hex(16)
    known.append(token)
    output += redactor.feed(token[:10])
    output += redactor.feed(token[10:] + "\r")
    output += redactor.flush()
    safe = token not in output
    assert safe
    assert output == "status\r[REDACTED]\r"


def test_overlapping_static_and_full_credentials_do_not_expose_suffix():
    from app.paths import StreamingRedactor

    prefix = secrets.token_hex(8)
    suffix = secrets.token_hex(4)
    redactor = StreamingRedactor([prefix, prefix + suffix])
    output = redactor.feed("TRACE:" + prefix) + redactor.feed(suffix + "\n") + redactor.flush()
    safe = prefix not in output and suffix not in output
    assert safe, "A longer credential suffix escaped redaction at a shorter credential boundary"
    assert output == "TRACE:[REDACTED]\n"


def test_overlapping_full_credential_prefix_is_hidden_at_eof():
    from app.paths import StreamingRedactor

    prefix = secrets.token_hex(8)
    suffix = secrets.token_hex(4)
    redactor = StreamingRedactor([prefix, prefix + " " + suffix])
    output = redactor.feed("TRACE:" + prefix + " " + suffix[:2]) + redactor.flush()
    assert output == "TRACE:[REDACTED]"


def test_real_subprocess_overlapping_credentials_hide_verification_suffix():
    prefix = secrets.token_hex(8)
    suffix = f"{secrets.randbelow(1000000):06d}"
    token = prefix + " " + suffix
    script = "import sys,time; print('Password:',flush=True); v=sys.stdin.readline().strip(); cut=v.index(' '); print('TRACE:'+v[:cut],end='',flush=True); time.sleep(.4); print(v[cut:],end='',flush=True)"
    chunks = []
    result = RsyncRunner().run([sys.executable, "-c", script], RunnerCallbacks(on_prompt=lambda prompt: token, on_output=chunks.append), secrets=[prefix], preflight=False)
    safe = token not in result.output and suffix not in result.output and suffix not in "".join(chunks)
    assert safe, "A verification suffix escaped overlapping credential redaction"


def test_real_subprocess_fragmented_echo_never_reaches_live_or_final_output():
    token = secrets.token_hex(16)
    script = "import sys,time; print('Password:',flush=True); v=sys.stdin.readline().strip(); print('TRACE:'+v[:8],end='',flush=True); time.sleep(.4); print(v[8:16],end='',flush=True); time.sleep(.4); print(v[16:],end='',flush=True)"
    chunks = []
    result = RsyncRunner().run([sys.executable, "-c", script], RunnerCallbacks(on_prompt=lambda prompt: token, on_output=chunks.append), preflight=False)
    successful = result.status is RunStatus.SUCCESS
    safe = token not in result.output and token not in "".join(chunks)
    assert successful
    assert safe, "A sensitive value escaped live/final redaction"


def test_host_key_confirmation_is_separate_from_authentication():
    prompts = []
    host_prompts = []
    token = secrets.token_hex(16)
    script = "import sys,select; print('The authenticity of host example.invalid cannot be established.\\nED25519 key fingerprint is SHA256:example-fingerprint.\\nAre you sure you want to continue connecting (yes/no/[fingerprint])? ',end='',flush=True); r,_,_=select.select([sys.stdin],[],[],2); answer=sys.stdin.readline().strip() if r else ''; sys.exit(3) if answer!='yes' else None; print('Password:',flush=True); sys.stdin.readline(); print('connected',flush=True)"
    callbacks = RunnerCallbacks(on_prompt=lambda prompt: prompts.append(prompt) or token, on_host_key=lambda prompt: host_prompts.append(prompt) or True)

    result = RsyncRunner().run([sys.executable, "-c", script], callbacks, preflight=False)

    assert result.status is RunStatus.SUCCESS
    assert len(host_prompts) == 1 and "SHA256:example-fingerprint" in host_prompts[0]
    assert len(prompts) == 1 and "Password:" in prompts[0]


def test_host_key_rejection_cancels_without_authentication():
    prompts = []
    script = "import sys,select; print('Are you sure you want to continue connecting (yes/no)? ',end='',flush=True); select.select([sys.stdin],[],[],2)"
    callbacks = RunnerCallbacks(on_prompt=lambda prompt: prompts.append(prompt) or "unused", on_host_key=lambda prompt: False)

    result = RsyncRunner().run([sys.executable, "-c", script], callbacks, preflight=False)

    assert result.status is RunStatus.CANCELLED
    assert prompts == []
