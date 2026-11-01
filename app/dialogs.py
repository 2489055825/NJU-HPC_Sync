from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .credential_store import CredentialStore
from .host_selector import SshHostSelector
from .models import Credential, Direction, Profile, SyncMode, format_local_time
from .paths import normalize_directory_path, normalize_local_path
from .ssh_config_editor import config_snapshot, load_ssh_config_state, save_ssh_config, ssh_config_path, validate_ssh_config
from .totp import TotpConfig, generate_code, remaining_seconds


class ProfileDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, profile: Profile | None = None, credential_names: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑 Profile" if profile else "新建 Profile")
        self.setMinimumWidth(480)
        self.profile = profile
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.name = QLineEdit(profile.name if profile else "")
        self.local = QLineEdit(profile.local_path if profile else "")
        local_row = QHBoxLayout()
        local_row.setContentsMargins(0, 0, 0, 0)
        local_row.setSpacing(8)
        local_row.addWidget(self.local)
        browse = QPushButton("选择")
        browse.clicked.connect(self._browse)
        local_row.addWidget(browse)
        local_widget = QWidget()
        local_widget.setLayout(local_row)
        self.host_selector = SshHostSelector()
        self.host = self.host_selector.combo
        if profile:
            self.host_selector.set_host(profile.remote_host)
        self.remote = QLineEdit(profile.remote_path if profile else "")
        self.credential = QComboBox()
        self.credential.addItem("（手工输入）", "")
        for name in credential_names or []:
            self.credential.addItem(name, name)
        if profile:
            index = self.credential.findData(profile.credential_name)
            if index >= 0:
                self.credential.setCurrentIndex(index)
        self.direction = QComboBox()
        self.direction.addItem(Direction.UPLOAD.label, Direction.UPLOAD.value)
        self.direction.addItem(Direction.DOWNLOAD.label, Direction.DOWNLOAD.value)
        self.mode = QComboBox()
        self.mode.addItem(SyncMode.NORMAL.label, SyncMode.NORMAL.value)
        self.mode.addItem(SyncMode.MIRROR.label, SyncMode.MIRROR.value)
        if profile:
            self.direction.setCurrentIndex(self.direction.findData(profile.default_direction))
            self.mode.setCurrentIndex(self.mode.findData(profile.default_mode))
        form.addRow("名称", self.name)
        form.addRow("本地目录", local_widget)
        form.addRow("远程 Host", self.host_selector)
        form.addRow("远程目录", self.remote)
        form.addRow("凭据", self.credential)
        form.addRow("默认方向", self.direction)
        form.addRow("默认模式", self.mode)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("目录按内容同步，程序会自动使用 rsync 所需的尾部 /。"))
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择本地目录", self.local.text() or str(Path.home()))
        if path:
            self.local.setText(path)

    def _validate_and_accept(self) -> None:
        if not all([self.name.text().strip(), self.local.text().strip(), self.remote.text().strip()]):
            QMessageBox.warning(self, "信息不完整", "请填写名称、本地目录和远程目录，并选择远程 Host。")
            return
        try:
            self.host_selector.selected_host()
        except ValueError as exc:
            QMessageBox.warning(self, "请选择远程 Host", str(exc))
            return
        self.accept()

    def result_profile(self) -> Profile:
        return Profile(
            id=self.profile.id if self.profile else None,
            name=self.name.text().strip(),
            local_path=normalize_directory_path(normalize_local_path(self.local.text())),
            remote_host=self.host_selector.selected_host(),
            remote_path=normalize_directory_path(self.remote.text()),
            credential_name=self.credential.currentData() or "",
            default_direction=self.direction.currentData(),
            default_mode=self.mode.currentData(),
        )


