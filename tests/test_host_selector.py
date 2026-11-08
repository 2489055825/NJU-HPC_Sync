import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QMessageBox

from app.credential_store import CredentialStore
from app.database import Database
from app.dialogs import ProfileDialog
from app.main_window import MainWindow
from app.models import Profile
from app.rsync_command import build_command


@pytest.fixture
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    path = ssh_dir / "config"
    path.write_text("Host alpha beta\n", encoding="utf-8")
    return path


@pytest.fixture
def window(application, config, tmp_path):
    database = Database(tmp_path / "app.sqlite3")
    window = MainWindow(database, CredentialStore(tmp_path / "credentials.json"))
    yield window
    window.close()
    database.close()


def test_main_window_offers_config_hosts_without_manual_input(window):
    assert isinstance(window.host, QComboBox)
    assert not window.host.isEditable()
    assert [window.host.itemData(index) for index in range(1, window.host.count())] == ["alpha", "beta"]
    assert not window.host.currentData()


def test_selected_alias_is_used_for_rsync_and_invalidates_preview(window, tmp_path):
    assert isinstance(window.host, QComboBox)
    window.local.setText(str(tmp_path))
    window.remote.setText("/remote/project")
    window._preview_completed = True

    window.host.setCurrentIndex(window.host.findData("beta"))

    assert not window._preview_completed
    request = window._request(True)
    assert request is not None
    assert request.remote_host == "beta"
    assert build_command(request)[-1] == "beta:/remote/project/"


def test_refresh_reloads_config_and_preserves_selection(window, config):
    assert isinstance(window.host, QComboBox)
    window.host.setCurrentIndex(window.host.findData("beta"))
    config.write_text("Host beta gamma\n", encoding="utf-8")

    window.host_selector.refresh_button.click()

    assert window.host_selector.selected_host() == "beta"
    assert window.host.findData("alpha") == -1
    assert window.host.findData("gamma") >= 0


def test_refresh_removed_host_cannot_silently_switch_target(window, config, tmp_path, monkeypatch):
    assert isinstance(window.host, QComboBox)
    window.host.setCurrentIndex(window.host.findData("alpha"))
    window.local.setText(str(tmp_path))
    window.remote.setText("/remote")
    window._preview_completed = True
    config.write_text("Host beta\n", encoding="utf-8")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings.append(text))

    window.host_selector.refresh_button.click()

    assert not window._preview_completed
    assert "alpha" in window.host.currentText()
    assert "alpha" in window.host_selector.message.text()
    assert window._request(True) is None
    assert warnings
    with pytest.raises(ValueError):
        window.host_selector.selected_host()
    window.host.setCurrentIndex(window.host.findData("beta"))
    assert window._request(True).remote_host == "beta"


