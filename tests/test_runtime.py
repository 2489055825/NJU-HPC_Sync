import json
import os
import subprocess
import sys

import pytest


def test_task_records_are_private_and_cancellation_does_not_overwrite_state(tmp_path):
    from app.runtime import TaskStore

    store = TaskStore(tmp_path / "runtime")
    record = store.create({"direction": "upload"}, "account", "project")
    task_id = record["task_id"]
    store.update(task_id, state="running", terminal=False)
    store.request_cancel(task_id)
    assert store.read(task_id)["state"] == "running"
    assert store.cancel_requested(task_id)
    assert store.resolve(None)["task_id"] == task_id
    assert os.stat(store.task_dir(task_id)).st_mode & 0o777 == 0o700
    assert os.stat(store.task_dir(task_id) / "task.json").st_mode & 0o777 == 0o600
    assert "account" in json.dumps(store.read(task_id))


def test_invalid_task_ids_cannot_escape_store_and_corruption_is_reported(tmp_path):
    from app.runtime import CliError, TaskStore

    store = TaskStore(tmp_path)
    with pytest.raises(CliError):
        store.read("../credentials")
    record = store.create({}, "account", "")
    (store.task_dir(record["task_id"]) / "task.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(CliError, match="记录"):
        store.read(record["task_id"])


def test_kernel_lease_is_exclusive_and_owner_is_visible(tmp_path):
    from app.runtime import BusyError, TransferLease

    lease = TransferLease.acquire({"owner": "cli", "task_id": "first"}, tmp_path)
    try:
        with pytest.raises(BusyError) as error:
            TransferLease.acquire({"owner": "gui"}, tmp_path)
        assert error.value.details["owner"]["task_id"] == "first"
    finally:
        lease.close()
    TransferLease.acquire({"owner": "gui"}, tmp_path).close()


def test_closing_parent_lease_preserves_inherited_process_lock(tmp_path):
    from app.runtime import BusyError, TransferLease

    lease = TransferLease.acquire({"owner": "cli"}, tmp_path)
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE, pass_fds=(lease.fd,),
    )
    lease.close()
    try:
        with pytest.raises(BusyError):
            TransferLease.acquire({"owner": "cli"}, tmp_path)
    finally:
        child.communicate(timeout=3)
    TransferLease.acquire({"owner": "cli"}, tmp_path).close()


def test_process_identity_rejects_reused_pid():
    from app.runtime import process_identity, process_matches

    identity = process_identity(os.getpid())
    assert process_matches(identity)
    assert not process_matches({**identity, "start_ticks": "0"})


def test_persistent_totp_windows_survive_new_instances_and_hide_secret(tmp_path):
    from app.totp import PersistentTotpReplayGuard, TotpConfig

    config = TotpConfig("JBSWY3DPEHPK3PXP")
    path = tmp_path / "totp-windows.json"
    assert PersistentTotpReplayGuard(path).claim(config, 3)
    assert not PersistentTotpReplayGuard(path).claim(config, 3)
    assert not PersistentTotpReplayGuard(path).claim(config, 2)
    assert PersistentTotpReplayGuard(path).claim(config, 4)
    assert config.secret not in path.read_text(encoding="utf-8")
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_concurrent_processes_cannot_claim_same_totp_window(tmp_path):
    from tests.cli_helpers import configure_home

    env = configure_home(tmp_path)
    script = ("from app.credential_store import CredentialStore; "
              "from app.totp import PersistentTotpReplayGuard,TotpConfig; "
              "c=CredentialStore().load()['account']; "
              "print(PersistentTotpReplayGuard().claim(TotpConfig(c.totp_secret),3))")
    processes = [subprocess.Popen([sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, text=True)
                 for _ in range(2)]
    outcomes = [process.communicate(timeout=4)[0].strip() for process in processes]
    assert sorted(outcomes) == ["False", "True"]


def test_equivalent_base32_keys_cannot_reuse_a_window(tmp_path):
    from app.totp import PersistentTotpReplayGuard, TotpConfig, TotpReplayGuard, generate_code

    unpadded, padded = TotpConfig("MY"), TotpConfig("MY======")
    assert generate_code(unpadded, 100) == generate_code(padded, 100)
    for guard in (TotpReplayGuard(), PersistentTotpReplayGuard(tmp_path / "windows.json")):
        assert guard.claim(unpadded, 3)
        assert not guard.claim(padded, 3)
