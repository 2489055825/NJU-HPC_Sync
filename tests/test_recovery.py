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


def test_credential_save_failure_preserves_disk_and_editor_state(home, monkeypatch):
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
    monkeypatch.setattr(store, "save", lambda _credentials: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    try:
        dialog._save()

        assert warnings and "disk full" in warnings[0]
        assert path.read_bytes() == original_bytes
        assert list(dialog.credentials) == ["original"]
        assert dialog._editing_name == "original"
        assert dialog.name.text() == "renamed"
        assert changed == []
    finally:
        dialog.done(0)


def test_credential_delete_failure_preserves_disk_and_dialog_state(home, monkeypatch):
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
    monkeypatch.setattr(store, "save", lambda _credentials: (_ for _ in ()).throw(OSError("read-only filesystem")))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    try:
        dialog._delete()

        assert warnings and "read-only filesystem" in warnings[0]
        assert path.read_bytes() == original_bytes
        assert list(dialog.credentials) == ["original"]
        assert dialog.list.currentItem().text() == "original"
        assert changed == []
    finally:
        dialog.done(0)
