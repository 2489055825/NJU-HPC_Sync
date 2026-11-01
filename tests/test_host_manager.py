import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(mode=0o700)
    path = ssh_dir / "config"
    path.write_text("Host alpha\n    HostName alpha.example\n", encoding="utf-8")
    return path


def test_dialog_loads_only_main_config_file(application, config):
    from app.dialogs import HostConfigDialog

    dialog = HostConfigDialog()
    try:
        assert dialog.config_path == config
        assert str(config) in dialog.path_label.text()
        assert dialog.editor.toPlainText() == config.read_text(encoding="utf-8")
    finally:
        dialog.close()


def test_save_creates_backup_and_emits_signal(application, config, monkeypatch):
    from app.dialogs import HostConfigDialog

    dialog = HostConfigDialog()
    saved = []
    dialog.config_saved.connect(lambda: saved.append(True))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    try:
        dialog.editor.setPlainText("Host beta\n    HostName beta.example\n")

        dialog._save()

        assert config.read_text(encoding="utf-8").startswith("Host beta")
        assert (config.parent / "config.bak").read_text(encoding="utf-8").startswith("Host alpha")
        assert saved == [True]
        assert not dialog.isWindowModified()
    finally:
        dialog.close()


def test_invalid_config_is_rejected_without_writing(application, config, monkeypatch):
    from app.dialogs import HostConfigDialog

    dialog = HostConfigDialog()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    original = config.read_text(encoding="utf-8")
    try:
        dialog.editor.setPlainText("Host\n")

        dialog._save()

        assert config.read_text(encoding="utf-8") == original
        assert warnings and "第 1 行" in warnings[0]
    finally:
        dialog.close()


def test_external_change_requires_confirmation_before_overwrite(application, config, monkeypatch):
    from app.dialogs import HostConfigDialog

    dialog = HostConfigDialog()
    questions = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _parent, _title, text, *_args: questions.append(text) or QMessageBox.No,
    )
    try:
        dialog.editor.setPlainText("Host from-editor\n")
        config.write_text("Host from-other-process\n", encoding="utf-8")

        dialog._save()

        assert config.read_text(encoding="utf-8") == "Host from-other-process\n"
        assert questions and str(config) in questions[0]
    finally:
        dialog.close()


def test_cancel_does_not_write_editor_contents(application, config):
    from app.dialogs import HostConfigDialog

    dialog = HostConfigDialog()
    original = config.read_text(encoding="utf-8")
    try:
        dialog.editor.setPlainText("Host unsaved\n")
        dialog.reject()

        assert config.read_text(encoding="utf-8") == original
    finally:
        dialog.close()


def test_snapshot_read_failure_is_reported_without_writing(application, config, monkeypatch):
    from app import dialogs
    from app.dialogs import HostConfigDialog

    dialog = HostConfigDialog()
    warnings = []
    monkeypatch.setattr(dialogs, "config_snapshot", lambda _path: (_ for _ in ()).throw(OSError("read failed")))
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    original = config.read_text(encoding="utf-8")
    try:
        dialog.editor.setPlainText("Host changed\n")

        dialog._save()

        assert config.read_text(encoding="utf-8") == original
        assert warnings and "read failed" in warnings[0]
    finally:
        dialog.close()


def test_reload_after_external_change_can_discard_local_edits(application, config, monkeypatch):
    from app.dialogs import HostConfigDialog

    dialog = HostConfigDialog()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    try:
        dialog.editor.insertPlainText("# local edit\n")
        assert dialog.editor.document().isModified()
        config.write_text("Host external\n", encoding="utf-8")

        dialog._reload()

        assert dialog.editor.toPlainText() == "Host external\n"
        assert not dialog.editor.document().isModified()
    finally:
        dialog.close()


def test_save_confirmation_shows_both_paths_and_can_cancel(application, config, monkeypatch):
    from app.dialogs import HostConfigDialog

    dialog = HostConfigDialog()
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda parent, title, text, *args: questions.append(text) or QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        dialog.editor.setPlainText("Host new\n")

        dialog._save()

        assert config.read_text(encoding="utf-8").startswith("Host alpha")
        assert questions and str(config) in questions[0]
        assert str(config.with_name("config.bak")) in questions[0]
    finally:
        dialog.close()
