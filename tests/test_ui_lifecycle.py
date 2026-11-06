import os
import secrets
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

import app.main_window as module
from app.credential_store import CredentialStore
from app.database import Database
from app.dialogs import CredentialsDialog
from app.models import Credential, Profile, RunStatus


def pump_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert predicate(), "Qt task did not reach the expected state"


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh/config").write_text("Host example\n", encoding="utf-8")
    db = Database(tmp_path / "history.sqlite3")
    for name in ("profile-A", "profile-B"):
        db.save_profile(Profile(name, str(tmp_path) + "/", "example", f"/sample/{name}/"))
    instance = module.MainWindow(db, CredentialStore(tmp_path / "credentials.json"))
    instance.auth_mode.setCurrentIndex(instance.auth_mode.findData("manual"))
    yield instance
    if instance._worker is not None:
        instance._cancel()
        pump_until(lambda: instance._worker is None)
    instance.close()
    app.processEvents()


def configure_totp_wait(window, monkeypatch):
    waiting = threading.Event()
    original_provider = module.AutoAuthProvider

    def provider(*args, **kwargs):
        result = original_provider(*args, **kwargs)
        result.session.clock = lambda: 100.0
        result.session.replay_guard.claim(result.session.config, 3)
        original_wait = result.session._wait

        def wait(delay):
            waiting.set()
            original_wait(delay)

        result.session._wait = wait
        return result

    monkeypatch.setattr(module, "AutoAuthProvider", provider)
    monkeypatch.setattr(module, "build_command", lambda request: [sys.executable, "-c", "import sys; print('Password:',flush=True); sys.stdin.readline()"])
    window.credentials["synthetic"] = Credential("synthetic", secrets.token_hex(8), "JBSWY3DPEHPK3PXP")
    window.credential.addItem("synthetic", "synthetic")
    window.credential.setCurrentIndex(window.credential.findData("synthetic"))
    window.auth_mode.setCurrentIndex(window.auth_mode.findData("auto"))
    window._start_sync()
    pump_until(waiting.is_set)


def test_totp_stop_restores_ui_without_waiting_for_next_window(window, monkeypatch):
    configure_totp_wait(window, monkeypatch)
    window.cancel_button.click()
    window._status_changed(RunStatus.AUTHENTICATING.value)
    assert window.status_label.text() != RunStatus.AUTHENTICATING.value

    pump_until(lambda: window._worker is None, 2)

    assert window.sync_button.isEnabled()
    assert window.profile_list.isEnabled()
    assert window.status_label.text() == RunStatus.CANCELLED.value


def test_close_during_totp_wait_saves_final_history_before_database_close(window, monkeypatch, tmp_path):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, traceback: errors.append(kind.__name__))
    configure_totp_wait(window, monkeypatch)
    window.show()

    window.close()
    pump_until(lambda: window._thread is None and not window.isVisible())

    assert errors == []
    with Database(tmp_path / "history.sqlite3") as db:
        assert db.list_history()[0].status == RunStatus.CANCELLED.value


def test_unfinished_thread_causes_close_to_be_deferred(window):
    class BusyThread:
        def quit(self):
            pass

        def wait(self, timeout):
            return False

    class BusyWorker:
        def cancel(self):
            pass

    window._worker = BusyWorker()
    window._thread = BusyThread()
    event = QCloseEvent()
    try:
        window.closeEvent(event)

        assert not event.isAccepted()
        assert len(window.db.list_profiles()) == 2
    finally:
        window._worker = None
        window._thread = None


def test_cancelled_ui_ignores_queued_status_and_progress(window):
    class Worker:
        def cancel(self):
            pass

    window._worker = Worker()
    try:
        window._cancel()
        window._status_changed(RunStatus.TRANSFERRING.value)
        window._process_output_line(">f+++++++++ late-output.txt")
        window._process_output_line("         10 100% 1.00kB/s 0:00:00 (xfr#1, to-chk=0/1)")

        assert window.status_label.text() != RunStatus.TRANSFERRING.value
        assert window.current_file.text() == ""
        assert window.transfer_count.text() == "-"
    finally:
        window._worker = None


