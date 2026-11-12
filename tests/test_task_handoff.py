"""Gate real worker processes at the two sides of startup ownership handoff."""
import ctypes
import json
import os
import subprocess
import sys

import pytest

from tests.cli_helpers import configure_home, pump


WRAPPER = '''import json,os,sys,time
from pathlib import Path
from app.runtime import process_identity
home = Path.home()
stage, executable, task_id, lease_fd, ready_fd = sys.argv[1:]
def gated():
    (home / 'gated-worker.json').write_text(json.dumps(process_identity(os.getpid())))
    while not (home / 'release-worker').exists():
        time.sleep(.02)
if stage == 'before_entry':
    gated()
    if executable:
        os.execv(executable, [executable, '--_worker', task_id, lease_fd, ready_fd])
from app import worker
if stage == 'preflight':
    original = worker.preflight
    def preflight(*args, **kwargs):
        gated()
        return original(*args, **kwargs)
    worker.preflight = preflight
raise SystemExit(worker.execute_task(task_id, int(lease_fd), int(ready_fd)))
'''


@pytest.mark.parametrize("stage,packaged", [("before_entry", False), ("preflight", False),
                                            ("before_entry", True)])
@pytest.mark.parametrize("cancel", [True, False], ids=["cancel", "finish"])
def test_client_exit_during_startup_preserves_worker_and_cancellation(tmp_path, monkeypatch,
                                                                     stage, packaged, cancel):
    from app.runtime import TaskStore, process_matches, stop_session
    from app.tasks import cancel_task, get_task, wait_task

    executable = os.environ.get("NJU_HPC_SYNC_TEST_EXECUTABLE", "") if packaged else ""
    if packaged and not executable:
        pytest.skip("No packaged executable specified")
    env = configure_home(tmp_path, monkeypatch)
    # Adopt and reap this test's worker when its submitting parent disappears.
    # Restore the prior subreaper setting, and never reap unrelated children.
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    client_code = f'''import sys
from pathlib import Path
from app import tasks
from app.models import SyncRequest
tasks._worker_command = lambda task_id, lease_fd, ready_fd: [
    sys.executable, '-c', {WRAPPER!r}, {stage!r}, {executable!r}, task_id, str(lease_fd), str(ready_fd)]
tasks.submit_task(SyncRequest(str(Path.home() / 'local'), 'example', '/sample/remote'), 'account')
'''
    client = subprocess.Popen([sys.executable, "-c", client_code], env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    store = TaskStore()
    identity = None
    task_id = None
    try:
        marker = tmp_path / "gated-worker.json"
        identity = pump(lambda: json.loads(marker.read_text()) if marker.exists() else None)
        record = store.records()[0]
        task_id = record["task_id"]
        client.kill()
        client.wait(timeout=3)
        observed = get_task(task_id)
        assert process_matches(identity)
        assert not observed["terminal"], "a live starting worker was reported as finished"
        assert observed["state"] == "starting"
        if stage == "preflight":
            assert store.read(task_id)["process"] == identity
        if cancel:
            pending = cancel_task(task_id, timeout=0)
            assert pending["cancel_requested"]
            assert not pending["terminal"]
            assert store.cancel_requested(task_id)
        (tmp_path / "release-worker").touch()
        final = wait_task(task_id, timeout=6)
        assert final["terminal"]
        if cancel:
            assert final["state"] == "cancelled"
            assert final["cli_exit_code"] == 7
            assert final["accepted"] is False
            assert final["transfer_success"] is False
            assert not (tmp_path / "bin/invocations.jsonl").exists(), "rsync started after startup cancellation"
        else:
            assert final["state"] == "succeeded"
            assert final["transfer_success"]
            assert len((tmp_path / "bin/invocations.jsonl").read_text().splitlines()) == 1
    finally:
        if client.poll() is None:
            client.kill()
            client.wait(timeout=3)
        if task_id:
            stop_session(store.read(task_id).get("child_process"))
        if identity:
            stop_session(identity)
            os.waitpid(identity["pid"], 0)
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0


def test_startup_cancel_is_returned_to_the_waiting_cli_as_cancelled(tmp_path, monkeypatch):
    from app.runtime import TaskStore, stop_session
    from app.tasks import cancel_task

    env = configure_home(tmp_path, monkeypatch)
    client_code = f'''import sys
from pathlib import Path
from app import tasks
from app.cli import main
tasks._worker_command = lambda task_id, lease_fd, ready_fd: [
    sys.executable, '-c', {WRAPPER!r}, 'before_entry', '', task_id, str(lease_fd), str(ready_fd)]
raise SystemExit(main(['sync', '--local', str(Path.home() / 'local'), '--host', 'example',
                      '--remote', '/sample/remote', '--direction', 'upload',
                      '--credential', 'account', '--json']))
'''
    client = subprocess.Popen([sys.executable, "-c", client_code], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    store = TaskStore()
    identity = None
    task_id = None
    try:
        marker = tmp_path / "gated-worker.json"
        identity = pump(lambda: json.loads(marker.read_text()) if marker.exists() else None)
        task_id = store.records()[0]["task_id"]
        assert cancel_task(task_id, timeout=0)["cancel_requested"]
        (tmp_path / "release-worker").touch()
        stdout, stderr = client.communicate(timeout=6)
        assert client.returncode == 7, stderr + stdout
        result = json.loads(stdout)
        assert result["state"] == "cancelled" and result["terminal"]
        assert result["error"]["code"] == "CANCELLED"
        assert result["task_id"] == task_id and result["accepted"] is False
        assert not (tmp_path / "bin/invocations.jsonl").exists()
    finally:
        if client.poll() is None:
            client.kill()
        client.communicate(timeout=3)
        if task_id:
            stop_session(store.read(task_id).get("child_process"))
        if identity:
            stop_session(identity)
