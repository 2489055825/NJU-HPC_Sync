import json
import os
import subprocess
import sys

from app.credential_store import CredentialStore
from app.models import Credential


def test_default_credential_path_uses_nju_hpc_sync_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("app.credential_store.Path.home", lambda: tmp_path)
    store = CredentialStore()
    assert store.path == tmp_path / ".config" / "nju-hpc-sync" / "credentials.json"


def test_credentials_are_round_tripped_with_private_permissions(tmp_path):
    path = tmp_path / "config" / "credentials.json"
    store = CredentialStore(path)
    credentials = {"nju": Credential("nju", "static", "JBSWY3DPEHPK3PXP")}
    store.save(credentials)
    assert store.load()["nju"].totp_secret == "JBSWY3DPEHPK3PXP"
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    raw = json.loads(path.read_text())
    assert raw["nju"]["password"] == "static"


def test_simultaneous_process_saves_have_only_one_successful_writer(tmp_path):
    store = CredentialStore(tmp_path / "credentials.json")
    store.save({"original": Credential("original", "static", "JBSWY3DPEHPK3PXP")})
    script = """import sys
from app.credential_store import CredentialStore
from app.models import Credential
store = CredentialStore(sys.argv[1])
credentials, snapshot = store.load_state()
credentials[sys.argv[2]] = Credential(sys.argv[2], 'synthetic-fixed', 'JBSWY3DPEHPK3PXP')
print('ready', flush=True)
sys.stdin.readline()
try:
    store.save(credentials, expected_snapshot=snapshot)
except RuntimeError:
    print('conflict', flush=True)
else:
    print('saved', flush=True)
"""
    children = [subprocess.Popen([sys.executable, "-c", script, str(store.path), name],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for name in ("first", "second")]
    try:
        for child in children:
            assert child.stdout.readline().strip() == "ready"
        for child in children:
            child.stdin.write("save\n")
            child.stdin.flush()
        outcomes = [child.communicate(timeout=5) for child in children]
        assert sorted(output.strip() for output, _error in outcomes) == ["conflict", "saved"]
        assert all(child.returncode == 0 for child in children)
        assert "original" in store.load() and len(store.load()) == 2
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=3)
