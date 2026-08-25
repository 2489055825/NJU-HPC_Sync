from __future__ import annotations

import threading
import re
import shlex
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, QSize, Qt, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QButtonGroup,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .auth import AutoAuthProvider
from .credential_store import CredentialStore
from .database import Database
from .dialogs import CredentialsDialog, HistoryDialog, LogManagementDialog, PasswordDialog, ProfileDialog, UsageDialog
from .models import Direction, HistoryRecord, Profile, RunResult, RunStatus, SyncMode, SyncRequest, format_local_time
from .paths import normalize_directory_path, normalize_local_path, redact_text
from .rsync_command import build_command, preflight
from .rsync_runner import RunnerCallbacks, RsyncRunner, friendly_exit_message
from .totp import TotpReplayGuard


class PromptBroker:
    def __init__(self, request_signal: Signal):
        self._condition = threading.Condition()
        self._response: str | None = None
        self._waiting = False
        self._request_signal = request_signal

    def request(self, prompt: str) -> str | None:
        with self._condition:
            self._response = None
            self._waiting = True
            self._request_signal.emit(prompt)
            self._condition.wait_for(lambda: not self._waiting, timeout=600)
            response = self._response
            self._response = None
            return response

    def answer(self, value: str | None) -> None:
        with self._condition:
            if not self._waiting:
                return
            self._response = value
            self._waiting = False
            self._condition.notify_all()


