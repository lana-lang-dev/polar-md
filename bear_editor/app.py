from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from .storage import Storage
from .window import MainWindow


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Bear Markdown — native Python desktop editor")
    parser.add_argument("file",nargs="?",help="Markdown document to open")
    parser.add_argument("--data-dir",type=Path,help="Override local recovery storage directory")
    args=parser.parse_args(argv)
    app=QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Bear Markdown")
    app.setOrganizationName("BearMarkdown")
    app.setApplicationVersion("2.0.0")
    directory=args.data_dir or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    storage=Storage(directory)
    try:
        window=MainWindow(storage,args.file or "")
        window.show()
        return app.exec()
    finally:
        storage.close()
