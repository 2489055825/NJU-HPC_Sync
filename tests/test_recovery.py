import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.credential_store import CredentialStore
from app.database import Database
from app.dialogs import HostConfigDialog
from app.main_window import MainWindow


@pytest.fixture
def home(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh/config").write_text("Host example\n", encoding="utf-8")
    yield tmp_path
    app.processEvents()


@pytest.mark.parametrize("payload", [
    b"{ invalid JSON", b"\xff", b'{"example":{"totp_period":"invalid"}}',
    b'{"example":{"totp_period":1e999}}', b'{"example":{"totp_period":0}}',
])
def test_corrupt_credentials_allow_manual_startup_without_rewriting(home, payload, monkeypatch):
    path = home / "credentials.json"
    path.write_bytes(payload)
    notices = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: notices.append(args[1]))
    window = MainWindow(Database(home / "history.sqlite3"), CredentialStore(path))
    try:
        assert window.credentials == {}
        assert window.auth_mode.currentData() == "manual"
        assert window.statusBar().currentMessage()
        window._open_credentials()
        assert notices
        assert path.read_bytes() == payload
    finally:
        window.close()


@pytest.mark.parametrize("method", ["button", "escape", "window"])
@pytest.mark.parametrize("choice", [QMessageBox.Cancel, QMessageBox.Discard, QMessageBox.Save])
def test_editor_close_respects_save_discard_cancel(home, monkeypatch, method, choice):
    path = home / ".ssh/config"
    original = path.read_text(encoding="utf-8")
    dialog = HostConfigDialog()
    dialog.show()
    dialog.editor.insertPlainText("# edited\n")
    questions = []
    responses = iter([choice, QMessageBox.Yes])
    monkeypatch.setattr(QMessageBox, "question", lambda *args: questions.append(args[1]) or next(responses))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        if method == "button":
            dialog.close_button.click()
        elif method == "escape":
            dialog.reject()
        else:
            dialog.close()

        assert questions
        assert dialog.isVisible() == (choice == QMessageBox.Cancel)
        expected = "# edited\n" + original if choice == QMessageBox.Save else original
        assert path.read_text(encoding="utf-8") == expected
    finally:
        dialog.done(0)


def test_failed_save_on_close_retains_editor_draft(home, monkeypatch):
    dialog = HostConfigDialog()
    dialog.show()
    dialog.editor.setPlainText('Host "unclosed')
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Save)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    try:
        dialog.reject()

        assert dialog.isVisible()
        assert dialog.editor.toPlainText() == 'Host "unclosed'
        assert (home / ".ssh/config").read_text(encoding="utf-8") == "Host example\n"
    finally:
        dialog.done(0)


@pytest.mark.parametrize("failure_point", ["fsync", "replace"])
def test_credential_save_failure_preserves_disk_and_editor_state(home, monkeypatch, failure_point):
    from app.dialogs import CredentialsDialog
    from app.models import Credential

    path = home / "credentials.json"
    store = CredentialStore(path)
    store.save({"original": Credential("original", "static", "JBSWY3DPEHPK3PXP")})
    original_bytes = path.read_bytes()
    dialog = CredentialsDialog(store)
    changed = []
    warnings = []
    dialog.credentials_changed.connect(lambda: changed.append(True))
    dialog.name.setText("renamed")
    dialog.static_password.setText("edited-static")
    original_credentials = store.load()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))

    def fail_write(*_args):
        raise OSError("disk full")

    try:
        with monkeypatch.context() as failure:
            failure.setattr(f"app.credential_store.os.{failure_point}", fail_write)
            dialog._save()

        assert warnings and "disk full" in warnings[0]
        assert path.read_bytes() == original_bytes
        assert dialog.credentials == original_credentials
        assert dialog._editing_name == "original"
        assert dialog.name.text() == "renamed"
        assert dialog.static_password.text() == "edited-static"
        assert list(home.glob("credentials.*.tmp")) == []
        assert changed == []

        dialog._save()
        QApplication.processEvents()

        assert store.load() == dialog.credentials
        assert list(store.load()) == ["renamed"]
        assert store.load()["renamed"].static_password == "edited-static"
        assert dialog.list.currentItem().text() == "renamed"
        assert dialog._editing_name == "renamed"
        assert changed == [True]
    finally:
        dialog.done(0)


@pytest.mark.parametrize("failure_point", ["fsync", "replace"])
def test_credential_delete_failure_preserves_disk_and_dialog_state(home, monkeypatch, failure_point):
    from app.dialogs import CredentialsDialog
    from app.models import Credential

    path = home / "credentials.json"
    store = CredentialStore(path)
    store.save({"original": Credential("original", "static", "JBSWY3DPEHPK3PXP")})
    original_bytes = path.read_bytes()
    dialog = CredentialsDialog(store)
    changed = []
    warnings = []
    dialog.credentials_changed.connect(lambda: changed.append(True))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))

    def fail_write(*_args):
        raise OSError("read-only filesystem")

    try:
        with monkeypatch.context() as failure:
            failure.setattr(f"app.credential_store.os.{failure_point}", fail_write)
            dialog._delete()

        assert warnings and "read-only filesystem" in warnings[0]
        assert path.read_bytes() == original_bytes
        assert list(dialog.credentials) == ["original"]
        assert dialog.list.currentItem().text() == "original"
        assert list(home.glob("credentials.*.tmp")) == []
        assert changed == []

        dialog._delete()
        QApplication.processEvents()

        assert store.load() == dialog.credentials == {}
        assert dialog.list.count() == 0
        assert dialog.name.text() == ""
        assert dialog.static_password.text() == ""
        assert dialog.totp_secret.text() == ""
        assert changed == [True]
    finally:
        dialog.done(0)


@pytest.mark.parametrize("operation", ["save", "delete"])
def test_credential_commit_keeps_disk_and_dialog_in_sync(home, monkeypatch, operation):
    from app.dialogs import CredentialsDialog
    from app.models import Credential

    path = home / "credentials.json"
    store = CredentialStore(path)
    store.save({"original": Credential("original", "static", "JBSWY3DPEHPK3PXP")})
    dialog = CredentialsDialog(store)
    warnings = []
    changed = []
    dialog.credentials_changed.connect(lambda: changed.append(True))
    original_chmod = os.chmod

    def fail_destination_chmod(target, mode):
        if target == path:
            raise PermissionError("synthetic chmod failure after replacement")
        original_chmod(target, mode)

    monkeypatch.setattr("app.credential_store.os.chmod", fail_destination_chmod)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    try:
        if operation == "save":
            dialog.name.setText("renamed")
            dialog._save()
            expected_names = ["renamed"]
        else:
            dialog._delete()
            expected_names = []

        assert warnings == []
        assert list(store.load()) == list(dialog.credentials) == expected_names
        assert os.stat(path).st_mode & 0o777 == 0o600
        assert changed == [True]
    finally:
        dialog.done(0)