class HostConfigDialog(QDialog):
    config_saved = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Host 管理[*]")
        self.resize(780, 600)
        self.config_path = ssh_config_path()
        self._snapshot: str | None = None
        self.path_label = QLabel(f"主配置文件：{self.config_path}")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.setPlaceholderText("例如：\nHost hpc-alias\n    HostName cluster.example.com\n    User researcher")
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.reload_button = QPushButton("重新加载")
        self.save_button = QPushButton("保存")
        self.close_button = QPushButton("关闭")
        self.reload_button.clicked.connect(self._reload)
        self.save_button.clicked.connect(self._save)
        self.close_button.clicked.connect(self.reject)
        self._load_from_disk()
        self.editor.document().modificationChanged.connect(self._update_title)
        buttons = QHBoxLayout()
        buttons.addWidget(self.reload_button)
        buttons.addStretch()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.path_label)
        layout.addWidget(QLabel("只编辑主配置文件；Include 引用的子文件不会被修改。保存前会创建 config.bak。"))
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

    def _update_title(self, modified: bool) -> None:
        self.setWindowModified(modified)

    def _load_from_disk(self) -> None:
        try:
            content, snapshot = load_ssh_config_state(self.config_path)
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(self, "无法读取 SSH 配置", f"无法读取 {self.config_path}，请检查文件权限和 UTF-8 编码。")
            self.status_label.setText("读取失败，编辑内容未改变；请修复文件后重新加载。")
            self.editor.setReadOnly(True)
            self.save_button.setEnabled(False)
            return
        self._snapshot = snapshot
        self.editor.setReadOnly(False)
        self.save_button.setEnabled(True)
        self.editor.setPlainText(content)
        self.editor.document().setModified(False)
        if self.config_path.exists():
            self.status_label.setText("已加载主配置文件。")
        else:
            self.status_label.setText("主配置文件不存在，保存时会创建该文件。")

    def _reload(self) -> None:
        if self.editor.document().isModified():
            answer = QMessageBox.question(
                self,
                "放弃未保存修改？",
                "编辑器中有未保存的修改，重新加载会丢弃这些修改。是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self._load_from_disk()

    def _save(self) -> None:
        text = self.editor.toPlainText()
        errors = validate_ssh_config(text)
        if errors:
            QMessageBox.warning(self, "无法保存 SSH 配置", "\n".join(errors))
            return
        try:
            current_snapshot = config_snapshot(self.config_path)
        except OSError as exc:
            QMessageBox.warning(self, "无法检查 SSH 配置", f"无法检查 {self.config_path}：{exc}")
            return
        changed = "文件已被其他程序修改，继续保存会覆盖外部修改。\n" if current_snapshot != self._snapshot else ""
        backup_path = self.config_path.with_name("config.bak")
        answer = QMessageBox.question(
            self,
            "确认保存 SSH 配置",
            f"{changed}保存文件：{self.config_path}\n备份文件：{backup_path}\n"
            "首次创建时无旧文件可备份。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            backup = save_ssh_config(text, self.config_path, expected_snapshot=current_snapshot)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "无法保存 SSH 配置", str(exc))
            return
        self._snapshot = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.editor.document().setModified(False)
        backup_text = f"备份：{backup}" if backup is not None else "首次创建文件，暂无旧文件备份。"
        self.status_label.setText(f"保存成功。{backup_text}")
        self.config_saved.emit()
        QMessageBox.information(self, "保存成功", f"SSH 主配置已保存。\n{backup_text}")


class PasswordDialog(QDialog):
    def __init__(self, prompt: str = "Password:", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("SSH 认证")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("固定密码 + 空格 + 当前 TOTP")
        self.prompt_label = QLabel(prompt.strip())
        self.prompt_label.setWordWrap(True)
        self.prompt_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.prompt_label.setToolTip(prompt.strip())
        self.prompt_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.prompt_label.setMinimumWidth(0)
        self.prompt_label.setMaximumWidth(520)
        form = QFormLayout(self)
        form.addRow("认证提示", self.prompt_label)
        form.addRow("完整密码", self.password)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.password.setFocus()

    def value(self) -> str | None:
        if self.exec() != QDialog.Accepted:
            self.password.clear()
            return None
        value = self.password.text()
        self.password.clear()
        return value


class CredentialsDialog(QDialog):
    credentials_changed = Signal()

    def __init__(self, store: CredentialStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("凭据管理")
        self.setMinimumSize(650, 410)
        self.store = store
        self.credentials = self.store.load()
        self._editing_name = ""
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load_item)
        self.add_button = QPushButton("新建")
        self.delete_button = QPushButton("删除")
        self.add_button.clicked.connect(self._new)
        self.delete_button.clicked.connect(self._delete)
        list_buttons = QHBoxLayout()
        list_buttons.addWidget(self.add_button)
        list_buttons.addWidget(self.delete_button)
        left = QVBoxLayout()
        left.addWidget(self.list)
        left.addLayout(list_buttons)
        self.name = QLineEdit()
        self.static_password = QLineEdit()
        self.totp_secret = QLineEdit()
        for field in (self.static_password, self.totp_secret):
            field.setEchoMode(QLineEdit.Password)
        self.algorithm = QComboBox()
        self.algorithm.addItems(["SHA1", "SHA256", "SHA512"])
        self.period = QSpinBox()
        self.period.setRange(5, 3600)
        self.period.setValue(30)
        self.digits = QSpinBox()
        self.digits.setRange(4, 10)
        self.digits.setValue(6)
        self.code = QLabel("未配置")
        self.remaining = QLabel("")
        show_password = QPushButton("显示")
        show_secret = QPushButton("显示")
        show_password.clicked.connect(lambda: self._toggle(self.static_password, show_password))
        show_secret.clicked.connect(lambda: self._toggle(self.totp_secret, show_secret))
        password_row = QHBoxLayout()
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.setSpacing(8)
        password_row.addWidget(self.static_password)
        password_row.addWidget(show_password)
        password_widget = QWidget()
        password_widget.setLayout(password_row)
        secret_row = QHBoxLayout()
        secret_row.setContentsMargins(0, 0, 0, 0)
        secret_row.setSpacing(8)
        secret_row.addWidget(self.totp_secret)
        secret_row.addWidget(show_secret)
        secret_widget = QWidget()
        secret_widget.setLayout(secret_row)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.addRow("名称", self.name)
        form.addRow("固定密码", password_widget)
        form.addRow("TOTP Secret", secret_widget)
        form.addRow("Algorithm", self.algorithm)
        form.addRow("Period（秒）", self.period)
        form.addRow("Digits", self.digits)
        code_row = QHBoxLayout()
        code_row.addWidget(self.code)
        code_row.addWidget(self.remaining)
        code_widget = QWidget()
        code_widget.setLayout(code_row)
        form.addRow("当前验证码", code_widget)
        save = QPushButton("保存")
        save.clicked.connect(self._save)
        form.addRow(save)
        right = QVBoxLayout()
        right.addLayout(form)
        root = QHBoxLayout(self)
        root.addLayout(left, 1)
        root.addLayout(right, 2)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_code)
        self.timer.start(1000)
        self._refresh_list()

    @staticmethod
    def _toggle(field: QLineEdit, button: QPushButton) -> None:
        visible = field.echoMode() == QLineEdit.Password
        field.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        button.setText("隐藏" if visible else "显示")

    def _refresh_list(self, selected_name: str | None = None) -> None:
        requested_selection = selected_name is not None
        current_item = self.list.currentItem()
        if selected_name is None and current_item is not None:
            selected_name = current_item.text()
        current_row = self.list.currentRow()
        scroll_position = self.list.verticalScrollBar().value()
        self.list.clear()
        for name in sorted(self.credentials, key=str.casefold):
            self.list.addItem(QListWidgetItem(name))
        if self.list.count():
            items = self.list.findItems(selected_name, Qt.MatchExactly) if selected_name else []
            selected_item = items[0] if items else self.list.item(min(max(current_row, 0), self.list.count() - 1))
            self.list.setCurrentItem(selected_item)
            if requested_selection:
                QTimer.singleShot(0, lambda: self.list.scrollToItem(selected_item))
            else:
                QTimer.singleShot(
                    0,
                    lambda: self.list.verticalScrollBar().setValue(
                        min(scroll_position, self.list.verticalScrollBar().maximum())
                    ),
                )
        else:
            self._new()

    def _new(self) -> None:
        self.list.clearSelection()
        self._editing_name = ""
        self.name.clear()
        self.static_password.clear()
        self.totp_secret.clear()
        self.algorithm.setCurrentText("SHA1")
        self.period.setValue(30)
        self.digits.setValue(6)
        self.code.setText("未配置")
        self.remaining.clear()

    def _load_item(self, item: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if item is None:
            return
        credential = self.credentials.get(item.text())
        if credential is None:
            return
        self.name.setText(credential.name)
        self._editing_name = credential.name
        self.static_password.setText(credential.static_password)
        self.totp_secret.setText(credential.totp_secret)
        self.algorithm.setCurrentText(credential.totp_algorithm)
        self.period.setValue(credential.totp_period)
        self.digits.setValue(credential.totp_digits)
        self._refresh_code()

    def _save(self) -> None:
        try:
            credential = Credential(self.name.text().strip(), self.static_password.text(), self.totp_secret.text(), self.algorithm.currentText(), self.period.value(), self.digits.value())
            credential.validate()
            if not credential.totp_secret:
                raise ValueError("TOTP Secret 不能为空")
            if self._editing_name and self._editing_name != credential.name:
                self.credentials.pop(self._editing_name, None)
            self.credentials[credential.name] = credential
            self._editing_name = credential.name
            self.store.save(self.credentials)
            self._refresh_list(credential.name)
            self.credentials_changed.emit()
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "无法保存凭据", str(exc))

    def _delete(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        if QMessageBox.question(self, "删除凭据", f"确定删除凭据“{item.text()}”？") != QMessageBox.Yes:
            return
        self.credentials.pop(item.text(), None)
        self.store.save(self.credentials)
        self._refresh_list()
        self.credentials_changed.emit()

    def _refresh_code(self) -> None:
        secret = self.totp_secret.text().strip()
        if not secret:
            self.code.setText("未配置")
            self.remaining.clear()
            return
        try:
            config = TotpConfig(secret, self.algorithm.currentText(), self.period.value(), self.digits.value())
            code, remaining = generate_code(config), remaining_seconds(config.period)
            self.code.setText(code)
            self.remaining.setText(f"剩余 {remaining} 秒")
        except ValueError as exc:
            self.code.setText("无效")
            self.remaining.setText(str(exc))


class HistoryDialog(QDialog):
    def __init__(self, records: list, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("同步历史")
        self.resize(1000, 570)
        self.setMinimumSize(800, 500)
        from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem, QPlainTextEdit

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["时间", "Profile", "方向", "模式", "类型", "状态", "耗时"])
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        header = self.table.horizontalHeader()
        for column, width in {0: 230, 2: 112, 3: 90, 4: 66, 5: 90, 6: 72}.items():
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.records = records
        for record in records:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [format_local_time(record.start_time), record.profile_name or "临时同步", "Local → NJU-HPC" if record.direction == "upload" else "NJU-HPC → Local", "强制镜像" if record.mode == "mirror" else "普通同步", "预览" if record.dry_run else "执行", record.status, f"{record.duration:.1f}s"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(row, column, item)
        self.table.currentCellChanged.connect(self._show_detail)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 3)
        layout.addWidget(QLabel("详细日志"))
        layout.addWidget(self.detail, 2)
        if records:
            self.table.selectRow(0)
            self._show_detail(0, 0, 0, 0)

    def _show_detail(self, current: int, _column: int, _previous: int, _previous_column: int) -> None:
        if 0 <= current < len(self.records):
            record = self.records[current]
            self.detail.setPlainText(record.log or "（无日志）")


class LogManagementDialog(QDialog):
    def __init__(self, retention_days: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("日志管理")
        self.setMinimumWidth(460)
        self.retention = QSpinBox()
        self.retention.setRange(1, 36500)
        self.retention.setValue(retention_days)
        self.retention.setSuffix(" 天")
        form = QFormLayout()
        form.addRow("日志保留时间", self.retention)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("同步历史及其详细日志会自动删除超过保留时间的记录。"))
        layout.addWidget(buttons)

    def retention_days(self) -> int:
        return self.retention.value()


class UsageDialog(QDialog):
    """Short, copyable explanation of the command flow used by the GUI."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("使用教程")
        self.resize(760, 520)
        text = (
            "基本原理\n"
            "本软件不实现 SFTP；它只组装参数并调用系统 rsync，rsync 再使用 OpenSSH 和 ~/.ssh/config。\n\n"
            "上传（Local -> NJU-HPC）:\n"
            "rsync -avzP --stats --itemize-changes -- /本地目录/ nju:/远程目录/\n\n"
            "下载（NJU-HPC -> Local）:\n"
            "rsync -avzP --stats --itemize-changes -- nju:/远程目录/ /本地目录/\n\n"
            "镜像预览（会检查待删除内容）:\n"
            "rsync -avzP --stats --itemize-changes --delete --dry-run -- /本地目录/ nju:/远程目录/\n\n"
            "使用方法\n"
            "1. 管理 -> 凭据管理：自动 TOTP 可保存凭据；也可以选择手工输入完整密码。\n"
            "2. 填写本地目录和远程目录，从下拉列表选择远程 Host；列表读取 ~/.ssh/config 及 Include 文件。修改 SSH 配置后点击“刷新”，或通过“管理 -> Host 管理”编辑主配置文件。凭据只负责认证。\n"
            "3. 选择方向和模式，先点“预览”；镜像同步确认删除数量后再执行。\n"
            "4. 目录会自动补齐尾部 /，表示同步目录内容。"
        )
        content = QPlainTextEdit()
        content.setReadOnly(True)
        content.setPlainText(text)
        content.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(content)
        layout.addWidget(buttons)
