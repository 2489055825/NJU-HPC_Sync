from __future__ import annotations

import sys
from pathlib import Path

def main() -> int:
    if len(sys.argv) > 1:
        if sys.argv[1] == "--_worker":
            from app.worker import execute_task
            return execute_task(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
        from app.cli import main as cli_main
        return cli_main(sys.argv[1:])

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from app.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("NJU-HPC Sync")
    app.setOrganizationName("NJU-HPC")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "app" / "assets" / "nju-hpc-sync.png")))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
