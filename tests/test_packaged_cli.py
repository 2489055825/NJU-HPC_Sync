"""Opt-in acceptance tests for a built executable, using no real SSH connection."""
import errno
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tests.cli_helpers import configure_home, pump


@pytest.fixture
def packaged_cli():
    executable = os.environ.get("NJU_HPC_SYNC_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Set NJU_HPC_SYNC_TEST_EXECUTABLE to validate a frozen build")
    assert Path(executable).is_file()
    return executable


def invoke(executable, env, *args):
    return subprocess.run([executable, *args], env=env, capture_output=True, text=True, timeout=10)


def test_packaged_detach_worker_waits_and_persists_result(packaged_cli, tmp_path):
    env = configure_home(tmp_path, sleep=.4)
    env["QT_QPA_PLATFORM"] = "this-is-not-a-qt-platform"
    start = invoke(packaged_cli, env, "sync", "--profile", "project", "--detach", "--json")
    assert start.returncode == 0, start.stderr + start.stdout
    task = json.loads(start.stdout)
    assert task["accepted"]
    final = invoke(packaged_cli, env, "wait", task["task_id"], "--json")
    assert final.returncode == 0, final.stderr + final.stdout
    assert json.loads(final.stdout)["state"] == "succeeded"


def test_packaged_cancel_stops_independent_worker(packaged_cli, tmp_path):
    env = configure_home(tmp_path, sleep=20)
    start = invoke(packaged_cli, env, "sync", "--profile", "project", "--detach", "--json")
    assert start.returncode == 0, start.stderr + start.stdout
    task = json.loads(start.stdout)
    stop = invoke(packaged_cli, env, "cancel", task["task_id"], "--json")
    assert stop.returncode == 0, stop.stderr + stop.stdout
    assert json.loads(stop.stdout)["state"] == "cancelled"


def test_packaged_startup_interrupt_returns_id_and_worker_still_finishes(packaged_cli, tmp_path):
    from app.runtime import TaskStore, stop_session

    env = configure_home(tmp_path)
    credential_path = tmp_path / ".config/nju-hpc-sync/credentials.json"
    payload = credential_path.read_bytes()
    credential_path.unlink()
    os.mkfifo(credential_path, 0o600)
    # The first read configures the client. Hold the worker's second read before
    # acceptance to test the actual frozen CLI, without modifying its internals.
    allow_second = threading.Event()
    gated = threading.Event()
    release = threading.Event()
    stopping = threading.Event()

    def feed_credentials():
        for second in (False, True):
            if second and not allow_second.wait(10):
                return
            deadline = time.monotonic() + 10
            while not stopping.is_set() and time.monotonic() < deadline:
                try:
                    fd = os.open(credential_path, os.O_WRONLY | os.O_NONBLOCK)
                except OSError as exc:
                    if exc.errno != errno.ENXIO:
                        raise
                    stopping.wait(.01)
                    continue
                try:
                    if second:
                        gated.set()
                        release.wait(10)
                    try:
                        os.write(fd, payload)
                    except BrokenPipeError:
                        pass
                finally:
                    os.close(fd)
                break

    feeder = threading.Thread(target=feed_credentials, daemon=True)
    feeder.start()
    client = subprocess.Popen([packaged_cli, "sync", "--profile", "project", "--json"],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    store = TaskStore(tmp_path / ".local/share/nju-hpc-sync")
    task_id = None
    try:
        task = pump(lambda: store.records() or None)
        task_id = task[0]["task_id"]
        allow_second.set()
        pump(gated.is_set)
        assert store.read(task_id)["accepted"] is False
        client.send_signal(signal.SIGINT)
        stdout, stderr = client.communicate(timeout=3)
        assert client.returncode == 130, stderr + stdout
        result = json.loads(stdout)
        assert result["task_id"] == task_id
        assert result["state"] == "starting" and result["terminal"] is False
        assert result["error"]["code"] == "WAIT_INTERRUPTED"
        assert "Traceback" not in stderr
        release.set()
        final = invoke(packaged_cli, env, "wait", task_id, "--json")
        assert final.returncode == 0, final.stderr + final.stdout
        assert json.loads(final.stdout)["state"] == "succeeded"
        assert len((tmp_path / "bin/invocations.jsonl").read_text().splitlines()) == 1
    finally:
        allow_second.set()
        release.set()
        stopping.set()
        feeder.join(3)
        if client.poll() is None:
            client.kill()
        client.communicate(timeout=3)
        if task_id:
            record = store.read(task_id)
            stop_session(record.get("child_process"))
            stop_session(record.get("process"))