def test_credential_dialogs_stop_timers_and_are_disposed(window):
    def reject_dialog():
        QApplication.activeModalWidget().reject()

    for _ in range(3):
        QTimer.singleShot(0, reject_dialog)
        window._open_credentials()

    assert all(not dialog.timer.isActive() for dialog in window.findChildren(CredentialsDialog))
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert window.findChildren(CredentialsDialog) == []


def test_profile_switch_resets_previous_task_display(window):
    window.status_label.setText(RunStatus.SUCCESS.value)
    window._set_current_file("last-task.txt")
    window.transfer_count.setText("5 / 5")
    window.profile_list.setCurrentRow(1)

    assert window.status_label.text() == RunStatus.WAITING.value
    assert window.current_file.text() == ""
    assert window.transfer_count.text() == "-"


def test_stop_before_thread_start_does_not_launch_task(window, monkeypatch, tmp_path):
    original_start = QThread.start
    queued_threads = []
    marker = tmp_path / "must-not-run"
    monkeypatch.setattr(QThread, "start", lambda thread: queued_threads.append(thread))
    monkeypatch.setattr(module, "build_command", lambda request: [sys.executable, "-c", "import sys; from pathlib import Path; Path(sys.argv[1]).touch()", str(marker)])
    window._start_sync()
    window.cancel_button.click()
    original_start(queued_threads[0])

    pump_until(lambda: window._worker is None)

    assert not marker.exists()
    assert window.status_label.text() == RunStatus.CANCELLED.value
    assert window.sync_button.isEnabled()


def test_existing_profile_does_not_hide_startup_credential_error(window, tmp_path):
    path = tmp_path / "bad-credentials.json"
    path.write_text("{ invalid JSON", encoding="utf-8")
    db = Database(tmp_path / "other-history.sqlite3")
    db.save_profile(Profile("existing", str(tmp_path), "example", "/sample/"))
    other = module.MainWindow(db, CredentialStore(path))
    try:
        assert other.statusBar().currentMessage()
        assert other.auth_mode.currentData() == "manual"
    finally:
        other.close()


@pytest.mark.parametrize("prompt", [
    "Password:",
    "Are you sure you want to continue connecting (yes/no)? ",
])
def test_close_during_modal_authentication_cleans_up(window, monkeypatch, tmp_path, prompt):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, traceback: errors.append(kind.__name__))
    script = "import sys; print(sys.argv[1],flush=True); sys.stdin.readline()"
    monkeypatch.setattr(module, "build_command", lambda request: [sys.executable, "-c", script, prompt])

    def close_prompt():
        if window._active_prompt_dialog is None:
            QTimer.singleShot(10, close_prompt)
        else:
            window.close()

    window.show()
    QTimer.singleShot(10, close_prompt)
    window._start_sync()
    pump_until(lambda: window._thread is None and not window.isVisible())

    assert errors == []
    assert window._active_prompt_dialog is None
    with Database(tmp_path / "history.sqlite3") as db:
        assert db.list_history()[0].status == RunStatus.CANCELLED.value


def test_repeated_tasks_restore_controls_across_profile_and_mode_switches(window, monkeypatch):
    requests = []
    script = "print('>f+++++++++ example.txt\\nNumber of regular files transferred: 1\\nNumber of deleted files: 0')"

    def command(request):
        requests.append(request)
        return [sys.executable, "-c", script]

    monkeypatch.setattr(module, "build_command", command)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Yes)
    for index in range(8):
        window.profile_list.setCurrentRow(index % 2)
        assert window.status_label.text() == RunStatus.WAITING.value
        window.download.setChecked(index % 2 == 1)
        window.upload.setChecked(index % 2 == 0)
        window.mirror.setChecked(index % 3 == 0)
        window.normal.setChecked(index % 3 != 0)
        window._start_sync()
        pump_until(lambda: window._worker is None and not window._start_after_cleanup)
        QApplication.processEvents()
        pump_until(lambda: window._worker is None and not window._start_after_cleanup)
        assert window.status_label.text() == RunStatus.SUCCESS.value
        assert window.sync_button.isEnabled()
        assert window.profile_list.isEnabled()
        assert window.remote.isEnabled()
        assert not window.cancel_button.isEnabled()
        assert window.transfer_count.text() == "-"

    assert sum(not request.dry_run for request in requests) == 8
    assert sum(request.dry_run for request in requests) == 3
