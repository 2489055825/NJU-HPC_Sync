import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from tests.cli_helpers import configure_home, pump


def test_gui_cannot_execute_while_cli_holds_lease(tmp_path, monkeypatch):
    from app.main_window import SyncWorker
    from app.models import RunStatus
    from app.runtime import TransferLease

    app = QApplication.instance() or QApplication([])
    configure_home(tmp_path, monkeypatch)
    marker = tmp_path / "must-not-run"
    command = [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)]
    worker = SyncWorker(command, "manual", None)
    results = []
    worker.finished.connect(results.append)
    with TransferLease.acquire({"owner": "cli", "task_id": "running"}):
        worker.run()
    assert results[0].status is RunStatus.FAILED
    assert "BusyError" in results[0].output
    assert not marker.exists()
    app.processEvents()


def test_cli_cannot_execute_during_gui_transfer_and_can_run_after_cancel(tmp_path, monkeypatch):
    from app.main_window import SyncWorker
    from app.models import SyncRequest
    from app.runtime import BusyError
    from app.tasks import submit_task, wait_task

    app = QApplication.instance() or QApplication([])
    configure_home(tmp_path, monkeypatch)
    marker = tmp_path / "started"
    script = "import time,sys; from pathlib import Path; Path(sys.argv[1]).touch(); time.sleep(20)"
    worker = SyncWorker([sys.executable, "-c", script, str(marker)], "manual", None)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        pump(marker.exists)
        with pytest.raises(BusyError) as error:
            submit_task(SyncRequest(str(tmp_path / "local"), "example", "/sample/remote"), "account")
        assert error.value.details["owner"]["owner"] == "gui"
    finally:
        worker.cancel()
        thread.join(5)
    assert not thread.is_alive()
    task = submit_task(SyncRequest(str(tmp_path / "local"), "example", "/sample/remote"), "account")
    assert wait_task(task["task_id"], timeout=5)["state"] == "succeeded"
    app.processEvents()
