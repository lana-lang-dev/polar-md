from __future__ import annotations

import base64
import copy
import html
import os
import re
import shutil
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, QUrl, QSize
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QImage, QKeySequence,
    QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat,
    QTextImageFormat, QTextLength, QTextTableFormat, QCloseEvent)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QToolButton, QPushButton, QLabel, QSplitter, QPlainTextEdit, QFrame,
    QFileDialog, QMessageBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
    QComboBox, QMenu, QListWidget, QInputDialog, QCheckBox, QSizePolicy)

from .dialogs import (PreferencesDialog, CommandPalette, ImportDialog, TablePicker,
    SearchDialog, HistoryDialog, dialog_buttons)
from .editor import RichEditor, AssetDocument
from .files import (read_markdown, atomic_write, resolve_attachment, copy_asset, unique_destination,
    file_digest, safe_url, write_bundle, MARKDOWN_SUFFIXES, IMAGE_SUFFIXES)
from .markdown import MarkdownSession, DIALECT, front_matter, title_for, image_references
from .models import DocumentState
from .serialization import HIGHLIGHT_PROPERTY, native_markdown, tables
from .storage import Storage


class MainWindow(QMainWindow):
    def __init__(self, storage: Storage, initial_path: str = ""):
        super().__init__()
        self.storage = storage
        self.preferences = storage.preferences()
        self.state = DocumentState()
        self.session = MarkdownSession()
        self.loading = False
        self.pending_rich = False
        self.pending_source = False
        self.digest = ""
        self.focus_mode = False
        self.recovered: DocumentState | None = None
        self.search_dialog = None
        self.resize(1120, 820)
        self.setMinimumSize(580, 420)
        self.setWindowTitle("Untitled — Bear Markdown")
        self.build_ui()
        self.build_actions()
        self.connect_signals()
        self.loading = True
        self.apply_preferences()
        self.loading = False
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(600)
        self.autosave_timer.timeout.connect(self.autosave)
        self.snapshot_timer = QTimer(self)
        self.snapshot_timer.setInterval(60_000)
        self.snapshot_timer.timeout.connect(lambda:self.safely(self.snapshot))
        self.snapshot_timer.start()
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self.toast.hide)
        saved = storage.load_draft()
        if saved and saved.dirty:
            self.recovered = saved
            self.install(DocumentState(), backup=False)
            self.recovery_bar.show()
            self.editor.setReadOnly(True)
            self.source.setReadOnly(True)
        elif saved:
            self.install(saved, backup=False)
        else:
            self.install(DocumentState(), backup=False)
        if initial_path:
            QTimer.singleShot(0, lambda:self.safely(lambda:self.open_path(Path(initial_path))))

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.topbar = QWidget()
        self.topbar.setObjectName("topbar")
        top = QHBoxLayout(self.topbar)
        top.setContentsMargins(14, 8, 14, 8)
        self.file_button = QToolButton()
        self.file_button.setText("File")
        self.file_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.file_button.setAccessibleName("File menu")
        top.addWidget(self.file_button)
        self.title_button = QPushButton("Untitled")
        self.title_button.setObjectName("title")
        self.title_button.setToolTip("Edit document title")
        self.title_button.clicked.connect(self.rename_title)
        top.addWidget(self.title_button)
        self.status = QLabel("Saved locally")
        self.status.setObjectName("status")
        top.addWidget(self.status)
        top.addStretch(1)
        self.mode_buttons = {}
        for mode in ["Write", "Markdown", "Split"]:
            button = QToolButton()
            button.setText(mode)
            button.setCheckable(True)
            button.setAccessibleName(f"{mode} mode")
            button.clicked.connect(lambda checked=False, m=mode:self.safely(lambda:self.set_mode(m)))
            self.mode_buttons[mode] = button
            top.addWidget(button)
        top.addStretch(1)
        self.more_button = QToolButton()
        self.more_button.setText("•••")
        self.more_button.setAccessibleName("Commands")
        self.more_button.setToolTip("Commands (Ctrl/Cmd+K)")
        self.more_button.clicked.connect(self.command_palette)
        top.addWidget(self.more_button)
        layout.addWidget(self.topbar)
        self.format_toolbar = QToolBar("Formatting")
        self.format_toolbar.setMovable(False)
        self.format_toolbar.setIconSize(QSize(16, 16))
        self.format_toolbar.setObjectName("formatting")
        layout.addWidget(self.format_toolbar)
        self.recovery_bar = QWidget()
        recovery = QHBoxLayout(self.recovery_bar)
        recovery.addWidget(QLabel("An unsaved version of this document was recovered."))
        for label, restore in [("Restore", True), ("Discard", False)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, r=restore:self.safely(lambda:self.recover(r)))
            recovery.addWidget(button)
        self.recovery_bar.hide()
        layout.addWidget(self.recovery_bar)
        self.exit_focus = QPushButton("Exit focus")
        self.exit_focus.clicked.connect(self.toggle_focus)
        self.exit_focus.hide()
        layout.addWidget(self.exit_focus, alignment=Qt.AlignmentFlag.AlignRight)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.rich_pane = QWidget()
        rich_layout = QHBoxLayout(self.rich_pane)
        rich_layout.setContentsMargins(24, 24, 24, 8)
        rich_layout.addStretch(1)
        self.editor = RichEditor()
        self.editor.setMaximumWidth(760)
        self.editor.setMinimumWidth(200)
        rich_layout.addWidget(self.editor, 20)
        rich_layout.addStretch(1)
        self.source = QPlainTextEdit()
        self.source.setAccessibleName("Markdown source")
        self.source.setPlaceholderText("Write Markdown…")
        self.source.setFrameShape(QFrame.Shape.NoFrame)
        self.source.setFont(QFont("Consolas", 11))
        self.source.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.splitter.addWidget(self.rich_pane)
        self.splitter.addWidget(self.source)
        self.splitter.setSizes([600, 600])
        layout.addWidget(self.splitter, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(14, 2, 14, 8)
        self.toast = QLabel()
        self.toast.setObjectName("toast")
        self.toast.setWordWrap(True)
        self.toast.hide()
        footer.addWidget(self.toast, 1)
        footer.addStretch(1)
        self.stats_button = QPushButton("0 words · 0 chars")
        self.stats_button.setObjectName("statistics")
        self.stats_button.clicked.connect(lambda:self.document_info(True))
        footer.addWidget(self.stats_button)
        layout.addLayout(footer)
        self.bubble = QFrame(self.editor.viewport())
        self.bubble.setObjectName("bubble")
        self.bubble_layout = QHBoxLayout(self.bubble)
        self.bubble_layout.setContentsMargins(3, 3, 3, 3)
        self.bubble.hide()

    def action(self, label, callback, shortcut="", checkable=False):
        action = QAction(label, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.setCheckable(checkable)
        action.triggered.connect(lambda checked=False:self.safely(callback))
        self.addAction(action)
        return action

    def build_actions(self):
        self.commands = []
        self.file_menu = QMenu(self)
        self.file_button.setMenu(self.file_menu)
        definitions = [
            ("New document", self.new_document, "Ctrl+N"),
            ("Open Markdown…", self.open_dialog, "Ctrl+O"),
            ("Import Apple Notes Export…", self.import_files, ""),
            ("Import Folder…", self.import_folder, ""),
            ("Save", self.save, "Ctrl+S"),
            ("Save As…", lambda:self.save(True), "Ctrl+Shift+S"),
            ("Export…", self.export_dialog, ""),
            ("Version History", self.history, ""),
            ("Document Info", self.document_info, ""),
            ("Copy as Markdown", self.copy_markdown, ""),
        ]
        for label, callback, shortcut in definitions:
            action = self.action(label, callback, shortcut)
            self.file_menu.addAction(action)
            self.commands.append((label, callback, shortcut))
        self.recent_menu = self.file_menu.addMenu("Recent files")
        self.recent_menu.aboutToShow.connect(self.refresh_recent)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.action("Print / PDF…", self.print_document, "Ctrl+P"))
        self.file_menu.addAction(self.action("Close", self.close, "Ctrl+Q"))
        self.format_actions = {}
        for level in [1, 2, 3]:
            action = self.action(f"H{level}", lambda n=level:self.editor.heading(n), f"Ctrl+Alt+{level}", True)
            self.format_actions[f"h{level}"] = action
            self.format_toolbar.addAction(action)
            self.commands.append((f"Heading {level}", lambda n=level:self.editor.heading(n), f"Ctrl+Alt+{level}"))
        self.format_toolbar.addSeparator()
        for key, label, shortcut in [("bold", "Bold", "Ctrl+B"), ("italic", "Italic", "Ctrl+I"), ("strike", "Strike", "Ctrl+Shift+X"), ("highlight", "Highlight", "Ctrl+Shift+H"), ("code", "Code", "")]:
            action = self.action(label, lambda k=key:self.editor.mark(k), shortcut, True)
            self.format_actions[key] = action
            self.format_toolbar.addAction(action)
            button = QToolButton()
            button.setDefaultAction(action)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.bubble_layout.addWidget(button)
        self.format_toolbar.addSeparator()
        for label, callback in [("• List", lambda:self.editor.list_format("bullet")), ("1. List", lambda:self.editor.list_format("ordered")), ("☐ Task", lambda:self.editor.list_format("task")), ("Quote", self.editor.quote), ("Link", self.link_dialog), ("Image", self.image_dialog), ("Table", self.table_dialog), ("Rule", lambda:self.editor.textCursor().insertHtml("<hr>")), ("Undo", self.editor.undo), ("Redo", self.editor.redo)]:
            action = self.action(label, callback)
            self.format_toolbar.addAction(action)
            if label == "Link":
                button = QToolButton()
                button.setDefaultAction(action)
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                self.bubble_layout.addWidget(button)
        for button in self.format_toolbar.findChildren(QToolButton):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        extras = [
            ("Find in document", self.search, "Ctrl+F"),
            ("Document outline", self.outline, "Ctrl+Shift+O"),
            ("Typography & Settings", self.settings, ""),
            ("Toggle dark mode", self.toggle_dark, ""),
            ("Focus mode", self.toggle_focus, "Ctrl+Shift+F"),
            ("Insert image", self.image_dialog, ""),
            ("Insert table", self.table_dialog, ""),
            ("Insert link", self.link_dialog, ""),
            ("Code block", self.code_dialog, ""),
            ("Toggle task", lambda:self.editor.list_format("task"), ""),
            ("Markdown view", lambda:self.set_mode("Markdown" if self.state.mode != "Markdown" else "Write"), ""),
            ("Split view", lambda:self.set_mode("Split" if self.state.mode != "Split" else "Write"), ""),
        ]
        for label, callback, shortcut in extras:
            self.action(label, callback, shortcut)
            self.commands.append((label, callback, shortcut))
        self.action("Commands", self.command_palette, "Ctrl+K")
        # Standard editor undo/redo shortcuts remain handled by QTextEdit/QPlainTextEdit.

    def connect_signals(self):
        self.editor.textChanged.connect(self.rich_changed)
        self.source.textChanged.connect(self.source_changed)
        self.editor.cursorPositionChanged.connect(self.update_selection)
        self.editor.selectionChanged.connect(self.update_selection)
        self.editor.files_dropped.connect(lambda paths:self.safely(lambda:self.drop_paths(paths)))
        self.editor.image_pasted.connect(lambda image:self.safely(lambda:self.paste_image(image)))
        self.editor.locate_image.connect(lambda ref:self.safely(lambda:self.locate_image(ref)))
        self.editor.edit_image.connect(self.image_dialog)
        self.editor.link_requested.connect(self.link_dialog)
        self.editor.table_requested.connect(self.table_dialog)
        self.editor.notice.connect(self.notify)

    def safely(self, callback):
        try:
            return callback()
        except Exception as error:
            self.status.setText("Error")
            self.notify(str(error) or "This action could not be completed.")
            return None

    def notify(self, message):
        self.toast.setText(str(message))
        self.toast.show()
        self.toast_timer.start(6500)

    def load_rich(self, markdown, reset_undo=False):
        self.loading = True
        try:
            old_cursor = self.editor.textCursor().position()
            old_scroll = self.editor.verticalScrollBar().value()
            document = self.editor.document()
            document.note_path = self.state.path
            document.mappings = self.state.attachments
            document.failed.clear()
            rendered = self.session.prepare(markdown)
            document.setMarkdown(rendered, DIALECT)
            self.render_highlights()
            self.style_document()
            self.session.baseline(document)
            cursor = self.editor.textCursor()
            cursor.setPosition(min(old_cursor, max(0, document.characterCount() - 1)))
            self.editor.setTextCursor(cursor)
            self.editor.verticalScrollBar().setValue(old_scroll)
            if reset_undo:
                document.clearUndoRedoStacks()
            self.style_blocks()
        finally:
            self.loading = False

    def render_highlights(self):
        document = self.editor.document()
        plain = document.toPlainText()
        matches = list(re.finditer(r"==([^\n=]+)==", plain))
        for match in reversed(matches):
            cursor = QTextCursor(document)
            cursor.setPosition(len(plain[:match.start()].encode("utf-16-le"))//2)
            if cursor.blockFormat().property(QTextFormat.Property.BlockCodeLanguage) is not None:
                continue
            cursor.setPosition(len(plain[:match.end()].encode("utf-16-le"))//2, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setProperty(HIGHLIGHT_PROPERTY, True)
            fmt.setBackground(QColor("#cfe2f3"))
            fmt.setForeground(QColor("#193e60"))
            cursor.insertText(match[1], fmt)

    def install(self, document: DocumentState, backup=True):
        if backup:
            self.snapshot()
        self.state = copy.deepcopy(document)
        self.pending_rich = self.pending_source = False
        self.load_rich(self.state.markdown, True)
        self.loading = True
        self.source.setPlainText(self.state.markdown)
        self.loading = False
        self.digest = file_digest(self.state.path)
        self.set_mode(self.state.mode, flush=False)
        cursor = self.editor.textCursor()
        cursor.setPosition(min(self.state.cursor, self.editor.document().characterCount() - 1))
        self.editor.setTextCursor(cursor)
        QTimer.singleShot(0, lambda:self.editor.verticalScrollBar().setValue(self.state.scroll))
        self.update_title()
        self.update_statistics()
        self.status.setText("Unsaved disk changes" if self.state.dirty else "Opened" if self.state.path else "New draft")
        self.editor.setFocus()

    def rich_changed(self):
        if self.loading:
            return
        self.pending_rich = True
        self.pending_source = False
        self.status.setText("Editing…")
        self.autosave_timer.start()
        if self.preferences.typewriter:
            rect = self.editor.cursorRect()
            scroll = self.editor.verticalScrollBar()
            scroll.setValue(scroll.value() + rect.center().y() - self.editor.viewport().height() // 2)

    def source_changed(self):
        if self.loading:
            return
        self.pending_source = True
        self.pending_rich = False
        self.status.setText("Editing…")
        self.autosave_timer.start()

    def flush(self):
        if self.pending_source:
            self.state.markdown = self.source.toPlainText()
            self.load_rich(self.state.markdown)
            self.pending_source = False
        elif self.pending_rich:
            self.state.markdown = self.session.serialize(self.editor.document())
            # Source is only updated from rich editing, never while the source is active.
            if self.source.toPlainText() != self.state.markdown:
                self.loading = True
                old = self.source.textCursor()
                position, anchor = old.position(), old.anchor()
                scroll = self.source.verticalScrollBar().value()
                self.source.setPlainText(self.state.markdown)
                cursor = self.source.textCursor()
                size = self.source.document().characterCount() - 1
                cursor.setPosition(min(anchor, size))
                cursor.setPosition(min(position, size), QTextCursor.MoveMode.KeepAnchor)
                self.source.setTextCursor(cursor)
                self.source.verticalScrollBar().setValue(scroll)
                self.loading = False
            self.pending_rich = False
        self.state.cursor = self.editor.textCursor().position()
        self.state.scroll = self.editor.verticalScrollBar().value()
        self.update_title()
        self.update_statistics()
        self.style_blocks()
        return self.state

    def autosave(self):
        if self.recovered:
            return
        try:
            self.flush()
            self.storage.autosave(self.state)
            self.status.setText("Saved locally · unsaved disk changes" if self.state.dirty else "Saved locally")
            if self.preferences.auto_save_disk and self.state.path and self.state.dirty:
                self.save()
        except Exception as error:
            self.status.setText("Local save failed")
            self.notify(f"Please save or export your document. {error}")

    def snapshot(self):
        self.flush()
        self.storage.snapshot(self.state)

    def recover(self, restore):
        recovered = self.recovered
        if not recovered:
            return
        if not restore:
            self.storage.snapshot(recovered)
            recovered = copy.deepcopy(recovered)
            recovered.markdown = recovered.saved_markdown
        self.recovered = None
        self.recovery_bar.hide()
        self.editor.setReadOnly(False)
        self.source.setReadOnly(False)
        self.install(recovered, backup=False)
        self.autosave()
        self.notify("Unsaved version restored" if restore else "Recovery discarded; a history snapshot was kept")

    def protect(self):
        if self.recovered:
            self.notify("Restore or discard the recovered version first.")
            return False
        self.flush()
        if not self.state.dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setText("Save changes before replacing this document?")
        box.setInformativeText("A local version snapshot will also be kept.")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        choice = box.exec()
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        return self.save() if choice == QMessageBox.StandardButton.Save else True

    def new_document(self):
        if self.protect():
            self.install(DocumentState())
            self.autosave()

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Markdown", self.state.path, "Markdown (*.md *.markdown *.txt)")
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path):
        markdown = read_markdown(path)
        if self.protect():
            absolute = str(path.resolve())
            self.install(DocumentState(id=absolute, markdown=markdown, saved_markdown=markdown, path=absolute))
            self.storage.remember_file(absolute)
            self.autosave()

    def refresh_recent(self):
        self.recent_menu.clear()
        for path in (self.storage.get("recent") or {}).get("paths", []):
            self.recent_menu.addAction(Path(path).name, lambda p=path:self.safely(lambda:self.open_path(Path(p))))
        if self.recent_menu.isEmpty():
            action = self.recent_menu.addAction("No recent files")
            action.setEnabled(False)

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select Apple Notes Markdown and attachments", "", "All files (*)")
        if paths:
            self.import_paths([Path(p) for p in paths])

    def import_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Import folder")
        if folder:
            self.import_paths([p for p in Path(folder).rglob("*") if p.is_file()])

    def import_paths(self, paths: list[Path]):
        notes = [p for p in paths if p.suffix.lower() in MARKDOWN_SUFFIXES]
        if not notes:
            raise ValueError("No Markdown files found. Select a .md, .markdown, or .txt file.")
        dialog = ImportDialog(notes, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        markdown = read_markdown(dialog.path)
        mappings = {}
        for ref in image_references(markdown):
            found = resolve_attachment(ref, str(dialog.path), {})
            if not found:
                candidates = [p for p in paths if p.name.casefold() == Path(ref).name.casefold()]
                found = candidates[0] if len(candidates) == 1 else None
            if found:
                if dialog.copy.isChecked():
                    found = copy_asset(found, self.storage.assets / uuid4().hex)
                mappings[ref] = str(found)
        if dialog.normalize.isChecked():
            session = MarkdownSession()
            temporary = QTextDocument()
            temporary.setMarkdown(session.prepare(markdown), DIALECT)
            session.baseline(temporary)
            markdown = session.serialize(temporary, normalize=True)
        if dialog.insert:
            self.snapshot()
            conflicts = set(mappings) & set(self.state.attachments)
            if conflicts:
                raise ValueError("Attachment paths conflict with this document. Open the note separately or rename its assets first.")
            self.state.attachments.update(mappings)
            self.editor.document().mappings = self.state.attachments
            session = MarkdownSession()
            rendered = session.prepare(markdown)
            prefix = front_matter(markdown)[0]
            if prefix:
                from .markdown import fence
                rendered = fence(prefix.rstrip(), "preserved-markdown-insert") + "\n\n" + rendered
                self.session.raw_languages.add("preserved-markdown-insert")
            self.session.raw_languages.update(session.raw_languages)
            self.editor.textCursor().insertMarkdown(rendered)
            self.notify("Markdown inserted")
        elif self.protect():
            absolute = str(dialog.path.resolve())
            self.install(DocumentState(id=absolute, markdown=markdown, saved_markdown=read_markdown(dialog.path), path=absolute, attachments=mappings))
            self.storage.remember_file(absolute)
            self.autosave()

    def save(self, save_as=False):
        self.flush()
        original_path = self.state.path
        path = original_path
        if save_as or not path:
            suggested = path or f"{self.title()}.md"
            path, _ = QFileDialog.getSaveFileName(self, "Save Markdown As", suggested, "Markdown (*.md);;Text (*.txt)")
            if not path:
                return False
            if not Path(path).suffix:
                path += ".md"
        if path == original_path and self.digest and file_digest(path) != self.digest:
            choice = QMessageBox.question(self, "File changed on disk", "The file changed outside this editor. Overwrite it with this version?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if choice != QMessageBox.StandardButton.Yes:
                return False
        self.status.setText("Saving…")
        # Copy managed attachments before writing Markdown, so references never point
        # at an unexported database blob after a successful Save As.
        self.save_attachments(Path(path))
        atomic_write(Path(path), self.state.markdown, bool(self.editor.toPlainText().strip()))
        self.state.path = str(Path(path).resolve())
        self.state.saved_markdown = self.state.markdown
        self.digest = file_digest(self.state.path)
        self.editor.document().note_path = self.state.path
        self.storage.autosave(self.state)
        self.storage.remember_file(self.state.path)
        self.status.setText("Saved to file")
        self.notify("Saved")
        self.update_title()
        return True

    def save_attachments(self, destination: Path):
        for ref in image_references(self.state.markdown):
            if QUrl(ref).scheme() in {"http", "https"}:
                continue
            source = resolve_attachment(ref, self.state.path, self.state.attachments)
            if not source:
                continue  # Missing references are intentionally preserved.
            relative = Path(ref.replace("\\", "/"))
            target = destination.parent / relative
            if source.resolve() == target.resolve():
                continue
            if relative.is_absolute() or ".." in relative.parts:
                if destination.parent.resolve() != Path(self.state.path).parent.resolve() if self.state.path else True:
                    raise ValueError(f"Save As cannot safely copy attachment outside the new folder: {ref}. Relink it to an Attachments/ path first.")
                continue
            if target.exists():
                if source.read_bytes() != target.read_bytes():
                    raise ValueError(f"Attachment already exists with different contents: {ref}. Choose another save folder to avoid overwriting it.")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def title(self):
        return title_for(self.state.markdown, self.state.path, self.state.title, self.preferences.heading_title)

    def update_title(self):
        title = self.title()
        self.title_button.setText(title if len(title) < 35 else title[:32] + "…")
        self.title_button.setToolTip(title)
        self.setWindowTitle(f"{title}{' •' if self.state.dirty else ''} — Bear Markdown")

    def rename_title(self):
        value, accepted = QInputDialog.getText(self, "Document title", "App title (use Save As to rename the file)", text=self.title())
        if accepted:
            self.state.title = value.strip()
            self.update_title()
            self.autosave()

    def set_mode(self, mode, flush=True):
        if flush:
            self.flush()
        self.state.mode = mode
        for action in self.format_toolbar.actions():
            action.setEnabled(mode != "Markdown")
        self.rich_pane.setVisible(mode != "Markdown")
        self.source.setVisible(mode != "Write")
        self.format_toolbar.setVisible(mode != "Markdown" and not self.focus_mode)
        self.editor.setMaximumWidth(16777215 if mode == "Split" else {"Narrow":640,"Normal":760,"Wide":920}.get(self.preferences.width,760))
        for name, button in self.mode_buttons.items():
            button.setChecked(name == mode)
        if mode == "Markdown":
            self.source.setFocus()
        else:
            self.editor.setFocus()

    def update_selection(self):
        fmt = self.editor.currentCharFormat()
        block = self.editor.textCursor().blockFormat()
        for key, action in self.format_actions.items():
            active = block.headingLevel() == int(key[1]) if key.startswith("h") and key[1:].isdigit() else {"bold":fmt.fontWeight() >= QFont.Weight.Bold,"italic":fmt.fontItalic(),"strike":fmt.fontStrikeOut(),"code":fmt.fontFixedPitch(),"highlight":bool(fmt.property(HIGHLIGHT_PROPERTY))}.get(key,False)
            action.setChecked(active)
        selection = self.editor.textCursor().hasSelection()
        if selection and self.preferences.bubble and self.editor.hasFocus() and not QApplication.activeModalWidget():
            self.bubble.adjustSize()
            rect = self.editor.cursorRect()
            x = max(0, min(rect.x(), self.editor.viewport().width() - self.bubble.width()))
            self.bubble.move(x, max(0, rect.y() - self.bubble.height() - 8))
            self.bubble.show()
            self.bubble.raise_()
        else:
            self.bubble.hide()

    def settings(self):
        dialog = PreferencesDialog(self.preferences, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.preferences = dialog.preferences()
            self.storage.save_preferences(self.preferences)
            self.apply_preferences()

    def toggle_dark(self):
        self.preferences.dark = not self.preferences.dark
        self.storage.save_preferences(self.preferences)
        self.apply_preferences()

    def toggle_focus(self):
        self.focus_mode = not self.focus_mode
        self.topbar.setVisible(not self.focus_mode)
        self.format_toolbar.setVisible(not self.focus_mode and self.state.mode != "Markdown")
        self.exit_focus.setVisible(self.focus_mode)
        self.stats_button.setVisible(not self.focus_mode)
        self.editor.setFocus()

    def command_palette(self):
        self.flush()
        self.bubble.hide()
        dialog = CommandPalette(self.commands, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chosen:
            self.safely(dialog.chosen)

    def search(self):
        self.flush()
        if self.state.mode == "Markdown":
            self.set_mode("Split")
        dialog = SearchDialog(self.editor, self.snapshot, self)
        self.search_dialog = dialog
        dialog.exec()
        self.search_dialog = None

    def outline(self):
        self.flush()
        dialog = QDialog(self)
        dialog.setWindowTitle("Document outline")
        dialog.resize(360, 380)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        positions = []
        block = self.editor.document().begin()
        while block.isValid():
            level = block.blockFormat().headingLevel()
            if level:
                listing.addItem("    " * (level - 1) + block.text())
                positions.append(block.position())
            block = block.next()
        layout.addWidget(listing)
        if not positions:
            layout.addWidget(QLabel("Add headings to see the document outline."))
        def jump():
            index = listing.currentRow()
            if index >= 0:
                self.set_mode("Write" if self.state.mode == "Markdown" else self.state.mode)
                cursor = self.editor.textCursor()
                cursor.setPosition(positions[index])
                self.editor.setTextCursor(cursor)
                self.editor.ensureCursorVisible()
                dialog.accept()
        listing.itemActivated.connect(jump)
        listing.itemClicked.connect(jump)
        dialog.exec()
        self.editor.setFocus()

    def statistics(self):
        text = self.editor.toPlainText()
        words = len(text.split())
        headings = tasks = completed = paragraphs = 0
        block = self.editor.document().begin()
        while block.isValid():
            fmt = block.blockFormat()
            headings += bool(fmt.headingLevel())
            paragraphs += bool(block.text().strip()) and not fmt.headingLevel()
            tasks += fmt.marker() != QTextBlockFormat.MarkerType.NoMarker
            completed += fmt.marker() == QTextBlockFormat.MarkerType.Checked
            block = block.next()
        return {"Words":words,"Characters":len(text),"Characters without spaces":len(re.sub(r"\s", "", text)),"Paragraphs":paragraphs,"Headings":headings,"Reading time":f"{max(1, round(words/220))} min","Tasks completed":completed,"Tasks remaining":tasks-completed}

    def update_statistics(self):
        stats = self.statistics()
        self.stats_button.setText(f"{stats['Words']:,} words · {stats['Characters']:,} chars")

    def document_info(self, statistics=False):
        self.flush()
        dialog = QDialog(self)
        dialog.setWindowTitle("Document statistics" if statistics else "Document info")
        dialog.resize(430, 360)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        values = self.statistics() if statistics else {"Filename":Path(self.state.path).name if self.state.path else "Untitled draft","Title":self.title(),"Location":str(Path(self.state.path).parent) if self.state.path else str(self.storage.directory),"File size":f"{len(self.state.markdown.encode()):,} bytes","Encoding":"UTF-8","Line endings":"CRLF" if "\r\n" in self.state.markdown else "LF","Mode":self.state.mode,**front_matter(self.state.markdown)[2]}
        for key, value in values.items():
            label = QLabel(str(value))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(str(key), label)
        layout.addLayout(form)
        if not statistics:
            note = QLabel("Front matter is preserved and editable in Markdown view.")
            note.setWordWrap(True)
            layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def history(self):
        self.snapshot()
        dialog = HistoryDialog(self.storage.versions(self.state.id), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chosen:
            self.snapshot()
            recovered = copy.deepcopy(dialog.chosen)
            if dialog.duplicate:
                recovered.id = uuid4().hex
                recovered.path = ""
                recovered.saved_markdown = ""
                recovered.title = f"{self.title()} copy"
            else:
                recovered.saved_markdown = self.state.saved_markdown
                recovered.path = self.state.path
            self.install(recovered, backup=False)
            self.autosave()

    def copy_markdown(self):
        self.flush()
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            document = QTextDocument()
            QTextCursor(document).insertFragment(cursor.selection())
            value = native_markdown(document)
        else:
            value = self.state.markdown
        QApplication.clipboard().setText(value)
        self.notify("Markdown copied")

    def code_dialog(self):
        if self.state.mode == "Markdown":
            self.set_mode("Write")
        language, accepted = QInputDialog.getItem(self, "Code block", "Language", ["text","javascript","typescript","json","html","css","python","java","bash","sql","markdown","xml","yaml"], editable=True)
        if accepted:
            self.editor.code_block(language if language != "text" else "")

    def link_dialog(self):
        if self.state.mode == "Markdown":
            self.set_mode("Write")
        cursor = self.editor.textCursor()
        fmt = cursor.charFormat()
        dialog = QDialog(self)
        dialog.setWindowTitle("Link")
        dialog.resize(400, 160)
        layout = QVBoxLayout(dialog)
        field = QLineEdit(fmt.anchorHref())
        field.setPlaceholderText("https://… or notes://…")
        field.setAccessibleName("Link URL")
        layout.addWidget(field)
        row = QHBoxLayout()
        for label, callback in [("Open", lambda:QDesktopServices.openUrl(QUrl(field.text())) if safe_url(field.text()) else None), ("Copy URL", lambda:QApplication.clipboard().setText(field.text()))]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        remove = QPushButton("Remove link")
        def remove_link():
            value = QTextCharFormat()
            value.setAnchor(False)
            value.setAnchorHref("")
            value.setFontUnderline(False)
            cursor.mergeCharFormat(value)
            dialog.reject()
        remove.clicked.connect(remove_link)
        row.addWidget(remove)
        layout.addLayout(row)
        dialog_buttons(dialog, layout)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            url = field.text().strip()
            if not safe_url(url):
                raise ValueError("Use http, https, mailto, tel, notes, or a relative link.")
            value = QTextCharFormat()
            value.setAnchor(True)
            value.setAnchorHref(url)
            value.setForeground(QColor("#6ea3d2" if self.preferences.dark else "#1f4e79"))
            value.setFontUnderline(True)
            if cursor.hasSelection():
                cursor.mergeCharFormat(value)
            else:
                cursor.insertText(url, value)
        self.editor.setFocus()

    def table_dialog(self):
        if self.state.mode == "Markdown":
            self.set_mode("Write")
        cursor = self.editor.textCursor()
        table = cursor.currentTable()
        if table:
            dialog = QDialog(self)
            dialog.setWindowTitle("Table controls")
            layout = QVBoxLayout(dialog)
            cell = table.cellAt(cursor)
            controls = [("Add row above",lambda:table.insertRows(cell.row(),1)),("Add row below",lambda:table.insertRows(cell.row()+1,1)),("Delete row",lambda:table.removeRows(cell.row(),1)),("Add column left",lambda:table.insertColumns(cell.column(),1)),("Add column right",lambda:table.insertColumns(cell.column()+1,1)),("Delete column",lambda:table.removeColumns(cell.column(),1))]
            def header():
                fmt = table.format()
                fmt.setHeaderRowCount(0 if fmt.headerRowCount() else 1)
                table.setFormat(fmt)
            def remove():
                selected = QTextCursor(self.editor.document())
                selected.setPosition(table.firstPosition()-1)
                selected.setPosition(table.lastPosition()+1,QTextCursor.MoveMode.KeepAnchor)
                selected.removeSelectedText()
            controls.extend([("Toggle header row",header),("Delete table",remove)])
            for label, callback in controls:
                button = QPushButton(label)
                button.clicked.connect(lambda checked=False, fn=callback:(fn(),dialog.accept()))
                layout.addWidget(button)
            dialog.exec()
        else:
            dialog = TablePicker(self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                columns, rows = dialog.dimensions
                fmt = QTextTableFormat()
                fmt.setBorder(1)
                fmt.setBorderBrush(QColor("#e4e4e7"))
                fmt.setCellPadding(8)
                fmt.setCellSpacing(0)
                fmt.setHeaderRowCount(1)
                fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength,100))
                table = cursor.insertTable(rows,columns,fmt)
                for column in range(columns):
                    table.cellAt(0,column).firstCursorPosition().insertText(f"Header {column+1}")
                self.editor.setTextCursor(table.cellAt(1 if rows>1 else 0,0).firstCursorPosition())
        self.editor.setFocus()

    def image_dialog(self):
        if self.state.mode == "Markdown":
            self.set_mode("Write")
        cursor = self.editor.textCursor()
        existing = cursor.charFormat().toImageFormat() if cursor.charFormat().isImageFormat() else None
        dialog = QDialog(self)
        dialog.setWindowTitle("Image")
        dialog.resize(430, 300)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        location = QLineEdit(existing.name() if existing else "")
        alt = QLineEdit(str(existing.property(QTextFormat.Property.ImageAltText) or "") if existing else "")
        caption = QLineEdit(str(existing.property(QTextFormat.Property.ImageTitle) or "") if existing else "")
        size = QComboBox()
        size.addItems(["Original","Small","Medium","Full width"])
        form.addRow("Image URL / path",location)
        form.addRow("Alt text",alt)
        form.addRow("Caption",caption)
        form.addRow("Display size",size)
        layout.addLayout(form)
        upload = QPushButton("Upload / replace image…")
        selected = []
        def choose():
            path, _ = QFileDialog.getOpenFileName(dialog,"Choose image","","Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.svg *.avif)")
            if path:
                selected[:] = [Path(path)]
                location.setText(path)
        upload.clicked.connect(choose)
        layout.addWidget(upload)
        note = QLabel("You can also paste or drag an image directly into the editor.")
        note.setWordWrap(True)
        layout.addWidget(note)
        dialog_buttons(dialog,layout,"Update image" if existing else "Insert image")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        ref = self.store_image(selected[0]) if selected else location.text().strip()
        if not safe_url(ref,True):
            raise ValueError("Choose an http(s) image URL or a relative attachment path.")
        fmt = QTextImageFormat(existing) if existing else QTextImageFormat()
        fmt.setName(ref)
        fmt.setProperty(QTextFormat.Property.ImageAltText,alt.text())
        fmt.setProperty(QTextFormat.Property.ImageTitle,caption.text())
        width = {"Small":240,"Medium":480,"Full width":max(240,self.editor.viewport().width()-30)}.get(size.currentText())
        if width:
            fmt.setWidth(width)
        else:
            fmt.clearProperty(QTextFormat.Property.ImageWidth)
        if existing:
            cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter,QTextCursor.MoveMode.KeepAnchor)
            cursor.insertImage(fmt)
        else:
            cursor.insertImage(fmt)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def store_image(self, path: Path):
        image = QImage(str(path))
        if image.isNull():
            raise ValueError("Image could not load. Choose a supported image file.")
        if self.state.path:
            folder = Path(self.state.path).parent / "Attachments"
        else:
            folder = self.storage.assets / self.state.id
        destination = copy_asset(path,folder)
        ref = "Attachments/"+destination.name
        self.state.attachments[ref] = str(destination)
        self.editor.document().mappings = self.state.attachments
        self.editor.document().addResource(QTextDocument.ResourceType.ImageResource,QUrl(ref),image)
        return ref

    def paste_image(self, image: QImage):
        folder = self.storage.assets / "clipboard"
        folder.mkdir(exist_ok=True)
        path = unique_destination(folder,f"image-{datetime.now():%Y-%m-%d-%H%M%S}.png")
        if not image.save(str(path),"PNG"):
            raise ValueError("Could not store pasted image.")
        ref = self.store_image(path)
        fmt = QTextImageFormat()
        fmt.setName(ref)
        if image.width()>700:
            fmt.setWidth(700)
        self.editor.textCursor().insertImage(fmt)
        self.notify("Image inserted")

    def locate_image(self, ref):
        path, _ = QFileDialog.getOpenFileName(self,"Locate attachment",Path(ref).name,"Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.svg *.avif)")
        if path:
            image = QImage(path)
            if image.isNull():
                raise ValueError("This image could not be loaded.")
            self.state.attachments[ref] = str(copy_asset(Path(path),self.storage.assets/uuid4().hex))
            self.editor.document().mappings = self.state.attachments
            self.editor.document().addResource(QTextDocument.ResourceType.ImageResource,QUrl(ref),image)
            self.editor.document().markContentsDirty(0,self.editor.document().characterCount())
            self.editor.viewport().update()
            self.autosave()
            self.notify("Attachment relinked; Markdown reference preserved")

    def drop_paths(self, paths):
        expanded=[]
        for value in paths:
            path=Path(value)
            expanded.extend(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else expanded.append(path)
        if expanded and all(p.suffix.lower() in IMAGE_SUFFIXES for p in expanded):
            for path in expanded:
                fmt=QTextImageFormat()
                fmt.setName(self.store_image(path))
                self.editor.textCursor().insertImage(fmt)
        else:
            self.import_paths(expanded)

    def export_dialog(self):
        self.flush()
        dialog=QDialog(self)
        dialog.setWindowTitle("Export document")
        layout=QVBoxLayout(dialog)
        for label, kind in [("Markdown (.md)","md"),("Markdown + attachments (.zip)","zip"),("Plain text (.txt)","txt"),("Readable HTML (.html)","html"),("PDF / Print","print")]:
            button=QPushButton(label)
            button.clicked.connect(lambda checked=False,k=kind:(dialog.accept(),self.safely(lambda:self.export(k))))
            layout.addWidget(button)
        dialog.exec()

    def export(self, kind):
        self.flush()
        if kind=="print":
            self.print_document()
            return
        path,_=QFileDialog.getSaveFileName(self,"Export document",f"{self.title()}.{kind}",f"{kind.upper()} (*.{kind})")
        if not path:
            return
        if kind=="md":
            self.save_attachments(Path(path))
            atomic_write(Path(path),self.state.markdown,bool(self.editor.toPlainText().strip()))
        elif kind=="txt":
            atomic_write(Path(path),self.editor.toPlainText())
        elif kind=="html":
            output=self.editor.document().toHtml()
            for ref in image_references(self.state.markdown):
                asset=resolve_attachment(ref,self.state.path,self.state.attachments)
                if asset:
                    import mimetypes
                    mime=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream'
                    data=base64.b64encode(asset.read_bytes()).decode()
                    output=output.replace(f'src="{html.escape(ref,quote=True)}"',f'src="data:{mime};base64,{data}"')
            atomic_write(Path(path),output)
        else:
            assets={ref:resolved for ref in image_references(self.state.markdown) if (resolved:=resolve_attachment(ref,self.state.path,self.state.attachments))}
            write_bundle(Path(path),self.state.markdown,f"{self.title()}.md",assets)
        self.notify("Export complete")

    def print_document(self):
        self.flush()
        printer=QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(self.title())
        dialog=QPrintDialog(printer,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.editor.document().print_(printer)

    def style_document(self):
        document=self.editor.document()
        cursor=QTextCursor(document)
        cursor.beginEditBlock()
        block=document.begin()
        line_height={'Compact':150,'Normal':172,'Relaxed':190}[self.preferences.spacing]
        while block.isValid():
            cursor=QTextCursor(block)
            fmt=cursor.blockFormat()
            code=fmt.property(QTextFormat.Property.BlockCodeLanguage)
            fmt.setLineHeight(145 if code is not None else line_height,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            if fmt.headingLevel():
                fmt.setTopMargin(18 if block.position() else 0)
                fmt.setBottomMargin(7)
            elif not block.textList() and code is None:
                fmt.setTopMargin(3)
                fmt.setBottomMargin(9)
            cursor.setBlockFormat(fmt)
            block=block.next()
        for table in tables(document):
            fmt=table.format()
            fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength,100))
            fmt.setCellPadding(8)
            fmt.setCellSpacing(0)
            fmt.setBorder(1)
            fmt.setBorderBrush(QColor('#3c3c42' if self.preferences.dark else '#e4e4e7'))
            table.setFormat(fmt)
        cursor.endEditBlock()

    def style_blocks(self):
        # View-only extra selections keep completed tasks readable without adding
        # strike marks to the actual Markdown document.
        selections=[]
        if self.preferences.strike_tasks:
            block=self.editor.document().begin()
            while block.isValid():
                if block.blockFormat().marker()==QTextBlockFormat.MarkerType.Checked:
                    selection=self.editor.ExtraSelection()
                    selection.cursor=QTextCursor(block)
                    selection.cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                    selection.format.setFontStrikeOut(True)
                    selection.format.setForeground(QColor('#77777d'))
                    selections.append(selection)
                block=block.next()
        if self.search_dialog is None:
            self.editor.setExtraSelections(selections)

    def apply_preferences(self):
        was_loading=self.loading
        self.loading=True
        dark=self.preferences.dark
        background='#1c1c1e' if dark else '#fbfbfb'
        foreground='#eeeef1' if dark else '#29292d'
        border='#3c3c42' if dark else '#e4e4e7'
        wash='#29292e' if dark else '#f2f3f5'
        accent='#6ea3d2' if dark else '#1f4e79'
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background:{background}; color:{foreground}; font-family:'Segoe UI','Helvetica Neue',sans-serif; }}
            QToolBar {{ border:0; border-bottom:1px solid {border}; spacing:2px; padding:5px 10px; }}
            #topbar {{ border-bottom:1px solid {border}; }}
            QToolButton,QPushButton {{ border:0; border-radius:5px; padding:6px 8px; }}
            QToolButton:hover,QPushButton:hover {{ background:{wash}; }}
            QToolButton:checked,QPushButton:checked {{ color:{accent}; background:{wash}; }}
            QToolButton:focus,QPushButton:focus,QLineEdit:focus,QComboBox:focus {{ border:1px solid {accent}; }}
            #title {{ font-weight:600; }}
            #status,#statistics {{ color:{'#aaaab2' if dark else '#77777d'}; font-size:11px; }}
            QTextEdit,QPlainTextEdit {{ border:0; background:{background}; selection-background-color:{accent}; selection-color:white; padding:8px; }}
            QPlainTextEdit {{ font-family:Consolas,Menlo,monospace; font-size:14px; padding:24px; }}
            QLineEdit,QComboBox,QListWidget {{ border:1px solid {border}; border-radius:5px; padding:7px; }}
            QDialog {{ background:{background}; }}
            QDialog QPushButton {{ border:1px solid {border}; margin-top:5px; }}
            QMenu {{ border:1px solid {border}; padding:6px; }}
            QMenu::item {{ padding:7px 24px; }}
            QMenu::item:selected,QListWidget::item:selected {{ background:{wash}; color:{accent}; }}
            QSplitter::handle {{ background:{border}; width:1px; height:1px; }}
            #bubble {{ border:1px solid {border}; border-radius:7px; background:{background}; }}
            #toast {{ background:{wash}; border-radius:7px; padding:8px 12px; font-size:12px; }}
            QCheckBox {{ spacing:8px; padding:4px; }}
        """)
        family={'Sans':'Segoe UI','Serif':'Georgia','Mono':'Consolas'}.get(self.preferences.font,'Segoe UI')
        size={'Small':12,'Medium':13.5,'Large':15}.get(self.preferences.size,13.5)
        font=QFont(family)
        font.setPointSizeF(size)
        self.editor.setFont(font)
        self.editor.document().setDefaultFont(font)
        self.editor.document().setDefaultStyleSheet(f"p {{line-height:{ {'Compact':150,'Normal':172,'Relaxed':190}[self.preferences.spacing]}%;}} a {{color:{accent};}} pre,code {{background:{wash};font-family:Consolas;}} table {{border-collapse:collapse;}}")
        self.editor.highlighter.dark=dark
        self.editor.highlighter.rehighlight()
        self.editor.setMaximumWidth({'Narrow':640,'Normal':760,'Wide':920}.get(self.preferences.width,760))
        self.style_document()
        self.style_blocks()
        self.loading=was_loading

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'splitter'):
            self.splitter.setOrientation(Qt.Orientation.Vertical if self.width()<820 else Qt.Orientation.Horizontal)
            self.status.setVisible(self.width()>900)

    def closeEvent(self,event:QCloseEvent):
        try:
            if self.recovered:
                event.accept()
                return
            if not self.protect():
                event.ignore()
                return
            self.snapshot()
            self.storage.autosave(self.state)
            self.storage.save_preferences(self.preferences)
            event.accept()
        except Exception as error:
            self.notify(str(error))
            event.ignore()
