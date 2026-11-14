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


def test_in_memory_totp_guard_normalizes_equivalent_base32_keys():
    from app.totp import TotpConfig, TotpReplayGuard, generate_code

    unpadded, padded = TotpConfig("MY"), TotpConfig("MY======")
    assert generate_code(unpadded, 100) == generate_code(padded, 100)
    guard = TotpReplayGuard()
    assert guard.claim(unpadded, 3)
    assert not guard.claim(padded, 3)
