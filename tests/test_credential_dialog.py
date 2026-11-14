import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.auth import validate_automatic_credential
from app.credential_store import CredentialStore
from app.dialogs import CredentialsDialog
from app.models import Credential
from app.totp import TotpConfig, generate_code


@pytest.fixture
def credential_dialog(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = CredentialStore(tmp_path / "credentials.json")
    store.save({name: Credential(name, "fixed-" + name, "JBSWY3DPEHPK3PXP") for name in ("A", "B")})
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    dialog = CredentialsDialog(store)
    dialog.list.setCurrentRow(0)
    yield app, dialog, store, warnings
    dialog.timer.stop()
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize("create_new", [False, True], ids=["rename", "new"])
def test_duplicate_credential_never_overwrites_existing_data(credential_dialog, create_new):
    _app, dialog, store, warnings = credential_dialog
    before = store.path.read_bytes()
    changed = []
    dialog.credentials_changed.connect(lambda: changed.append(True))
    if create_new:
        dialog._new()
        dialog.static_password.setText("new-fixed")
        dialog.totp_secret.setText("JBSWY3DPEHPK3PXP")
    dialog.name.setText("B")
    dialog._save()
    assert store.path.read_bytes() == before
    assert list(dialog.credentials) == ["A", "B"]
    assert store.load()["B"].static_password == "fixed-B"
    assert warnings and changed == []


def test_nonconflicting_rename_and_edit_still_save(credential_dialog):
    _app, dialog, store, warnings = credential_dialog
    dialog.name.setText("C")
    dialog._save()
    assert set(store.load()) == {"B", "C"}
    assert store.load()["C"].static_password == "fixed-A"
    dialog.static_password.setText("changed-fixed")
    dialog._save()
    assert store.load()["C"].static_password == "changed-fixed"
    assert warnings == []


def test_new_then_click_same_credential_restores_form(credential_dialog):
    app, dialog, _store, _warnings = credential_dialog
    dialog.show()
    app.processEvents()
    dialog._new()
    item = dialog.list.item(0)
    QTest.mouseClick(dialog.list.viewport(), Qt.LeftButton, pos=dialog.list.visualItemRect(item).center())
    app.processEvents()
    assert dialog.name.text() == "A"
    assert dialog.static_password.text() == "fixed-A"
    assert dialog._editing_name == "A"


def test_totp_preview_save_and_authentication_use_same_normalized_secret(credential_dialog):
    _app, dialog, store, warnings = credential_dialog
    dialog.static_password.setText(" fixed password ")
    dialog.totp_secret.setText("  jbsw y3dp ehpk 3pxp  ")
    dialog._refresh_code()
    assert dialog.code.text().isdigit()
    dialog._save()
    saved = store.load()["A"]
    assert saved.totp_secret == "JBSWY3DPEHPK3PXP"
    assert saved.static_password == " fixed password "
    validate_automatic_credential(saved)
    assert generate_code(TotpConfig(saved.totp_secret), 59) == generate_code(TotpConfig("JBSWY3DPEHPK3PXP"), 59)
    assert warnings == []


def test_invalid_totp_secret_is_not_saved(credential_dialog):
    _app, dialog, store, warnings = credential_dialog
    before = store.path.read_bytes()
    dialog.totp_secret.setText("not-a-base32-secret!")
    dialog._save()
    assert store.path.read_bytes() == before
    assert warnings
