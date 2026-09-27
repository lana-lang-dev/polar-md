import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication

@pytest.fixture(scope="session", autouse=True)
def app():
    application = QApplication.instance() or QApplication([])
    yield application

@pytest.fixture
def window(tmp_path, app):
    from bear_editor.storage import Storage
    from bear_editor.window import MainWindow
    storage = Storage(tmp_path / "state")
    widget = MainWindow(storage)
    widget.show()
    app.processEvents()
    yield widget
    widget.autosave_timer.stop()
    widget.snapshot_timer.stop()
    widget.hide()
    widget.deleteLater()
    app.processEvents()
    storage.close()
