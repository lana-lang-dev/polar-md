from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor, QTextDocument, QColor, QTextCharFormat, QKeyEvent
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QCheckBox, QComboBox, QPushButton, QDialogButtonBox, QListWidget, QListWidgetItem,
    QPlainTextEdit, QGridLayout, QTextEdit, QWidget)

from .models import Preferences, DocumentState
from .markdown import front_matter, title_for, image_references
from .files import read_markdown


def dialog_buttons(dialog: QDialog, layout, accept="Apply"):
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText(accept)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return buttons


class PreferencesDialog(QDialog):
    def __init__(self, preferences: Preferences, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Typography & Settings")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.controls = {}
        for key, label, choices in [("font", "Font", ["Sans", "Serif", "Mono"]), ("size", "Text size", ["Small", "Medium", "Large"]), ("width", "Line width", ["Narrow", "Normal", "Wide"]), ("spacing", "Line spacing", ["Compact", "Normal", "Relaxed"])]:
            control = QComboBox()
            control.addItems(choices)
            control.setCurrentText(getattr(preferences, key))
            self.controls[key] = control
            form.addRow(label, control)
        layout.addLayout(form)
        for key, label in [("dark", "Dark mode"), ("bubble", "Floating selection toolbar"), ("strike_tasks", "Strike completed tasks"), ("heading_title", "Use first heading as app title"), ("typewriter", "Typewriter mode"), ("auto_save_disk", "Auto-save opened files")]:
            control = QCheckBox(label)
            control.setChecked(getattr(preferences, key))
            self.controls[key] = control
            layout.addWidget(control)
        hint = QLabel("Local recovery is always enabled. Disk auto-save is off by default.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        dialog_buttons(self, layout)

    def preferences(self):
        return Preferences(**{key: control.isChecked() if isinstance(control, QCheckBox) else control.currentText() for key, control in self.controls.items()})


class CommandPalette(QDialog):
    def __init__(self, commands: list[tuple[str, callable, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Commands")
        self.resize(480, 380)
        self.commands = commands
        self.chosen = None
        layout = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText("What would you like to do?")
        self.query.setAccessibleName("Find command")
        self.items = QListWidget()
        layout.addWidget(self.query)
        layout.addWidget(self.items)
        self.query.textChanged.connect(self.filter)
        self.items.itemActivated.connect(self.activate)
        self.filter("")
        self.query.setFocus()

    def filter(self, query):
        self.items.clear()
        def fuzzy(label):
            iterator = iter(label.casefold())
            return all(any(char == candidate for candidate in iterator) for char in query.casefold())
        for label, action, shortcut in self.commands:
            if fuzzy(label):
                item = QListWidgetItem(f"{label}    {shortcut}")
                item.setData(Qt.ItemDataRole.UserRole, label)
                self.items.addItem(item)
        if self.items.count():
            self.items.setCurrentRow(0)

    def activate(self, item):
        if item:
            self.chosen = next(action for label, action, _ in self.commands if label == item.data(Qt.ItemDataRole.UserRole))
            self.accept()

    def keyPressEvent(self, event):
        if event.key() in {Qt.Key.Key_Up, Qt.Key.Key_Down}:
            count = self.items.count()
            if count:
                self.items.setCurrentRow((self.items.currentRow() + (1 if event.key() == Qt.Key.Key_Down else -1)) % count)
        elif event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.activate(self.items.currentItem())
        else:
            super().keyPressEvent(event)


class ImportDialog(QDialog):
    def __init__(self, paths: list[Path], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Apple Notes Markdown")
        self.resize(440, 310)
        self.paths = paths
        self.insert = False
        layout = QVBoxLayout(self)
        self.chooser = QComboBox()
        self.chooser.addItems([p.name for p in paths])
        layout.addWidget(QLabel("Choose one document"))
        layout.addWidget(self.chooser)
        self.details = QLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.normalize = QCheckBox("Normalize supported Markdown blocks")
        self.copy = QCheckBox("Copy attachments into local recovery storage")
        self.copy.setChecked(True)
        layout.addWidget(self.normalize)
        layout.addWidget(self.copy)
        self.buttons = dialog_buttons(self, layout, "Open document")
        insert = self.buttons.addButton("Insert contents", QDialogButtonBox.ButtonRole.ActionRole)
        insert.clicked.connect(self.insert_contents)
        self.chooser.currentIndexChanged.connect(self.preview)
        self.preview()

    def preview(self):
        try:
            path = self.paths[self.chooser.currentIndex()]
            markdown = read_markdown(path)
            metadata = front_matter(markdown)[2]
            self.details.setText(f"{title_for(markdown, path.name)}\n\n{len(markdown.split()):,} words · {len(image_references(markdown))} images\nFront matter: {'found' if metadata or front_matter(markdown)[0] else 'not found'}")
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        except (OSError, ValueError) as error:
            self.details.setText(str(error))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def insert_contents(self):
        self.insert = True
        self.accept()

    @property
    def path(self):
        return self.paths[self.chooser.currentIndex()]


class TablePicker(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Insert table")
        self.dimensions = (3, 4)
        layout = QVBoxLayout(self)
        self.label = QLabel("3 × 4 · columns × rows")
        layout.addWidget(self.label)
        grid = QGridLayout()
        grid.setSpacing(3)
        for row in range(1, 7):
            for column in range(1, 9):
                button = QPushButton("")
                button.setFixedSize(28, 28)
                button.setAccessibleName(f"Insert {column} by {row} table")
                button.setToolTip(f"{column} × {row}")
                button.clicked.connect(lambda checked=False, c=column, r=row:self.choose(c, r))
                grid.addWidget(button, row - 1, column - 1)
        layout.addLayout(grid)

    def choose(self, columns, rows):
        self.dimensions = (columns, rows)
        self.accept()


class SearchDialog(QDialog):
    def __init__(self, editor: QTextEdit, backup, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.backup = backup
        self.setWindowTitle("Find in document")
        self.resize(380, 240)
        self.matches = []
        self.index = 0
        layout = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Find")
        self.query.setAccessibleName("Search text")
        self.replacement = QLineEdit()
        self.replacement.setPlaceholderText("Replace with")
        self.replacement.setAccessibleName("Replacement text")
        layout.addWidget(self.query)
        row = QHBoxLayout()
        self.case = QCheckBox("Match case")
        self.whole = QCheckBox("Whole word")
        row.addWidget(self.case)
        row.addWidget(self.whole)
        layout.addLayout(row)
        self.count = QLabel("0 matches")
        layout.addWidget(self.count)
        navigation = QHBoxLayout()
        for text, delta in [("Previous", -1), ("Next", 1)]:
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, d=delta:self.move(d))
            navigation.addWidget(button)
        layout.addLayout(navigation)
        layout.addWidget(self.replacement)
        replace = QPushButton("Replace all")
        replace.clicked.connect(self.replace_all)
        layout.addWidget(replace)
        close = QPushButton("Done")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.query.textChanged.connect(self.find)
        self.query.returnPressed.connect(lambda:self.move(1))
        self.case.toggled.connect(self.find)
        self.whole.toggled.connect(self.find)
        self.finished.connect(lambda _:self.editor.setExtraSelections([]))
        self.finished.connect(lambda _:self.editor.setFocus())

    def find(self):
        self.matches = []
        query = self.query.text()
        flags = QTextDocument.FindFlag(0)
        if self.case.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords
        cursor = QTextCursor(self.editor.document())
        if query:
            while True:
                cursor = self.editor.document().find(query, cursor, flags)
                if cursor.isNull():
                    break
                self.matches.append(cursor)
        self.index = 0
        self.show_match()

    def move(self, delta):
        if self.matches:
            self.index = (self.index + delta) % len(self.matches)
        self.show_match()

    def show_match(self):
        selections = []
        for i, cursor in enumerate(self.matches):
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(QColor("#e9bb54" if i == self.index else "#ebd988"))
            selection.format.setForeground(QColor("#29292d"))
            selections.append(selection)
        self.editor.setExtraSelections(selections)
        self.count.setText(f"{self.index + 1 if self.matches else 0} / {len(self.matches)} matches")
        if self.matches:
            self.editor.setTextCursor(self.matches[self.index])
            self.editor.ensureCursorVisible()

    def replace_all(self):
        if not self.matches:
            return
        self.backup()
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        for match in reversed(self.matches):
            match.insertText(self.replacement.text())
        cursor.endEditBlock()
        self.find()


class HistoryDialog(QDialog):
    def __init__(self, versions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Version history")
        self.resize(640, 500)
        self.chosen = None
        self.duplicate = False
        self.versions = versions
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        for _, timestamp, document in versions:
            self.list.addItem(f"{datetime.fromtimestamp(timestamp):%b %d, %Y · %H:%M:%S}    {len(document.markdown.split()):,} words")
        if not versions:
            layout.addWidget(QLabel("No versions yet. Snapshots are saved every minute and before document changes."))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Version preview")
        layout.addWidget(self.list)
        layout.addWidget(self.preview)
        self.list.currentRowChanged.connect(lambda row:self.preview.setPlainText(versions[row][2].markdown) if row >= 0 else None)
        row = QHBoxLayout()
        for text, duplicate in [("Restore", False), ("Duplicate as new draft", True)]:
            button = QPushButton(text)
            button.setEnabled(bool(versions))
            button.clicked.connect(lambda checked=False, d=duplicate:self.choose(d))
            row.addWidget(button)
        layout.addLayout(row)
        if versions:
            self.list.setCurrentRow(0)

    def choose(self, duplicate):
        index = self.list.currentRow()
        if index >= 0:
            self.chosen = self.versions[index][2]
            self.duplicate = duplicate
            self.accept()
