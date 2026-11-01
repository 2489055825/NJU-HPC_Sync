from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .ssh_config import read_ssh_hosts


class SshHostSelector(QWidget):
    """Select a configured alias and retain unavailable Profile targets visibly."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._hosts: list[str] = []
        self._warnings: list[str] = []
        self.combo = QComboBox()
        self.combo.setEditable(False)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(12)
        self.combo.setToolTip(f"从 {Path.home() / '.ssh/config'} 及 Include 文件读取 SSH Host 别名")
        self.combo.currentIndexChanged.connect(self._update_message)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setToolTip("重新读取 SSH 配置，保留当前选择")
        self.refresh_button.clicked.connect(self.refresh)
        self.message = QLabel()
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.combo, 1)
        row.addWidget(self.refresh_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(row)
        layout.addWidget(self.message)
        self.refresh()

    def refresh(self) -> None:
        selected = self.combo.currentData() or ""
        self._hosts, self._warnings = read_ssh_hosts()
        self.combo.clear()
        self.combo.addItem("请选择 SSH Host", "")
        for host in self._hosts:
            self.combo.addItem(host, host)
        self.set_host(selected)

    def set_host(self, host: str) -> None:
        # Only the current unavailable Profile target belongs in the list.
        for index in range(self.combo.count() - 1, 0, -1):
            if self.combo.itemData(index) not in self._hosts:
                self.combo.removeItem(index)
        index = self.combo.findData(host)
        if host and index < 0:
            self.combo.addItem(f"{host}（配置中不存在）", host)
            index = self.combo.count() - 1
            self.combo.model().item(index).setEnabled(False)
        self.combo.setCurrentIndex(max(index, 0))
        self._update_message()

    def selected_host(self) -> str:
        host = self.combo.currentData() or ""
        if host not in self._hosts:
            raise ValueError("请选择 SSH 配置中的有效远程 Host；修改 ~/.ssh/config 后可点击“刷新”。")
        return host

    def _update_message(self) -> None:
        messages = list(self._warnings)
        host = self.combo.currentData() or ""
        if host and host not in self._hosts:
            messages.insert(0, f"原 Host“{host}”已不在 SSH 配置中，请重新选择或修改配置后刷新。")
        if not self._hosts:
            messages.append(f"未找到可选 Host，请在 {Path.home() / '.ssh/config'} 中添加具体 Host 别名后刷新。")
        self.message.setText("\n".join(messages))
        self.message.setVisible(bool(messages))
