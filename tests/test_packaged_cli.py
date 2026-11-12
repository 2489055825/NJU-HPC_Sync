"""Opt-in acceptance tests for a built executable, using no real SSH connection."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.cli_helpers import configure_home


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
