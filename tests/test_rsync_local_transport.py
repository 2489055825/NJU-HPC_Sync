"""Real rsync client/server over a local SSH stand-in, without any network."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.cli_helpers import configure_home


def configure_real_transport(home):
    rsync = shutil.which("rsync")
    if not rsync:
        pytest.skip("System rsync is not installed")
    env = configure_home(home)
    # Execute the real client with an explicit remote shell. This also keeps
    # a mistakenly constructed host::module case on the isolated SSH stand-in
    # instead of allowing rsync to open a daemon socket. Refuse daemon servers
    # as well, so a regression cannot read this computer's daemon configuration.
    binary = home / "bin/rsync"
    binary.write_text(
        f"#!{sys.executable}\nimport os,sys\n"
        "args=sys.argv[1:]\n"
        "if '--daemon' in args: sys.exit('Daemon transport is forbidden in this test')\n"
        "if '--server' not in args: args=['-e','ssh',*args]\n"
        f"os.execv({rsync!r}, [{rsync!r},*args])\n", encoding="utf-8")
    binary.chmod(0o755)
    # OpenSSH gives the joined remote command to a shell. Keep that expansion
    # layer in the test, with both rsync endpoints confined to temporary data.
    script = (f"#!{sys.executable}\nimport os,sys\n"
              "args=sys.argv[sys.argv.index('example')+1:]\n"
              "os.chdir(os.environ['HOME'])\n"
              "os.execv('/bin/sh', ['sh', '-c', ' '.join(args)])\n")
    ssh = home / "bin/ssh"
    ssh.write_text(script, encoding="utf-8")
    ssh.chmod(0o755)
    return env


def cli_command(packaged):
    if packaged:
        executable = os.environ.get("NJU_HPC_SYNC_TEST_EXECUTABLE")
        if not executable:
            pytest.skip("No packaged executable specified")
        return [executable]
    return [sys.executable, "-m", "app.cli"]


@pytest.mark.parametrize("packaged", [False, True], ids=["source", "packaged"])
def test_real_rsync_preview_mirror_and_download(tmp_path, packaged):
    command = cli_command(packaged)
    env = configure_real_transport(tmp_path)
    source = tmp_path / "local"
    remote = tmp_path / "remote data"
    remote.mkdir()
    (source / "中文 file.txt").write_text("payload\n", encoding="utf-8")
    (remote / "obsolete.txt").write_text("old\n", encoding="utf-8")

    def run(*args):
        completed = subprocess.run([*command, *args, "--local", str(source), "--host", "example",
                                    "--remote", str(remote), "--credential", "account", "--json"],
                                   env=env, capture_output=True, text=True, timeout=10)
        assert completed.returncode == 0, completed.stderr + completed.stdout
        return json.loads(completed.stdout)

    preview = run("preview", "--mirror", "--direction", "upload")
    assert preview["dry_run"] and preview["summary"]["deleted_entries"] == 1
    assert (remote / "obsolete.txt").exists()
    assert not (remote / "中文 file.txt").exists()
    uploaded = run("sync", "--mirror", "--direction", "upload")
    assert uploaded["transfer_success"]
    assert (remote / "中文 file.txt").read_text(encoding="utf-8") == "payload\n"
    assert not (remote / "obsolete.txt").exists()
    (remote / "result.txt").write_text("computed\n", encoding="utf-8")
    downloaded = run("sync", "--direction", "download")
    assert downloaded["transfer_success"]
    assert (source / "result.txt").read_text(encoding="utf-8") == "computed\n"


@pytest.mark.parametrize("packaged", [False, True], ids=["source", "packaged"])
@pytest.mark.parametrize("literal,decoy", [
    ("data[1]", "data1"),
    ("data*", "data-other"),
    ("data?", "dataX"),
    ("data]", "other"),
    (r"data\name", "dataname"),
    (r"data\[1]", r"data\1"),
    (r"parent\name/child[1]", r"parent\name/child1"),
    ("空 格'\";$name/child[1]", "空 格'\";$name/child1"),
    ("$(touch shell-marker)`touch shell-marker`", "other"),
    ("line\n[1]", "line\n1"),
])
def test_literal_remote_directory_never_selects_glob_decoy(tmp_path, packaged, literal, decoy):
    command = cli_command(packaged)
    env = configure_real_transport(tmp_path)
    local = tmp_path / "local"
    remote, other = tmp_path / "remote" / literal, tmp_path / "remote" / decoy
    remote.mkdir(parents=True)
    other.mkdir(parents=True)
    (remote / "must_keep.txt").write_text("correct source\n")
    (other / "wrong.txt").write_text("wrong source\n")
    (local / "must_keep.txt").write_text("correct source\n")

    def run(operation, direction):
        completed = subprocess.run(
            [*command, operation, "--mirror", "--local", str(local), "--host", "example",
             "--remote", str(remote), "--direction", direction, "--credential", "account", "--json"],
            env=env, capture_output=True, text=True, timeout=10)
        assert completed.returncode == 0, completed.stderr + completed.stdout
        return json.loads(completed.stdout)

    preview = run("preview", "download")
    assert preview["summary"]["deleted_entries"] == 0
    assert not (local / "wrong.txt").exists()
    result = run("sync", "download")
    assert result["transfer_success"]
    assert (local / "must_keep.txt").read_text() == "correct source\n"
    assert not (local / "wrong.txt").exists()
    assert result["request"]["remote_path"] == str(remote) + "/"

    (local / "uploaded.txt").write_text("new local content\n")
    run("sync", "upload")
    assert (remote / "uploaded.txt").read_text() == "new local content\n"
    assert (other / "wrong.txt").read_text() == "wrong source\n"
    assert not (other / "uploaded.txt").exists()
    assert not (tmp_path / "shell-marker").exists()


@pytest.mark.parametrize("packaged", [False, True], ids=["source", "packaged"])
@pytest.mark.parametrize("literal", ["new[1]", "new*", "new?", r"new\[1]", "空 格'\";\n[new]"])
def test_upload_creates_exact_literal_directory_and_preview_does_not(tmp_path, packaged, literal):
    command = cli_command(packaged)
    env = configure_real_transport(tmp_path)
    # An explicit CLI policy must override inherited rsync defaults.
    env.update(RSYNC_PROTECT_ARGS="1", RSYNC_OLD_ARGS="2")
    local = tmp_path / "local"
    parent = tmp_path / "remote"
    parent.mkdir()
    remote = parent / literal
    decoy = parent / "new1"
    decoy.mkdir()
    (decoy / "must_keep.txt").write_text("untouched\n")
    (local / "uploaded.txt").write_text("new local content\n")
    for operation in ("preview", "sync"):
        completed = subprocess.run(
            [*command, operation, "--mirror", "--local", str(local), "--host", "example",
             "--remote", str(remote), "--direction", "upload", "--credential", "account", "--json"],
            env=env, capture_output=True, text=True, timeout=10)
        assert completed.returncode == 0, completed.stderr + completed.stdout
        assert json.loads(completed.stdout)["transfer_success"]
        if operation == "preview":
            assert not remote.exists()
    assert (remote / "uploaded.txt").read_text() == "new local content\n"
    assert (decoy / "must_keep.txt").read_text() == "untouched\n"
    assert sorted(path.name for path in parent.iterdir()) == sorted([literal, "new1"])


@pytest.mark.parametrize("packaged", [False, True], ids=["source", "packaged"])
@pytest.mark.parametrize("old_args", ["0", "2"])
def test_missing_literal_source_never_mirrors_a_matching_decoy(tmp_path, packaged, old_args):
    command = cli_command(packaged)
    env = configure_real_transport(tmp_path)
    env.update(RSYNC_PROTECT_ARGS="0", RSYNC_OLD_ARGS=old_args)
    local = tmp_path / "local"
    remote = tmp_path / "remote" / "data[1]"
    # The escaped spelling is a distinct legal directory, too. A protected
    # rsync argument falls back to that spelling when its glob matches nothing.
    for name in ("data1", r"data\[1]"):
        decoy = remote.with_name(name)
        decoy.mkdir(parents=True)
        (decoy / "wrong.txt").write_text("wrong source\n")
    (local / "must_keep.txt").write_text("correct local data\n")
    completed = subprocess.run(
        [*command, "sync", "--mirror", "--local", str(local), "--host", "example",
         "--remote", str(remote), "--direction", "download", "--credential", "account", "--json"],
        env=env, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 6, completed.stderr + completed.stdout
    assert json.loads(completed.stdout)["state"] == "failed"
    assert (local / "must_keep.txt").read_text() == "correct local data\n"
    assert not (local / "wrong.txt").exists()


@pytest.mark.parametrize("packaged", [False, True], ids=["source", "packaged"])
@pytest.mark.parametrize("name", ["relative[1]", "-P"])
def test_relative_remote_source_ignores_cdpath_and_cd_options(tmp_path, packaged, name):
    command = cli_command(packaged)
    env = configure_real_transport(tmp_path)
    remote = tmp_path / name
    remote.mkdir()
    (remote / "correct.txt").write_text("literal relative source\n")
    decoy = tmp_path / "search" / name
    decoy.mkdir(parents=True)
    (decoy / "wrong.txt").write_text("CDPATH decoy\n")
    env["CDPATH"] = str(decoy.parent)
    completed = subprocess.run(
        [*command, "sync", "--mirror", "--local", str(tmp_path / "local"), "--host", "example",
         f"--remote={name}", "--direction", "download", "--credential", "account", "--json"],
        env=env, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert (tmp_path / "local/correct.txt").read_text() == "literal relative source\n"
    assert not (tmp_path / "local/wrong.txt").exists()


@pytest.mark.parametrize("packaged", [False, True], ids=["source", "packaged"])
def test_relative_upload_starting_with_colon_remains_an_ssh_directory(tmp_path, packaged):
    command = cli_command(packaged)
    env = configure_real_transport(tmp_path)
    (tmp_path / "local/uploaded.txt").write_text("literal colon directory\n")
    completed = subprocess.run(
        [*command, "sync", "--mirror", "--local", str(tmp_path / "local"), "--host", "example",
         "--remote", ":data", "--direction", "upload", "--credential", "account", "--json"],
        env=env, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert (tmp_path / ":data/uploaded.txt").read_text() == "literal colon directory\n"
