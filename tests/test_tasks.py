import json
import os
import signal
import subprocess
import sys

import pytest

from tests.cli_helpers import configure_home, pump


def request(tmp_path):
    from app.models import SyncRequest
    return SyncRequest(str(tmp_path / "local"), "example", "/sample/remote")


def test_detached_task_finishes_after_submitting_client_is_killed(tmp_path, monkeypatch):
    from app.tasks import get_task, wait_task

    env = configure_home(tmp_path, monkeypatch, sleep=.5)
    script = ("import json; from app.tasks import submit_task,wait_task; from app.models import SyncRequest; "
              "r=submit_task(SyncRequest('" + str(tmp_path / "local") + "','example','/sample/remote'),'account','project'); "
              "print(json.dumps(r),flush=True); wait_task(r['task_id'])")
    client = subprocess.Popen([sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, text=True)
    record = json.loads(client.stdout.readline())
    client.kill()
    client.wait(timeout=3)
    client.stdout.close()
    final = wait_task(record["task_id"], timeout=5)
    assert final["state"] == "succeeded"
    assert get_task()["task_id"] == record["task_id"]
    assert len((tmp_path / "bin/invocations.jsonl").read_text().splitlines()) == 1


def test_busy_timeout_and_repeated_wait_do_not_submit_twice(tmp_path, monkeypatch):
    from app.runtime import CliError
    from app.tasks import submit_task, wait_task
    from app.database import Database

    configure_home(tmp_path, monkeypatch, sleep=.5)
    task = submit_task(request(tmp_path), "account", "project")
    try:
        with pytest.raises(CliError) as busy:
            submit_task(request(tmp_path), "account", "project")
        assert busy.value.code == "BUSY"
        assert busy.value.details["owner"]["task_id"] == task["task_id"]
        with pytest.raises(CliError) as timeout:
            wait_task(task["task_id"], timeout=.01)
        assert timeout.value.exit_code == 124
    finally:
        final = wait_task(task["task_id"], timeout=5)
    assert final["transfer_success"]
    assert wait_task(task["task_id"])["state"] == "succeeded"
    with Database() as db:
        assert len(db.list_history()) == 1


def test_cancel_is_task_bound_and_idempotent(tmp_path, monkeypatch):
    from app.tasks import cancel_task, submit_task

    configure_home(tmp_path, monkeypatch, sleep=20)
    task = submit_task(request(tmp_path), "account", "project")
    final = cancel_task(task["task_id"], timeout=5)
    assert final["state"] == "cancelled" and final["terminal"]
    assert cancel_task(task["task_id"])["state"] == "cancelled"


def test_worker_death_never_allows_second_transfer_while_child_survives(tmp_path, monkeypatch):
    from app.runtime import CliError, TaskStore, process_matches
    from app.tasks import get_task, submit_task

    configure_home(tmp_path, monkeypatch, sleep=20, ignore_signals=True)
    task = submit_task(request(tmp_path), "account", "project")
    record = pump(lambda: (r if (r := TaskStore().read(task["task_id"])).get("child_process") else None))
    log = TaskStore().task_dir(task["task_id"]) / "output.log"
    pump(lambda: "sending incremental file list" in log.read_text())
    os.kill(record["process"]["pid"], signal.SIGKILL)
    pump(lambda: not process_matches(record["process"]))
    try:
        result = get_task(task["task_id"])
        assert result["state"] == "interrupted" and result["transfer_success"] is None
        with pytest.raises(CliError) as blocked:
            submit_task(request(tmp_path), "account", "project")
        assert blocked.value.code == "RECOVERY_REQUIRED"
    finally:
        child = record["child_process"]
        if process_matches(child):
            os.killpg(child["pid"], signal.SIGKILL)


def test_unknown_task_never_initializes_runtime(tmp_path, monkeypatch):
    from app.runtime import CliError
    from app.tasks import get_task

    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(CliError):
        get_task()
    assert not (tmp_path / ".local").exists()


@pytest.mark.parametrize("same_task", [True, False])
def test_startup_observation_uses_only_the_matching_live_lease(tmp_path, monkeypatch, same_task):
    from app.runtime import TaskStore, TransferLease
    from app.tasks import get_task

    monkeypatch.setenv("HOME", str(tmp_path))
    store = TaskStore()
    task = store.create({}, "account", "")
    task_id = task["task_id"]
    store.update(task_id, process={})
    with TransferLease.acquire({"owner": "cli", "task_id": task_id if same_task else "another"}) as lease:
        before = {path: path.read_bytes() for path in store.root.rglob("*") if path.is_file()}
        observed = get_task(task_id)
        assert observed["terminal"] is not same_task
        assert observed["state"] == ("starting" if same_task else "interrupted")
        assert {path: path.read_bytes() for path in store.root.rglob("*") if path.is_file()} == before
        assert lease.fd >= 0
    assert get_task(task_id)["terminal"]


def test_unknown_child_with_held_lease_is_never_reported_safely_stopped(tmp_path, monkeypatch):
    from app.runtime import TaskStore, TransferLease
    from app.tasks import cancel_task, get_task

    monkeypatch.setenv("HOME", str(tmp_path))
    store = TaskStore()
    task = store.create({}, "account", "")
    task_id = task["task_id"]
    store.update(task_id, process={}, accepted=True, state="running")
    with TransferLease.acquire({"owner": "cli", "task_id": task_id}):
        observed = get_task(task_id)
        assert observed["state"] == "interrupted"
        assert not observed["terminal"]
        assert not observed["requires_cleanup"]
        cancelled = cancel_task(task_id, timeout=0)
        assert cancelled["cancel_requested"]
        assert not cancelled["terminal"]
        assert store.cancel_requested(task_id)


def test_cancel_of_known_session_retains_uncertainty_for_an_unknown_lease_holder(tmp_path, monkeypatch):
    from app.runtime import TaskStore, TransferLease, process_identity, process_matches
    from app.tasks import cancel_task, get_task

    monkeypatch.setenv("HOME", str(tmp_path))
    store = TaskStore()
    record = store.create({}, "account", "")
    task_id = record["task_id"]
    children = []
    with TransferLease.acquire({"owner": "cli", "task_id": task_id}) as lease:
        try:
            for _ in range(2):
                children.append(subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    pass_fds=(lease.fd,), start_new_session=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            identities = [process_identity(child.pid) for child in children]
            store.update(task_id, process={}, accepted=True, state="running", child_process=identities[0])
            lease.close()  # Only the two independent child sessions now own it.
            result = cancel_task(task_id, timeout=0)
            assert not process_matches(identities[0])
            assert process_matches(identities[1])
            assert TransferLease.held_owner()["task_id"] == task_id
            assert result["state"] == "interrupted" and not result["terminal"]
            assert result["error"]["code"] == "RECOVERY_REQUIRED"
            assert not result["requires_cleanup"]
            assert not get_task(task_id)["terminal"]
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=3)


def test_cancel_stops_descendant_even_after_pty_leader_exits(tmp_path, monkeypatch):
    from app.runtime import process_identity, process_matches
    from app.tasks import cancel_task, submit_task

    configure_home(tmp_path, monkeypatch)
    script = f'''#!{sys.executable}
import os,signal,time
from pathlib import Path
pid=os.fork()
if pid == 0:
    signal.signal(signal.SIGINT,signal.SIG_IGN)
    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    Path(__file__).with_name("descendant.pid").write_text(str(os.getpid()))
    time.sleep(30)
else:
    print("sending incremental file list",flush=True)
    time.sleep(30)
'''
    (tmp_path / "bin/rsync").write_text(script)
    task = submit_task(request(tmp_path), "account")
    marker = tmp_path / "bin/descendant.pid"
    pump(marker.exists)
    identity = process_identity(int(marker.read_text()))
    try:
        result = cancel_task(task["task_id"], timeout=5)
        assert not process_matches(identity), "transfer descendant survived cancellation"
        assert result["state"] == "cancelled" and result["terminal"]
    finally:
        if process_matches(identity):
            os.kill(identity["pid"], signal.SIGKILL)