class CurrentFileLabel(QLabel):
    """Display long paths within the available layout width."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self.setWordWrap(False)

    def set_full_text(self, value: str) -> None:
        self._full_text = value
        self.setToolTip(value)
        self._refresh_text()

    def clear(self) -> None:
        self._full_text = ""
        self.setToolTip("")
        super().clear()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().sizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self) -> None:
        width = self.contentsRect().width()
        text = self.fontMetrics().elidedText(self._full_text, Qt.ElideMiddle, width) if width > 0 else ""
        super().setText(text)


class SyncWorker(QObject):
    output = Signal(str)
    status = Signal(str)
    prompt = Signal(str)
    notice = Signal(str)
    finished = Signal(object)

    def __init__(self, command: list[str], auth_mode: str, credential, dry_run: bool = False, replay_guard: TotpReplayGuard | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.command = command
        self.auth_mode = auth_mode
        self.credential = credential
        self.dry_run = dry_run
        self.replay_guard = replay_guard
        self.runner = RsyncRunner()
        self.broker = PromptBroker(self.prompt)

    @Slot()
    def run(self) -> None:
        provider = None
        secrets: list[str] = []
        try:
            if self.auth_mode == "auto":
                if self.credential is None:
                    raise ValueError("自动认证需要选择凭据")
                provider = AutoAuthProvider(self.credential, self.notice.emit, self.replay_guard)
                secrets = provider.sensitive_values()
            else:
                provider = self.broker.request
            def report_status(value: RunStatus) -> None:
                self.status.emit(RunStatus.PREVIEWING.value if self.dry_run and value in {RunStatus.CONNECTING, RunStatus.TRANSFERRING} else value.value)

            callbacks = RunnerCallbacks(on_output=self.output.emit, on_status=report_status, on_prompt=provider)
            result = self.runner.run(self.command, callbacks, secrets=secrets, preflight=False)
        except Exception as exc:
            self.output.emit(f"{type(exc).__name__}: {exc}\n")
            result = RunResult(RunStatus.FAILED, None, f"{type(exc).__name__}: {exc}\n", "", "", 0.0, self.command)
        self.finished.emit(result)

    def answer(self, value: str | None) -> None:
        self.broker.answer(value)

    def cancel(self) -> None:
        self.broker.answer(None)
        self.runner.cancel()


class MainWindow(QMainWindow):
    def __init__(self, database: Database | None = None, credentials: CredentialStore | None = None):
        super().__init__()
        self.setWindowTitle("NJU-HPC Sync")
        self.resize(1120, 760)
        self.db = database or Database()
        self.db.cleanup_history()
        self.credential_store = credentials or CredentialStore()
        self.credentials = self.credential_store.load()
        self._profiles: list[Profile] = []
        self._thread: QThread | None = None
        self._worker: SyncWorker | None = None
        self._current_request: SyncRequest | None = None
        self._current_profile_name = ""
        self._pending_mirror = False
        self._start_after_cleanup = False
        self._preview_completed = False
        self._preview_output = ""
        self._run_number = 0
        self._log_pending = ""
        self._last_log_status = ""
        self._totp_replay_guard = TotpReplayGuard()
        self._build_ui()
        self._load_profiles()

    def _build_ui(self) -> None:
        self.profile_list = QListWidget()
        self.profile_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.profile_list.setTextElideMode(Qt.ElideMiddle)
        self.profile_list.currentItemChanged.connect(self._profile_selected)
        new_button = QPushButton("＋ 新建")
        edit_button = QPushButton("编辑")
        delete_button = QPushButton("删除")
        new_button.clicked.connect(self._new_profile)
        edit_button.clicked.connect(self._edit_profile)
        delete_button.clicked.connect(self._delete_profile)
        profile_actions = QHBoxLayout()
        profile_actions.addWidget(new_button)
        profile_actions.addWidget(edit_button)
        profile_actions.addWidget(delete_button)
        left = QVBoxLayout()
        left.addWidget(QLabel("Profiles"))
        left.addWidget(self.profile_list)
        left.addLayout(profile_actions)
        left_widget = QWidget()
        left_widget.setLayout(left)

        self.local = QLineEdit()
        self.local.textChanged.connect(self._inputs_changed)
        local_browse = QPushButton("选择")
        local_browse.clicked.connect(self._browse_local)
        local_row = QHBoxLayout()
        local_row.setContentsMargins(0, 0, 0, 0)
        local_row.setSpacing(8)
        local_row.addWidget(self.local)
        local_row.addWidget(local_browse)
        local_widget = QWidget()
        local_widget.setLayout(local_row)
        self.host = QLineEdit()
        self.host.textChanged.connect(self._inputs_changed)
        self.remote = QLineEdit()
        self.remote.textChanged.connect(self._inputs_changed)
        self.credential = QComboBox()
        self.auth_mode = QComboBox()
        self.auth_mode.addItem("自动 TOTP", "auto")
        self.auth_mode.addItem("手工输入完整密码", "manual")
        self.auth_mode.currentIndexChanged.connect(self._auth_mode_changed)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.addRow("本地目录", local_widget)
        form.addRow("远程 Host", self.host)
        form.addRow("远程目录", self.remote)
        form.addRow("凭据", self.credential)
        form.addRow("认证方式", self.auth_mode)
        paths_group = QGroupBox("同步任务")
        paths_group.setLayout(form)

        self.upload = QRadioButton(Direction.UPLOAD.label)
        self.download = QRadioButton(Direction.DOWNLOAD.label)
        self.direction_group = QButtonGroup(self)
        self.direction_group.setExclusive(True)
        self.direction_group.addButton(self.upload)
        self.direction_group.addButton(self.download)
        self.upload.setAutoExclusive(False)
        self.download.setAutoExclusive(False)
        self.upload.setChecked(True)
        self.upload.toggled.connect(lambda checked: self._direction_changed(checked, Direction.UPLOAD))
        self.download.toggled.connect(lambda checked: self._direction_changed(checked, Direction.DOWNLOAD))
        direction_row = QHBoxLayout()
        direction_row.addWidget(self.upload)
        direction_row.addWidget(self.download)
        direction_row.addStretch()
        self.normal = QRadioButton(SyncMode.NORMAL.label)
        self.mirror = QRadioButton(SyncMode.MIRROR.label)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.normal)
        self.mode_group.addButton(self.mirror)
        self.normal.setAutoExclusive(False)
        self.mirror.setAutoExclusive(False)
        self.normal.setChecked(True)
        self.normal.toggled.connect(lambda checked: self._mode_changed(checked, SyncMode.NORMAL))
        self.mirror.toggled.connect(lambda checked: self._mode_changed(checked, SyncMode.MIRROR))
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.normal)
        mode_row.addWidget(self.mirror)
        mode_row.addStretch()
        self.mode_warning = QLabel("")
        self.mode_warning.setStyleSheet("color: #b42318; font-weight: 600;")
        self.mode_warning.setVisible(False)
        options = QVBoxLayout()
        options.addWidget(QLabel("方向"))
        options.addLayout(direction_row)
        options.addWidget(QLabel("模式"))
        options.addLayout(mode_row)
        options.addWidget(self.mode_warning)
        options_widget = QWidget()
        options_widget.setLayout(options)

        self.preview_button = QPushButton("预览")
        self.sync_button = QPushButton("开始同步")
        self.cancel_button = QPushButton("停止")
        self.preview_button.clicked.connect(self._preview)
        self.sync_button.clicked.connect(self._start_sync)
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.sync_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        self.status_label = QLabel("Waiting")
        self.current_file = CurrentFileLabel()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        clear_log = QPushButton("清空日志")
        clear_log.clicked.connect(self.log.clear)
        save_log = QPushButton("保存日志")
        save_log.clicked.connect(self._save_log)
        copy_log = QPushButton("复制日志")
        copy_log.clicked.connect(lambda: QApplication.clipboard().setText(self.log.toPlainText()))
        log_actions = QHBoxLayout()
        log_actions.addWidget(QLabel("日志"))
        log_actions.addStretch()
        log_actions.addWidget(clear_log)
        log_actions.addWidget(copy_log)
        log_actions.addWidget(save_log)
        right = QVBoxLayout()
        right.addWidget(paths_group)
        right.addWidget(options_widget)
        right.addLayout(buttons)
        right.addWidget(self.status_label)
        right.addWidget(self.current_file)
        right.addLayout(log_actions)
        right.addWidget(self.log, 1)
        right_widget = QWidget()
        right_widget.setLayout(right)
        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)
        status = QStatusBar()
        self.setStatusBar(status)
        self._build_menu()
        self._refresh_credentials()
        self._auth_mode_changed()

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("管理")
        credentials_action = QAction("凭据管理", self)
        credentials_action.triggered.connect(self._open_credentials)
        history_action = QAction("同步历史", self)
        history_action.triggered.connect(self._open_history)
        log_management_action = QAction("日志管理", self)
        log_management_action.triggered.connect(self._open_log_management)
        tutorial_action = QAction("使用教程", self)
        tutorial_action.triggered.connect(self._open_tutorial)
        menu.addAction(credentials_action)
        menu.addAction(history_action)
        menu.addAction(log_management_action)
        menu.addSeparator()
        menu.addAction(tutorial_action)

    def _load_profiles(self) -> None:
        self._profiles = self.db.list_profiles()
        self.profile_list.clear()
        for profile in self._profiles:
            item = QListWidgetItem(profile.name)
            item.setToolTip(profile.name)
            item.setData(256, profile.id)
            self.profile_list.addItem(item)
        if self.profile_list.count():
            self.profile_list.setCurrentRow(0)

    def _refresh_credentials(self) -> None:
        try:
            self.credentials = self.credential_store.load()
        except RuntimeError as exc:
            self.credentials = {}
            self.statusBar().showMessage(str(exc), 8000)
        selected = self.credential.currentData() if hasattr(self, "credential") else ""
        self.credential.clear()
        self.credential.addItem("（手工输入）", "")
        for name in sorted(self.credentials, key=str.casefold):
            self.credential.addItem(name, name)
        if selected:
            index = self.credential.findData(selected)
            if index >= 0:
                self.credential.setCurrentIndex(index)

    def _profile_selected(self, item: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if item is None:
            return
        profile = self.db.get_profile(item.data(256))
        if profile is None:
            return
        self._current_profile_name = profile.name
        self.local.setText(profile.local_path)
        self.host.setText(profile.remote_host)
        self.remote.setText(profile.remote_path)
        self.upload.setChecked(profile.default_direction == Direction.UPLOAD.value)
        self.download.setChecked(profile.default_direction == Direction.DOWNLOAD.value)
        self.normal.setChecked(profile.default_mode == SyncMode.NORMAL.value)
        self.mirror.setChecked(profile.default_mode == SyncMode.MIRROR.value)
        index = self.credential.findData(profile.credential_name)
        self.credential.setCurrentIndex(index if index >= 0 else 0)
        self._preview_completed = False

    def _new_profile(self) -> None:
        dialog = ProfileDialog(self, credential_names=list(self.credentials))
        if dialog.exec() == ProfileDialog.Accepted:
            try:
                self.db.save_profile(dialog.result_profile())
                self._load_profiles()
            except Exception as exc:
                QMessageBox.warning(self, "无法保存 Profile", str(exc))

    def _edit_profile(self) -> None:
        item = self.profile_list.currentItem()
        if item is None:
            return
        profile = self.db.get_profile(item.data(256))
        if profile is None:
            return
        dialog = ProfileDialog(self, profile, list(self.credentials))
        if dialog.exec() == ProfileDialog.Accepted:
            try:
                self.db.save_profile(dialog.result_profile())
                self._load_profiles()
            except Exception as exc:
                QMessageBox.warning(self, "无法保存 Profile", str(exc))

    def _delete_profile(self) -> None:
        item = self.profile_list.currentItem()
        if item is None:
            return
        if QMessageBox.question(self, "删除 Profile", f"确定删除“{item.text()}”？") == QMessageBox.Yes:
            self.db.delete_profile(item.data(256))
            self._load_profiles()

    def _browse_local(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择本地目录", self.local.text() or str(Path.home()))
        if path:
            self.local.setText(path)

    def _direction_changed(self, checked: bool, _direction: Direction) -> None:
        if checked:
            self._preview_completed = False

    def _inputs_changed(self, _value: str) -> None:
        self._preview_completed = False

    def _mode_changed(self, checked: bool, mode: SyncMode) -> None:
        if checked:
            is_mirror = mode is SyncMode.MIRROR
            self.mode_warning.setText("⚠ 镜像会删除 Destination 中多余文件，执行前必须预览并确认。" if is_mirror else "")
            self.mode_warning.setVisible(is_mirror)
            self._preview_completed = False

    def _auth_mode_changed(self) -> None:
        self.credential.setEnabled(self.auth_mode.currentData() == "auto")

    def _request(self, dry_run: bool) -> SyncRequest | None:
        try:
            direction = Direction.UPLOAD if self.upload.isChecked() else Direction.DOWNLOAD
            mode = SyncMode.MIRROR if self.mirror.isChecked() else SyncMode.NORMAL
            local_path = normalize_directory_path(normalize_local_path(self.local.text()))
            remote_path = normalize_directory_path(self.remote.text())
            self.local.setText(local_path)
            self.remote.setText(remote_path)
            request = SyncRequest(local_path, self.host.text(), remote_path, direction, mode, dry_run)
            check = preflight(request)
            if not check.ok:
                raise ValueError(check.message)
            return request
        except ValueError as exc:
            QMessageBox.warning(self, "无法开始同步", str(exc))
            return None

    def _preview(self) -> None:
        request = self._request(True)
        if request:
            self._pending_mirror = False
            self._start_worker(request)

    def _start_sync(self) -> None:
        if self._worker is not None:
            return
        if self.mirror.isChecked() and not self._preview_completed:
            request = self._request(True)
            if request:
                self._pending_mirror = True
                self._start_worker(request)
            return
        request = self._request(False)
        if request:
            self._start_worker(request)

    def _start_worker(self, request: SyncRequest) -> None:
        credential = self.credentials.get(self.credential.currentData() or "")
        if self.auth_mode.currentData() == "auto" and credential is None:
            QMessageBox.warning(self, "缺少凭据", "自动认证请选择一个已保存的凭据，或切换为手工输入。")
            return
        command = build_command(request)
        self._current_request = request
        self._preview_output = ""
        self._run_number += 1
        self._log_pending = ""
        self._last_log_status = ""
        self._clear_current_file()
        kind = "预览" if request.dry_run else "同步"
        profile = self._current_profile_name or "临时任务"
        command_text = shlex.join(self._display_command(command))
        self._append_log(
            f"\n{'=' * 72}\n"
            f"第 {self._run_number} 轮 | {kind} | {request.mode.label}\n"
            f"Profile: {profile}\n"
            f"连接: {request.remote_host} | {request.direction.label} | {request.local_path} -> {request.remote_path}\n"
            f"命令: {command_text}\n"
            f"{'=' * 72}\n"
        )
        self.status_label.setText(RunStatus.WAITING.value)
        self.cancel_button.setEnabled(True)
        self.preview_button.setEnabled(False)
        self.sync_button.setEnabled(False)
        self._thread = QThread(self)
        self._worker = SyncWorker(command, self.auth_mode.currentData(), credential, request.dry_run, self._totp_replay_guard)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.output.connect(self._append_log)
        self._worker.status.connect(self._status_changed)
        self._worker.prompt.connect(self._password_prompt)
        self._worker.notice.connect(self._append_notice)
        self._worker.finished.connect(self._worker_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._thread_cleanup)
        self._thread.start()

    def _display_command(self, command: list[str]) -> list[str]:
        return command

    @Slot(str)
    def _append_log(self, text: str) -> None:
        # Keep complete lines so PTY chunks split in the middle are filtered
        # consistently. Ordinary file names and progress redraws are noise in
        # the main view; history still stores the runner's complete output.
        safe_text = redact_text(text).replace("\r\n", "\n").replace("\r", "\n")
        self._log_pending += safe_text
        complete, separator, pending = self._log_pending.rpartition("\n")
        if not separator:
            pending = self._log_pending
            complete = ""
        self._log_pending = pending
        for line in complete.split("\n") if complete else []:
            self._append_log_line(line)
        for line in safe_text.splitlines():
            change = self._parse_itemized_change(line)
            if change and change[1]:
                self._set_current_file(change[1])

    def _set_current_file(self, path: str) -> None:
        self.current_file.set_full_text(path)

    def _clear_current_file(self) -> None:
        self.current_file.clear()
        self.current_file.setToolTip("")

    @Slot(str)
    def _append_notice(self, text: str) -> None:
        self._append_log(text.rstrip("\r\n") + "\n")

    def _append_log_line(self, line: str) -> None:
        line = line.strip()
        if not line or not self._is_key_log_line(line):
            return
        self.log.appendPlainText(line)
        self.log.ensureCursorVisible()

    @staticmethod
    def _is_key_log_line(line: str) -> bool:
        if line.startswith(("=", "命令:", "Profile:", "第 ", "[", "状态:", "结果:")):
            return True
        if line.startswith(("sending ", "receiving ", "building ", "created directory ", "deleting ", "sent ", "received ", "total size is ", "speedup is ", "正在")):
            return True
        return bool(re.search(r"(?i)(rsync\s*[:：]|error|failed|warning|permission denied|no such file|connection|timeout|fatal|refused|cannot|同步失败|认证失败)", line))

    @staticmethod
    def _sync_counts(output: str) -> tuple[int | None, int | None]:
        def stat(name: str) -> int | None:
            match = re.search(rf"(?im)^\s*{re.escape(name)}:\s*(\d+)\b", output)
            return int(match.group(1)) if match else None

        transferred = stat("Number of regular files transferred")
        deleted = stat("Number of deleted files")
        if deleted is None:
            deleted = sum(1 for line in output.splitlines() if line.strip().lower().startswith("deleting "))
        return transferred, deleted

    @staticmethod
    def _rsync_stat(output: str, name: str) -> int | None:
        match = re.search(rf"(?im)^\s*{re.escape(name)}:\s*([\d,]+)", output)
        return int(match.group(1).replace(",", "")) if match else None

    @staticmethod
    def _total_entries(output: str) -> int | None:
        match = re.search(r"(?im)^\s*Number of files:\s*([\d,]+)", output)
        return int(match.group(1).replace(",", "")) if match else None

    @staticmethod
    def _entry_breakdown(output: str) -> tuple[int | None, int | None]:
        match = re.search(r"(?im)^\s*Number of files:\s*[\d,]+\s*\(reg:\s*([\d,]+),\s*dir:\s*([\d,]+)", output)
        if not match:
            return None, None
        return tuple(int(value.replace(",", "")) for value in match.groups())

    @staticmethod
    def _parse_itemized_change(line: str) -> tuple[str, str] | None:
        line = line.rstrip()
        if line.startswith("*deleting"):
            name = line[len("*deleting"):].strip()
            return ("delete", name) if name else None
        if len(line) < 13 or line[11] != " ":
            return None
        code, name = line[:11], line[12:].strip()
        if not name or code[0] not in "<>" or code[1] == "d":
            return None
        return ("add" if code[2] == "+" else "update", name)

    @classmethod
    def _changes(cls, output: str) -> list[tuple[str, str]]:
        changes = []
        for line in output.splitlines():
            change = cls._parse_itemized_change(line)
            if change:
                changes.append(change)
        return changes

    @staticmethod
    def _format_bytes(value: int | None) -> str:
        if value is None:
            return "未知"
        units = ("B", "KiB", "MiB", "GiB", "TiB")
        amount = float(value)
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
            amount /= 1024
        return f"{int(value)} B"

    @staticmethod
    def _format_time(value: str) -> str:
        return format_local_time(value)

    @staticmethod
    def _failure_count(output: str, exit_code: int | None) -> int | None:
        if exit_code == 0:
            return 0
        matches = [
            line for line in output.splitlines()
            if re.search(r"(?i)^rsync:.*(?:failed|vanished|permission denied|no such file|cannot)", line)
        ]
        return len(matches) or None

    def _flush_log_pending(self) -> None:
        if self._log_pending.strip():
            self._append_log_line(self._log_pending)
        self._log_pending = ""

    @Slot(str)
    def _status_changed(self, status: str) -> None:
        self.status_label.setText(status)
        if status in {RunStatus.SUCCESS.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value}:
            self._clear_current_file()
            self.statusBar().clearMessage()
        elif status == RunStatus.PREVIEWING.value:
            self.statusBar().showMessage("正在预览…")
        if status != self._last_log_status and status in {
            RunStatus.CONNECTING.value,
            RunStatus.AUTHENTICATING.value,
            RunStatus.PREVIEWING.value,
            RunStatus.TRANSFERRING.value,
        }:
            self._append_log(f"[状态] {status}\n")
            self._last_log_status = status

    @Slot(str)
    def _password_prompt(self, prompt: str) -> None:
        if self._worker is None:
            return
        dialog = PasswordDialog(prompt, self)
        self._worker.answer(dialog.value())

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText(RunStatus.CANCELLED.value)
            self._clear_current_file()
            self.statusBar().clearMessage()

    @Slot(object)
    def _worker_finished(self, result: RunResult) -> None:
        request = self._current_request
        if request is None:
            return
        self._flush_log_pending()
        transferred, deleted = self._sync_counts(result.output)
        total_entries = self._total_entries(result.output)
        regular_files, directories = self._entry_breakdown(result.output)
        changes = self._changes(result.output)
        added = sum(1 for kind, _name in changes if kind == "add")
        updated = sum(1 for kind, _name in changes if kind == "update")
        failures = self._failure_count(result.output, result.exit_code)
        if total_entries is None:
            file_total = "未知"
        elif regular_files is None or directories is None:
            file_total = str(total_entries)
        else:
            file_total = f"{total_entries}（文件 {regular_files}，目录 {directories}）"
        transfer_text = str(transferred) if transferred is not None else "未知"
        delete_text = str(deleted) if deleted is not None else "未知"
        failure_text = str(failures) if failures is not None else "未知"
        self._append_log(
            f"[摘要] 条目总数：{file_total} | 传输文件：{transfer_text}（新增 {added}，更新 {updated}）| 删除：{delete_text} | 失败/跳过：{failure_text}\n"
        )
        self._append_log(
            f"[数据] 文件总大小：{self._format_bytes(self._rsync_stat(result.output, 'Total file size'))} | "
            f"实际传输：{self._format_bytes(self._rsync_stat(result.output, 'Total transferred file size'))} | "
            f"协议收发：{self._format_bytes(self._rsync_stat(result.output, 'Total bytes sent'))} / "
            f"{self._format_bytes(self._rsync_stat(result.output, 'Total bytes received'))}\n"
        )
        self._append_log(
            f"[时间] 开始：{self._format_time(result.start_time)} | 结束：{self._format_time(result.end_time)} | "
            f"耗时：{result.duration:.1f} 秒\n"
        )
        if not changes and not (deleted or 0):
            self._append_log("[提示] 没有需要传输或删除的文件\n")
            self._set_current_file("无文件变更")
        for kind, name in changes[:10]:
            label = {"add": "新增", "update": "更新", "delete": "删除"}[kind]
            self._append_log(f"[变更] {label}：{name}\n")
        if len(changes) > 10:
            self._append_log(f"[变更] 其余 {len(changes) - 10} 项已省略\n")
        self._append_log(f"[结果] {result.status.value}，exit code: {result.exit_code}\n")
        if result.status is RunStatus.FAILED:
            self._append_log(f"{friendly_exit_message(result.exit_code)}\n")
        record = HistoryRecord(self._current_profile_name, request.local_path, request.remote_host, request.remote_path, request.direction.value, request.mode.value, request.dry_run, result.status.value, result.exit_code, result.duration, result.output, start_time=result.start_time, end_time=result.end_time)
        self.db.add_history(record)
        self.db.cleanup_history()
        if request.dry_run and result.status is RunStatus.SUCCESS:
            self._preview_output = result.output
            self._preview_completed = True
            if self._pending_mirror:
                self._pending_mirror = False
                _transferred, deletions = self._sync_counts(result.output)
                deletions = deletions or 0
                text = f"预览完成。\n\n将删除 {deletions} 个文件。\n\n确认执行镜像同步？" if deletions else "预览完成，未发现待删除文件。\n\n确认执行镜像同步？"
                if QMessageBox.warning(self, "确认镜像同步", text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                    self._preview_completed = True
                    self._start_after_cleanup = True
        self._clear_current_file()
        self.status_label.setText(result.status.value)
        self.statusBar().clearMessage()

    def _thread_cleanup(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None
        self._current_request = None
        self.cancel_button.setEnabled(False)
        self.preview_button.setEnabled(True)
        self.sync_button.setEnabled(True)
        if self._start_after_cleanup:
            self._start_after_cleanup = False
            QTimer.singleShot(0, self._start_sync)

    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存日志", str(Path.home() / "nju-hpc-sync.log"), "Log files (*.log);;All files (*)")
        if path:
            Path(path).write_text(self.log.toPlainText(), encoding="utf-8")

    def _open_credentials(self) -> None:
        dialog = CredentialsDialog(self.credential_store, self)
        dialog.credentials_changed.connect(self._refresh_credentials)
        dialog.exec()

    def _open_history(self) -> None:
        HistoryDialog(self.db.list_history(), self).exec()

    def _open_log_management(self) -> None:
        dialog = LogManagementDialog(self.db.get_log_retention_days(), self)
        if dialog.exec() != LogManagementDialog.Accepted:
            return
        days = dialog.retention_days()
        self.db.set_log_retention_days(days)
        deleted = self.db.cleanup_history()
        self.statusBar().showMessage(f"日志保留时间已设为 {days} 天，已清理 {deleted} 条过期记录。", 8000)

    def _open_tutorial(self) -> None:
        UsageDialog(self).exec()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None:
            self._worker.cancel()
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(3000)
        self.db.close()
        event.accept()
