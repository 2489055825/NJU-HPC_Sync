import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QFormLayout, QGroupBox, QMessageBox, QProgressBar
from PySide6.QtWidgets import QSizePolicy

from app.credential_store import CredentialStore
from app.database import Database
from app.dialogs import CredentialsDialog, HistoryDialog, HostConfigDialog, PasswordDialog
from app.main_window import MainWindow
from app.models import Credential, HistoryRecord, Profile, RunStatus, format_local_time


def test_final_status_clears_current_file(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))
    window.current_file.setText("transferred-file.txt")
    window._status_changed(RunStatus.PREVIEWING.value)
    assert window.statusBar().currentMessage() == "正在预览…"

    window._status_changed(RunStatus.SUCCESS.value)

    assert window.current_file.text() == ""
    assert window.current_file.toolTip() == ""
    assert window.statusBar().currentMessage() == ""
    window.close()
    app.processEvents()


def test_invalid_profile_row_does_not_crash_gui_or_change_database(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "invalid.sqlite3"
    db = Database(path)
    db.save_profile(Profile("example", "/tmp/local", "example", "/remote"))
    db._connection.execute("ALTER TABLE profiles ADD COLUMN legacy_value TEXT")
    db._connection.commit()
    before = path.read_bytes()
    window = MainWindow(db, CredentialStore(tmp_path / "credentials.json"))
    try:
        assert window.profile_list.count() == 0
        assert "PROFILE_CONFIG_INVALID" in window.statusBar().currentMessage()
        assert path.read_bytes() == before
    finally:
        window.close()
        app.processEvents()


def test_transfer_progress_is_parsed_and_displayed(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))
    window._append_log(">f+++++++++ result/file.pth\n")
    assert window.transfer_count.text() == "1 / 统计中"

    window._append_log("\r              0   0%    0.00kB/s    0:00:00")
    assert window.transfer_details.text() == "大小：0 B / 未知 | 进度：0% | 速度：0.00kB/s"

    window._append_log("\r         10,240 100%    1.25MB/s    0:00:01 (xfr#1, to-chk=4/12)\n")

    assert window.transfer_count.text() == "1 / 12"
    assert window.transfer_details.text() == "大小：10.0 KiB / 10.0 KiB | 进度：100% | 速度：1.25MB/s"
    window._reset_transfer_info()
    assert window.transfer_count.text() == "-"
    assert window.transfer_details.text() == "大小：- | 进度：- | 速度：-"
    window.close()
    app.processEvents()


def test_log_append_keeps_view_at_bottom(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))
    for index in range(80):
        window._append_log(f"[日志] {index}\n")
    window.log.verticalScrollBar().setValue(0)

    window._append_log("[日志] latest\n")
    app.processEvents()

    assert window.log.verticalScrollBar().value() == window.log.verticalScrollBar().maximum()
    window.close()
    app.processEvents()


def test_save_log_failure_shows_warning_and_preserves_log(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))
    window.log.setPlainText("safe log contents")
    warnings = []
    export_path = tmp_path / "export.log"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(export_path), ""))

    def fail_write(_path, _text, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    try:
        with monkeypatch.context() as failure:
            failure.setattr(Path, "write_text", fail_write)
            window._save_log()

        assert warnings and "read-only filesystem" in warnings[0]
        assert window.log.toPlainText() == "safe log contents"

        window._save_log()

        assert export_path.read_text(encoding="utf-8") == "safe log contents"
        assert len(warnings) == 1
    finally:
        window.close()
        app.processEvents()


def test_main_window_has_no_progress_bar(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))

    assert window.findChildren(QProgressBar) == []

    window.close()
    app.processEvents()


def test_management_menu_exposes_host_manager(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))

    management_menu = window.menuBar().actions()[0].menu()

    assert "Host 管理" in [action.text() for action in management_menu.actions()]
    window.close()
    app.processEvents()


def test_delete_selected_profile_removes_it_from_database_and_list(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "app.sqlite3")
    database.save_profile(Profile("profile-a", "/tmp/a", "host", "/remote/a"))
    database.save_profile(Profile("profile-b", "/tmp/b", "host", "/remote/b"))
    window = MainWindow(database, CredentialStore(tmp_path / "credentials.json"))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    try:
        window.profile_list.setCurrentRow(0)
        window._delete_profile()

        assert [profile.name for profile in database.list_profiles()] == ["profile-b"]
        assert window.profile_list.count() == 1
        assert window.profile_list.item(0).text() == "profile-b"
    finally:
        window.close()
        app.processEvents()


def test_host_manager_save_refreshes_host_selector(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    config = ssh_dir / "config"
    config.write_text("Host alpha\n", encoding="utf-8")
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))
    window.host_selector.set_host("alpha")
    config.write_text("Host beta\n", encoding="utf-8")

    original_exec = HostConfigDialog.exec

    def emit_saved(dialog):
        dialog.config_saved.emit()
        return QDialog.Rejected

    monkeypatch.setattr(HostConfigDialog, "exec", emit_saved)
    try:
        window._open_host_manager()

        assert window.host_selector.combo.findData("beta") >= 0
    finally:
        monkeypatch.setattr(HostConfigDialog, "exec", original_exec)
        window.close()
        app.processEvents()


