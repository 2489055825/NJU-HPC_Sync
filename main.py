from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("NJU-HPC Sync")
    app.setOrganizationName("NJU-HPC")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "app" / "assets" / "nju-hpc-sync.png")))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
