import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGroupBox, QProgressBar
from PySide6.QtWidgets import QSizePolicy

from app.credential_store import CredentialStore
from app.database import Database
from app.dialogs import CredentialsDialog, HistoryDialog, PasswordDialog
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


def test_transfer_progress_is_parsed_and_displayed(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))
    window._append_log("\r         10,240 100%    1.25MB/s    0:00:01 (xfr#2, to-chk=4/12)\n")

    assert window.transfer_count.text() == "文件：2 / 12"
    assert window.transfer_details.text() == "大小：10.0 KiB / 10.0 KiB | 进度：100% | 速度：1.25MB/s"
    window._reset_transfer_info()
    assert window.transfer_count.text() == "文件：-"
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


def test_main_window_has_no_progress_bar(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))

    assert window.findChildren(QProgressBar) == []

    window.close()
    app.processEvents()


def test_transfer_information_is_visually_grouped(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Database(tmp_path / "app.sqlite3"), CredentialStore(tmp_path / "credentials.json"))

    assert any(group.title() == "传输实时信息" for group in window.findChildren(QGroupBox))

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
