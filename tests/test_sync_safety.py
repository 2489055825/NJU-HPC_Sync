import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.credential_store import CredentialStore
from app.database import Database
from app.main_window import MainWindow
from app.models import RunResult, RunStatus, SyncMode


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh/config").write_text("Host example\n", encoding="utf-8")
    instance = MainWindow(Database(tmp_path / "history.sqlite3"), CredentialStore(tmp_path / "credentials.json"))
    instance.local.setText(str(tmp_path) + "/")
    instance.remote.setText("/sample/first/")
    instance.host_selector.set_host("example")
    instance.auth_mode.setCurrentIndex(instance.auth_mode.findData("manual"))
    instance.mirror.setChecked(True)
    yield instance
    instance.close()
    app.processEvents()


def complete_preview(window):
    window._current_request = window._request(True)
    window._pending_mirror = False
    window._worker_finished(RunResult(RunStatus.SUCCESS, 0, "Number of deleted files: 1,234\n", "", "", 0.1, []))
    window._current_request = None


def test_refusing_mirror_confirmation_never_authorizes_next_click(window, monkeypatch):
    confirmations = []
    launches = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: confirmations.append(args[2]) or QMessageBox.No)
    monkeypatch.setattr(window, "_start_worker", launches.append)
    complete_preview(window)

    window._start_sync()
    window._start_sync()

    assert len(confirmations) == 2
    assert launches == []
    assert "1234" in confirmations[0] or "1,234" in confirmations[0]


def test_successful_old_preview_cannot_authorize_changed_target(window, monkeypatch):
    original = window._request(True)
    window._current_request = original
    window.remote.setText("/sample/second/")
    window._worker_finished(RunResult(RunStatus.SUCCESS, 0, "", "", "", 0.1, []))
    window._current_request = None
    launches = []
    monkeypatch.setattr(window, "_start_worker", launches.append)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Yes)

    window._start_sync()

    assert len(launches) == 1
    assert launches[0].dry_run
    assert launches[0].remote_path == "/sample/second/"


def test_accepted_manual_preview_is_used_once(window, monkeypatch):
    complete_preview(window)
    launches = []
    confirmations = []
    monkeypatch.setattr(window, "_start_worker", launches.append)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: confirmations.append(args[2]) or QMessageBox.Yes)

    window._start_sync()
    window._start_sync()

    assert len(confirmations) == 1
    assert not launches[0].dry_run
    assert launches[1].dry_run


def test_parameters_changed_during_confirmation_require_new_preview(window, monkeypatch):
    complete_preview(window)
    launches = []
    monkeypatch.setattr(window, "_start_worker", launches.append)

    def change_target(*args):
        window.remote.setText("/sample/changed-in-modal/")
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "warning", change_target)
    window._start_sync()

    assert not any(not request.dry_run for request in launches)


def test_counts_parse_grouped_numbers():
    assert MainWindow._sync_counts("Number of regular files transferred: 1,234\nNumber of deleted files: 2,345 (reg: 2,345)\n") == (1234, 2345)


def test_auto_preview_confirms_after_cleanup_and_rejects_execution(window, monkeypatch):
    launches = []
    questions = []
    monkeypatch.setattr(window, "_start_worker", launches.append)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: questions.append(args[2]) or QMessageBox.No)
    window._start_sync()
    assert launches[0].dry_run
    window._current_request = launches[0]
    window._worker_finished(RunResult(RunStatus.SUCCESS, 0, "Number of deleted files: 0\n", "", "", 0.1, []))
    window._thread_cleanup()
    QApplication.processEvents()
    window._start_sync()

    assert len(questions) == 2
    assert len(launches) == 1


def test_real_worker_locks_task_controls_and_restores_after_exit(window, monkeypatch):
    monkeypatch.setattr("app.main_window.build_command", lambda request: [sys.executable, "-c", "import time; time.sleep(.25)"])
    window._start_worker(window._request(True))
    try:
        assert not window.remote.isEnabled()
        assert not window.host_selector.isEnabled()
        assert not window.profile_list.isEnabled()
        assert not window.download.isEnabled()
        assert all(not action.isEnabled() for action in window._task_actions)
    finally:
        deadline = time.monotonic() + 5
        while window._thread is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(.01)
    assert window._thread is None
    assert window.remote.isEnabled()
    assert window.download.isEnabled()
