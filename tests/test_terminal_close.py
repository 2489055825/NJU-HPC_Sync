import json
import os
import subprocess
import sys

import pexpect
import pytest

from tests.cli_helpers import configure_home


@pytest.mark.parametrize("packaged", [False, True], ids=["source", "packaged"])
def test_closing_callers_pty_does_not_stop_accepted_task(tmp_path, packaged):
    if packaged:
        executable = os.environ.get("NJU_HPC_SYNC_TEST_EXECUTABLE")
        if not executable:
            pytest.skip("No packaged executable specified")
        command = [executable]
    else:
        command = [sys.executable, "-m", "app.cli"]
    env = configure_home(tmp_path, sleep=.5)
    terminal = pexpect.spawn(command[0], [*command[1:], "sync", "--profile", "project", "--json"],
                             env=env, encoding="utf-8", timeout=8)
    try:
        terminal.expect(r"task_id=([0-9a-f]{32})")
        task_id = terminal.match[1]
    finally:
        terminal.close(force=True)
    final = subprocess.run([*command, "wait", task_id, "--json"], env=env,
                           capture_output=True, text=True, timeout=8)
    assert final.returncode == 0, final.stderr + final.stdout
    assert json.loads(final.stdout)["state"] == "succeeded"
