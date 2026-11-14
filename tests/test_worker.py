import json
import os
import signal
import subprocess
import sys
import threading
from dataclasses import asdict

import pytest

from tests.cli_helpers import configure_home


def run_worker(tmp_path, monkeypatch, *, mirror=False, dry_run=False, **behavior):
    from app.models import SyncMode, SyncRequest
    from app.runtime import TaskStore, TransferLease
    from app.worker import execute_task

    configure_home(tmp_path, monkeypatch, **behavior)
    store = TaskStore()
    request = SyncRequest(str(tmp_path / "local"), "example", "/sample/remote",
                          mode=SyncMode.MIRROR if mirror else SyncMode.NORMAL, dry_run=dry_run)
    record = store.create(asdict(request), "account", "project")
    with TransferLease.acquire({"owner": "cli", "task_id": record["task_id"]}) as lease:
        execute_task(record["task_id"], os.dup(lease.fd), None)
    return store.read(record["task_id"]), store


def test_worker_persists_mirror_preview_statistics_and_history(tmp_path, monkeypatch):
    record, store = run_worker(tmp_path, monkeypatch, mirror=True, dry_run=True)
    assert record["state"] == "succeeded"
    assert record["terminal"] and record["transfer_success"]
    assert record["dry_run"] and record["mode"] == "mirror"
    assert record["summary"]["deleted_entries"] == 2
    assert record["summary"]["transferred_file_bytes"] == 128
    assert record["history_saved"]
    assert len(record["changes"]) == 1
    argv = json.loads((tmp_path / "bin/invocations.jsonl").read_text())
    assert argv[-3:] == ["--", str(tmp_path / "local") + "/", "example:/sample/remote/"]
    assert "--delete" in argv and "--dry-run" in argv
    assert (store.task_dir(record["task_id"]) / "output.log").exists()


def test_worker_uses_saved_totp_and_redacts_echoed_password(tmp_path, monkeypatch):
    from app.totp import TotpSession

    # Pick a safe instant while retaining real code generation and durable claims.
    original = TotpSession.__init__

    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.clock = lambda: 100.0

    monkeypatch.setattr(TotpSession, "__init__", initialize)
    record, store = run_worker(tmp_path, monkeypatch, authenticate=True, echo_password=True)
    assert record["state"] == "succeeded"
    log = (store.task_dir(record["task_id"]) / "output.log").read_text()
    assert "synthetic-fixed" not in log and "JBSWY3DPEHPK3PXP" not in log
    assert "TRACE:[REDACTED]" in log
    assert "synthetic-fixed" not in json.dumps(record)
    runtime = tmp_path / ".local/share/nju-hpc-sync"
    assert not list(runtime.glob("totp-windows.*"))


def test_unknown_host_key_fails_without_interaction(tmp_path, monkeypatch):
    record, _store = run_worker(tmp_path, monkeypatch, host_key=True)
    assert record["state"] == "failed"
    assert record["error"]["code"] == "HOST_KEY_REQUIRED"
    assert record["cli_exit_code"] == 5


def test_history_write_failure_does_not_turn_success_into_retry(tmp_path, monkeypatch):
    from app.database import Database

    def fail(_self, _record):
        raise OSError("synthetic disk full")

    monkeypatch.setattr(Database, "add_history", fail)
    record, _store = run_worker(tmp_path, monkeypatch)
    assert record["state"] == "succeeded" and record["transfer_success"]
    assert record["history_saved"] is False
    assert record["warnings"]


def test_nonzero_rsync_is_not_reported_as_success(tmp_path, monkeypatch):
    record, _store = run_worker(tmp_path, monkeypatch, exit_code=23, error=True)
    assert record["state"] == "failed"
    assert record["rsync_exit_code"] == 23
    assert record["cli_exit_code"] == 6


def test_worker_defers_sigterm_cleanup_until_runner_control_flow(tmp_path, monkeypatch):
    from app.models import SyncRequest
    from app.rsync_runner import RsyncRunner
    from app.runtime import TaskStore, TransferLease
    from app.worker import execute_task

    configure_home(tmp_path, monkeypatch, sleep=20)
    store = TaskStore()
    request = SyncRequest(str(tmp_path / "local"), "example", "/sample/remote")
    record = store.create(asdict(request), "account", "project")
    started = threading.Event()
    original_run = RsyncRunner.run

    def run(*args, **kwargs):
        started.set()
        return original_run(*args, **kwargs)

    monkeypatch.setattr("app.worker.RsyncRunner.run", run)

    def send_sigterm():
        if started.wait(3):
            os.kill(os.getpid(), signal.SIGTERM)

    signal_thread = threading.Thread(target=send_sigterm, daemon=True)
    signal_thread.start()
    with TransferLease.acquire({"owner": "cli", "task_id": record["task_id"]}) as lease:
        code = execute_task(record["task_id"], os.dup(lease.fd), None)
    signal_thread.join(3)
    final = store.read(record["task_id"])
    assert code == 7
    assert final["state"] == "cancelled" and final["terminal"]


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signal_cancels_worker_during_totp_window_wait(tmp_path, monkeypatch, signum):
    from app.models import SyncRequest
    from app.runtime import TaskStore, TransferLease, session_alive, stop_session
    from tests.cli_helpers import pump

    env = configure_home(tmp_path, monkeypatch, authenticate=True)
    store = TaskStore()
    record = store.create(asdict(SyncRequest(str(tmp_path / "local"), "example", "/sample/remote")),
                          "account", "project")
    task_id = record["task_id"]
    script = '''import sys
from pathlib import Path
from app import worker
original = worker.AutoAuthProvider
def provider(*args, **kwargs):
    value = original(*args, **kwargs)
    value.session.clock = lambda: 100.0
    value.session.replay_guard.claim(value.session.config, 3)
    wait = value.session._wait
    def waiting(delay):
        (Path.home() / 'waiting-totp').touch()
        wait(delay)
    value.session._wait = waiting
    return value
worker.AutoAuthProvider = provider
raise SystemExit(worker.execute_task(sys.argv[1], int(sys.argv[2]), None))
'''
    child = None
    try:
        with TransferLease.acquire({"owner": "cli", "task_id": task_id}) as lease:
            child = subprocess.Popen([sys.executable, "-c", script, task_id, str(lease.fd)],
                                     env=env, pass_fds=(lease.fd,), start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            lease.close()
            pump((tmp_path / "waiting-totp").exists)
            child.send_signal(signum)
            assert child.wait(timeout=3) == 7
        final = store.read(task_id)
        assert final["state"] == "cancelled" and final["terminal"]
        assert not session_alive(final.get("child_process"))
        with TransferLease.acquire({"owner": "test"}):
            pass  # Cancelled task released the inherited lease, too.
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
        stop_session(store.read(task_id).get("child_process"))