def test_loading_missing_profile_keeps_target_unavailable(window, tmp_path, monkeypatch):
    assert isinstance(window.host, QComboBox)
    profile = window.db.save_profile(Profile("old profile", str(tmp_path), "removed", "/remote"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings.append(text))

    window._load_profiles(profile.id)

    assert "removed" in window.host.currentText()
    assert window._request(False) is None
    assert warnings


def test_profile_dialog_saves_selected_alias(application, config, tmp_path):
    profile = Profile("example", str(tmp_path), "beta", "/remote")
    dialog = ProfileDialog(profile=profile)
    try:
        assert isinstance(dialog.host, QComboBox)
        assert not dialog.host.isEditable()
        assert dialog.host_selector.selected_host() == "beta"

        dialog.host.setCurrentIndex(dialog.host.findData("alpha"))
        dialog._validate_and_accept()

        assert dialog.result() == QDialog.Accepted
        assert dialog.result_profile().remote_host == "alpha"
    finally:
        dialog.close()


def test_profile_dialog_rejects_missing_host(application, config, tmp_path, monkeypatch):
    dialog = ProfileDialog(profile=Profile("example", str(tmp_path), "removed", "/remote"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings.append(text))
    try:
        assert isinstance(dialog.host, QComboBox)
        assert "removed" in dialog.host.currentText()

        dialog._validate_and_accept()

        assert dialog.result() == QDialog.Rejected
        assert warnings
        with pytest.raises(ValueError):
            dialog.result_profile()
    finally:
        dialog.close()


def test_profile_dialog_refresh_blocks_removed_selection(application, config, tmp_path, monkeypatch):
    dialog = ProfileDialog(profile=Profile("example", str(tmp_path), "alpha", "/remote"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings.append(text))
    try:
        assert isinstance(dialog.host, QComboBox)
        config.write_text("Host beta\n", encoding="utf-8")

        dialog.host_selector.refresh_button.click()
        dialog._validate_and_accept()

        assert dialog.result() == QDialog.Rejected
        assert warnings
    finally:
        dialog.close()


def test_missing_config_provides_location_and_can_be_refreshed(application, config, tmp_path):
    from app.host_selector import SshHostSelector

    config.unlink()
    selector = SshHostSelector()
    try:
        assert str(config) in selector.message.text()
        with pytest.raises(ValueError):
            selector.selected_host()

        config.write_text("Host newly-added\n", encoding="utf-8")
        selector.refresh_button.click()
        selector.combo.setCurrentIndex(selector.combo.findData("newly-added"))

        assert selector.selected_host() == "newly-added"
        assert selector.message.isHidden()
    finally:
        selector.close()


def test_new_profile_persists_form_and_selects_saved_task(window, tmp_path, monkeypatch):
    local = tmp_path / "local data"

    def accept_profile(dialog):
        dialog.name.setText("new task")
        dialog.local.setText(str(local))
        dialog.host_selector.set_host("beta")
        dialog.remote.setText("/sample/project")
        dialog.direction.setCurrentIndex(dialog.direction.findData("download"))
        dialog.mode.setCurrentIndex(dialog.mode.findData("mirror"))
        dialog._validate_and_accept()
        return dialog.result()

    monkeypatch.setattr(ProfileDialog, "exec", accept_profile)

    window._new_profile()
    QApplication.processEvents()

    profiles = window.db.list_profiles()
    assert len(profiles) == 1
    saved = profiles[0]
    assert saved.name == "new task"
    assert saved.local_path == str(local) + "/"
    assert saved.remote_host == "beta"
    assert saved.remote_path == "/sample/project/"
    assert saved.default_direction == "download"
    assert saved.default_mode == "mirror"
    assert window.profile_list.currentItem().data(256) == saved.id
    assert window.local.text() == str(local) + "/"
    assert window.remote.text() == "/sample/project/"
    assert window.host_selector.selected_host() == "beta"
    assert window.download.isChecked()
    assert window.mirror.isChecked()


def test_edit_profile_updates_record_and_keeps_selection(window, tmp_path, monkeypatch):
    original = window.db.save_profile(Profile("original", str(tmp_path), "alpha", "/sample/original"))
    other = window.db.save_profile(Profile("other", str(tmp_path), "alpha", "/sample/other"))
    window._load_profiles(original.id)
    QApplication.processEvents()

    def accept_edit(dialog):
        dialog.name.setText("renamed")
        dialog.host_selector.set_host("beta")
        dialog.remote.setText("/sample/edited")
        dialog._validate_and_accept()
        return dialog.result()

    monkeypatch.setattr(ProfileDialog, "exec", accept_edit)

    window._edit_profile()
    QApplication.processEvents()

    saved = window.db.get_profile(original.id)
    assert saved.name == "renamed"
    assert saved.remote_host == "beta"
    assert saved.remote_path == "/sample/edited/"
    assert len(window.db.list_profiles()) == 2
    assert window.db.get_profile(other.id) == other
    assert window.profile_list.currentItem().data(256) == original.id
    assert window.profile_list.currentItem().text() == "renamed"
    assert window.remote.text() == "/sample/edited/"
    assert window.host_selector.selected_host() == "beta"


@pytest.mark.parametrize("action", ["_new_profile", "_edit_profile"])
def test_duplicate_profile_warns_and_preserves_saved_tasks(window, tmp_path, monkeypatch, action):
    original = window.db.save_profile(Profile("original", str(tmp_path), "alpha", "/sample/original"))
    window.db.save_profile(Profile("existing", str(tmp_path), "beta", "/sample/existing"))
    window._load_profiles(original.id)
    QApplication.processEvents()
    before = window.db.list_profiles()
    warnings = []

    def accept_duplicate(dialog):
        dialog.name.setText("existing")
        dialog.local.setText(str(tmp_path))
        dialog.host_selector.set_host("beta")
        dialog.remote.setText("/sample/attempted-change")
        dialog._validate_and_accept()
        return dialog.result()

    monkeypatch.setattr(ProfileDialog, "exec", accept_duplicate)
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, _text: warnings.append(title))

    getattr(window, action)()
    QApplication.processEvents()

    assert warnings == ["无法保存 Profile"]
    assert window.db.list_profiles() == before
    assert window.profile_list.count() == 2
    assert window.profile_list.currentItem().data(256) == original.id
    assert window.profile_list.currentItem().text() == "original"
    assert window.remote.text() == "/sample/original"