def test_transfer_information_is_visually_grouped(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))

    assert window.transfer_group.title() == "传输实时信息"
    assert isinstance(window.transfer_group.layout(), QFormLayout)
    assert window.transfer_group.layout().horizontalSpacing() == 24
    assert window.transfer_group.layout().verticalSpacing() == 10

    window.close()
    app.processEvents()


def test_log_time_is_displayed_in_local_timezone():
    previous_timezone = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"
    time.tzset()
    try:
        assert MainWindow._format_time("2026-08-23T07:54:41+00:00") == "2026-08-23 15:54:41 UTC+08:00"
    finally:
        if previous_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_timezone
        time.tzset()


def test_long_current_file_does_not_set_a_wide_minimum_size(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))
    long_path = "/" + "/".join(["very-long-directory-name"] * 40) + "/result.out"

    window._set_current_file(long_path)

    assert window.current_file.sizePolicy().horizontalPolicy() == QSizePolicy.Ignored
    assert window.current_file.minimumSizeHint().width() == 0
    assert window.current_file.toolTip() == long_path
    window.resize(640, 480)
    window.show()
    app.processEvents()
    assert "…" in window.current_file.text()
    window.close()
    app.processEvents()


def test_password_prompt_wraps_without_widening_dialog():
    app = QApplication.instance() or QApplication([])
    prompt = "Password authentication for /" + "long-segment/" * 16 + ":"
    dialog = PasswordDialog(prompt)

    dialog.show()
    app.processEvents()

    assert dialog.width() == 520
    assert dialog.prompt_label.wordWrap()
    assert dialog.prompt_label.toolTip() == prompt
    assert dialog.prompt_label.width() <= 520
    dialog.close()
    app.processEvents()


def test_long_profile_name_is_elided_without_horizontal_scrollbar(tmp_path):
    app = QApplication.instance() or QApplication([])
    profile_name = "profile-" + "very-long-name-" * 20
    database = Database(tmp_path / "app.sqlite3")
    database.save_profile(Profile(profile_name, "/tmp/local", "host", "/remote"))
    window = MainWindow(database, CredentialStore(tmp_path / "credentials.json"))

    window.show()
    app.processEvents()
    assert window.profile_list.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    assert window.profile_list.textElideMode() == Qt.ElideMiddle
    assert window.profile_list.horizontalScrollBar().maximum() == 0
    assert window.profile_list.item(0).toolTip() == profile_name
    window.close()
    database.close()
    app.processEvents()


def test_profile_refresh_preserves_selected_row_and_scroll_position(tmp_path):
    app = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "app.sqlite3")
    for index in range(20):
        database.save_profile(Profile(f"profile-{index:02d}", "/tmp/local", "host", "/remote"))
    window = MainWindow(database, CredentialStore(tmp_path / "credentials.json"))
    window.show()
    app.processEvents()

    window.profile_list.setCurrentRow(15)
    window.profile_list.scrollToItem(window.profile_list.item(15))
    app.processEvents()
    selected_name = window.profile_list.currentItem().text()
    scroll_position = window.profile_list.verticalScrollBar().value()

    window._load_profiles()
    app.processEvents()

    assert window.profile_list.currentItem().text() == selected_name
    assert window.profile_list.verticalScrollBar().value() == scroll_position
    window.close()
    database.close()
    app.processEvents()


def test_credential_refresh_preserves_selected_row_and_scroll_position(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = CredentialStore(tmp_path / "credentials.json")
    store.save({
        f"credential-{index:02d}": Credential(f"credential-{index:02d}", "password", "JBSWY3DPEHPK3PXP")
        for index in range(20)
    })
    dialog = CredentialsDialog(store)
    dialog.show()
    app.processEvents()
    dialog.list.setCurrentRow(15)
    dialog.list.scrollToItem(dialog.list.item(15))
    app.processEvents()
    selected_name = dialog.list.currentItem().text()
    scroll_position = dialog.list.verticalScrollBar().value()

    dialog._refresh_list()
    app.processEvents()

    assert dialog.list.currentItem().text() == selected_name
    assert dialog.list.verticalScrollBar().value() == scroll_position
    dialog.close()
    app.processEvents()


def test_history_uses_tooltips_and_stable_columns_for_long_values():
    app = QApplication.instance() or QApplication([])
    profile_name = "profile-" + "very-long-name-" * 20
    record = HistoryRecord(
        profile_name=profile_name,
        local_path="/tmp/local",
        remote_host="host",
        remote_path="/remote",
        direction="upload",
        mode="normal",
        dry_run=False,
        status="Success",
        exit_code=0,
        duration=1.0,
        log="log",
        start_time="2026-08-24T05:00:00+00:00",
        end_time="2026-08-24T05:00:01+00:00",
    )
    dialog = HistoryDialog([record])

    dialog.show()
    app.processEvents()

    assert not dialog.table.wordWrap()
    assert dialog.table.textElideMode() == Qt.ElideRight
    assert dialog.table.columnWidth(0) == 230
    assert dialog.table.item(0, 1).toolTip() == profile_name
    assert dialog.table.item(0, 0).toolTip() == format_local_time(record.start_time)
    dialog.close()
    app.processEvents()
