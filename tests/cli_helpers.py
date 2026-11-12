"""Synthetic rsync processes for CLI tests; never contact a real host."""
import json
import os
import sys
import time
from pathlib import Path


FAKE_RSYNC = '''import json,sys,time,signal
from pathlib import Path
root = Path(__file__).parent
config = json.loads((root / "behavior.json").read_text())
with (root / "invocations.jsonl").open("a") as out:
    out.write(json.dumps(sys.argv[1:]) + "\\n")
if config.get("ignore_signals"):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
if config.get("host_key"):
    print("Are you sure you want to continue connecting (yes/no)? ", end="", flush=True)
    sys.stdin.readline()
if config.get("authenticate"):
    print("Password:", flush=True)
    value = sys.stdin.readline().strip()
    if config.get("echo_password"):
        print("TRACE:" + value, flush=True)
    if not value or " " not in value:
        sys.exit(255)
print("sending incremental file list", flush=True)
time.sleep(config.get("sleep", 0))
print(">f+++++++++ sample.txt", flush=True)
print("Number of regular files transferred: 1", flush=True)
print("Number of deleted files: " + ("2" if "--delete" in sys.argv else "0"), flush=True)
print("Total transferred file size: 128 bytes", flush=True)
if config.get("error"):
    print("rsync: permission denied", flush=True)
sys.exit(config.get("exit_code", 0))
'''


def configure_home(home: Path, monkeypatch=None, **behavior):
    from app.credential_store import CredentialStore
    from app.database import Database
    from app.models import Credential, Profile

    home.mkdir(parents=True, exist_ok=True)
    binary = home / "bin"
    binary.mkdir(exist_ok=True)
    (binary / "rsync").write_text(f"#!{sys.executable}\n" + FAKE_RSYNC, encoding="utf-8")
    (binary / "rsync").chmod(0o755)
    (binary / "behavior.json").write_text(json.dumps(behavior), encoding="utf-8")
    ssh = home / ".ssh"
    ssh.mkdir(exist_ok=True)
    (ssh / "config").write_text("Host example\n    HostName example.invalid\n", encoding="utf-8")
    local = home / "local"
    local.mkdir(exist_ok=True)
    env = {**os.environ, "HOME": str(home), "PATH": str(binary) + os.pathsep + os.environ["PATH"],
           "PYTHONDONTWRITEBYTECODE": "1"}
    if monkeypatch is not None:
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("PATH", env["PATH"])
    CredentialStore(home / ".config/nju-hpc-sync/credentials.json").save({
        "account": Credential("account", "synthetic-fixed", "JBSWY3DPEHPK3PXP")
    })
    with Database(home / ".local/share/nju-hpc-sync/nju-hpc-sync.sqlite3") as db:
        db.save_profile(Profile("project", str(local), "example", "/sample/remote", "account", default_mode="mirror"))
    return env


def pump(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.03)
    raise AssertionError("Expected task state was not reached")
