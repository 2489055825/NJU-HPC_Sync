import json
import os

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
