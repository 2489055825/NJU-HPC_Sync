import json
import os
import signal
import select
import subprocess
import sys
from pathlib import Path

import pytest

from tests.cli_helpers import configure_home, pump


ROOT = Path(__file__).resolve().parents[1]


def cli(env, *args, timeout=8):
    return subprocess.run([sys.executable, "-m", "app.cli", *args], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)


def test_readonly_queries_do_not_create_configuration(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    for args, field in [(('profiles', 'list'), 'profiles'), (('history', 'list'), 'history'),
                        (('credentials', 'list'), 'credentials'), (('hosts', 'list'), 'hosts')]:
        result = cli(env, *args, "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)[field] == []
    assert list(tmp_path.iterdir()) == []


def test_profile_sync_is_normal_unless_mirror_explicit_and_stdout_is_json(tmp_path):
    env = configure_home(tmp_path)
    result = cli(env, "sync", "--profile", "project", "--json")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["state"] == "succeeded" and value["mode"] == "normal"
    assert value["summary"]["transferred_files"] == 1
    assert value["task_id"] in result.stderr
    assert "synthetic-fixed" not in result.stdout + result.stderr


def test_temporary_download_mirror_preview(tmp_path):
    env = configure_home(tmp_path)
    result = cli(env, "preview", "--local", str(tmp_path / "local"), "--host", "example",
                 "--remote", "/sample/remote", "--direction", "download", "--credential", "account",
                 "--mirror", "--json")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["direction"] == "download" and value["mode"] == "mirror" and value["dry_run"]
    argv = json.loads((tmp_path / "bin/invocations.jsonl").read_text())
    assert argv[-2:] == ["example:./", str(tmp_path / "local") + "/"]
    assert "--rsync-path=CDPATH= cd -P /sample/remote/ && exec rsync" in argv
    assert value["request"]["remote_path"] == "/sample/remote/"


@pytest.mark.parametrize("args", [
    ["sync", "--profile", "project", "--local", "/tmp/source"],
    ["sync", "--local", "/tmp/source", "--host", "example", "--remote", "/sample/remote"],
    ["sync", "--profile", "missing"],
    ["sync", "--profile", "project", "--detach", "--wait-timeout", "1"],
    ["wait", "--timeout", "-1"], ["cancel", "../escape"],
])
def test_invalid_arguments_are_json_errors_without_starting_transfer(tmp_path, args):
    env = configure_home(tmp_path)
    result = cli(env, *args, "--json")
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"]
    assert not (tmp_path / "bin/invocations.jsonl").exists()


def test_detach_status_wait_and_history(tmp_path):
    env = configure_home(tmp_path, sleep=.3)
    submitted = cli(env, "sync", "--profile", "project", "--detach", "--json")
    assert submitted.returncode == 0, submitted.stderr
    value = json.loads(submitted.stdout)
    assert value["accepted"]
    final = cli(env, "wait", value["task_id"], "--json")
    assert final.returncode == 0
    status = cli(env, "status", "--json")
    assert json.loads(status.stdout)["state"] == "succeeded"
    history = cli(env, "history", "list", "--json")
    assert len(json.loads(history.stdout)["history"]) == 1


def test_wait_timeout_leaves_background_task_running(tmp_path):
    env = configure_home(tmp_path, sleep=.5)
    result = cli(env, "sync", "--profile", "project", "--wait-timeout", ".01", "--json")
    assert result.returncode == 124
    value = json.loads(result.stdout)
    assert value["error"]["code"] == "WAIT_TIMEOUT"
    assert cli(env, "wait", value["task_id"], "--json").returncode == 0


def test_sync_exit_code_and_status_exit_code_have_different_meanings(tmp_path):
    env = configure_home(tmp_path, exit_code=23)
    result = cli(env, "sync", "--profile", "project", "--json")
    assert result.returncode == 6
    value = json.loads(result.stdout)
    assert value["rsync_exit_code"] == 23 and value["state"] == "failed"
    status = cli(env, "status", value["task_id"], "--json")
    assert status.returncode == 0
    assert json.loads(status.stdout)["state"] == "failed"


def test_main_cli_dispatch_never_imports_qt(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    script = '''import sys
class BlockQt:
    def find_spec(self, fullname, *args):
        if fullname.startswith("PySide6"):
            raise RuntimeError("CLI attempted to import Qt")
sys.meta_path.insert(0, BlockQt())
sys.argv = ["nju-hpc-sync", "profiles", "list", "--json"]
import main
raise SystemExit(main.main())
'''
    result = subprocess.run([sys.executable, "-c", script], env=env, cwd=ROOT,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["profiles"] == []


def test_terminated_cli_waiter_does_not_cancel_worker(tmp_path):
    env = configure_home(tmp_path, sleep=.6)
    client = subprocess.Popen([sys.executable, "-m", "app.cli", "sync", "--profile", "project", "--json"],
                              env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = client.stderr.readline()
        assert "task_id=" in line
        task_id = line.strip().split("task_id=", 1)[1]
        client.send_signal(signal.SIGTERM)
        stdout, _stderr = client.communicate(timeout=5)
        assert client.returncode == 143
        assert json.loads(stdout)["task_id"] == task_id
        assert cli(env, "wait", task_id, "--json").returncode == 0
    finally:
        if client.poll() is None:
            client.kill()
            client.communicate()


def test_readonly_database_preserves_existing_bytes(tmp_path):
    from app.database import Database
    from app.models import Profile

    path = tmp_path / "history.sqlite3"
    with Database(path) as db:
        db.save_profile(Profile("example", "/tmp/a", "example", "/sample/a"))
    original = path.read_bytes()
    with Database(path, readonly=True) as db:
        assert len(db.list_profiles()) == 1
    assert path.read_bytes() == original
    with pytest.raises(FileNotFoundError):
        Database(tmp_path / "missing/nothing.sqlite3", readonly=True)
    assert not (tmp_path / "missing").exists()


def test_concurrent_cli_submissions_start_only_one_transfer(tmp_path):
    env = configure_home(tmp_path, sleep=.7)
    clients = [subprocess.Popen([sys.executable, "-m", "app.cli", "sync", "--profile", "project", "--detach", "--json"],
                                env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
               for _ in range(2)]
    results = [(client, client.communicate(timeout=8)) for client in clients]
    assert sorted(client.returncode for client in clients) == [0, 3]
    accepted = next(json.loads(output[0]) for client, output in results if client.returncode == 0)
    assert cli(env, "wait", accepted["task_id"], "--json").returncode == 0
    assert len((tmp_path / "bin/invocations.jsonl").read_text().splitlines()) == 1


def test_credentials_query_does_not_expose_or_change_saved_values(tmp_path):
    env = configure_home(tmp_path)
    path = tmp_path / ".config/nju-hpc-sync/credentials.json"
    original = path.read_bytes()
    result = cli(env, "credentials", "list", "--json")
    assert result.returncode == 0
    credential = json.loads(result.stdout)["credentials"][0]
    assert credential["name"] == "account" and credential["ready"]
    assert "synthetic-fixed" not in result.stdout and "JBSWY3DPEHPK3PXP" not in result.stdout
    assert path.read_bytes() == original


def test_closed_stdout_and_stderr_do_not_kill_transfer(tmp_path):
    env = configure_home(tmp_path, sleep=.3)
    client = subprocess.Popen([sys.executable, "-m", "app.cli", "sync", "--profile", "project", "--json"],
                              env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    client.stdout.close()
    client.wait(timeout=8)
    status = cli(env, "status", "--json")
    assert json.loads(status.stdout)["state"] == "succeeded"


def test_invalid_totp_secret_is_rejected_before_task_submission(tmp_path):
    env = configure_home(tmp_path)
    path = tmp_path / ".config/nju-hpc-sync/credentials.json"
    payload = json.loads(path.read_text())
    payload["account"]["totp_secret"] = "invalid!"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = cli(env, "sync", "--profile", "project", "--json")
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "CREDENTIAL_INVALID"
    assert not (tmp_path / "bin/invocations.jsonl").exists()
    assert not (tmp_path / ".local/share/nju-hpc-sync/tasks").exists()
    doctor = cli(env, "doctor", "--json")
    assert doctor.returncode == 4
    assert json.loads(doctor.stdout)["ready"] is False


def test_source_launcher_streams_task_id_before_transfer_finishes(tmp_path):
    env = configure_home(tmp_path, sleep=2)
    client = subprocess.Popen([str(ROOT / "run.sh"), "sync", "--profile", "project", "--json"],
                              env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([client.stderr], [], [], 1.5)[0], "launcher buffered task id until task completed"
        assert client.stderr.readline().startswith("task_id=")
        stdout, _stderr = client.communicate(timeout=8)
        assert client.returncode == 0 and json.loads(stdout)["state"] == "succeeded"
    finally:
        if client.poll() is None:
            client.communicate(timeout=8)


@pytest.mark.parametrize("direction", ["upload", "download"])
@pytest.mark.parametrize("local_name", ["local", "app"])
def test_relative_local_path_is_snapshotted_against_caller_directory(tmp_path, direction, local_name):
    env = configure_home(tmp_path)
    (tmp_path / local_name).mkdir(exist_ok=True)
    completed = subprocess.run([sys.executable, str(ROOT / "main.py"), "sync", "--local", local_name,
                                "--host", "example", "--remote", "/sample/remote", "--direction", direction,
                                "--credential", "account", "--mirror", "--json"], env=env, cwd=tmp_path,
                               capture_output=True, text=True, timeout=8)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["request"]["local_path"] == str(tmp_path / local_name) + "/"
    argv = json.loads((tmp_path / "bin/invocations.jsonl").read_text())
    assert str(tmp_path / local_name) + "/" in argv[-2:]


@pytest.mark.parametrize("mutation", ["missing_timestamp", "bad_process", "bad_terminal", "naive_timestamp"])
def test_corrupt_task_schema_returns_json_error(tmp_path, mutation):
    env = configure_home(tmp_path)
    from app.runtime import TaskStore

    store = TaskStore(tmp_path / ".local/share/nju-hpc-sync")
    record = store.create({}, "account", "")
    if mutation == "missing_timestamp":
        del record["created_at"]
    elif mutation == "bad_process":
        record["process"] = "not-a-process-identity"
    elif mutation == "bad_terminal":
        record["terminal"] = "false"
    else:
        record["created_at"] = "2026-01-01T00:00:00"
    (store.task_dir(record["task_id"]) / "task.json").write_text(json.dumps(record), encoding="utf-8")
    result = cli(env, "status", "--json")
    assert result.returncode == 8
    assert json.loads(result.stdout)["error"]["code"] == "RECORD_INVALID"
    assert "Traceback" not in result.stderr
