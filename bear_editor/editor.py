from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QImage, QPainter, QTextBlockFormat,
    QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextImageFormat, QTextListFormat,
    QSyntaxHighlighter, QKeyEvent)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtWidgets import QTextEdit, QMenu, QApplication

from .files import resolve_attachment, safe_url, IMAGE_SUFFIXES
from .serialization import HIGHLIGHT_PROPERTY


class AssetDocument(QTextDocument):
    asset_loaded = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.note_path = ""
        self.mappings: dict[str, str] = {}
        self.network = QNetworkAccessManager(self)
        self.pending: set[str] = set()
        self.failed: set[str] = set()

    def loadResource(self, kind, name):
        if kind != QTextDocument.ResourceType.ImageResource:
            return None
        ref = name.toString()
        # All local image access is resolved explicitly, never through arbitrary imported HTML.
        source = resolve_attachment(ref, self.note_path, self.mappings)
        if source and source.suffix.lower() in IMAGE_SUFFIXES:
            image = QImage(str(source))
            if not image.isNull():
                return image
        if name.scheme() in {"https", "http"} and ref not in self.pending and ref not in self.failed:
            self.pending.add(ref)
            request = QNetworkRequest(name)
            request.setTransferTimeout(15000)
            reply = self.network.get(request)
            def finished():
                self.pending.discard(ref)
                image = QImage()
                if reply.error() == QNetworkReply.NetworkError.NoError:
                    data = reply.readAll()
                    if data.size() <= 25 * 1024 * 1024:
                        image.loadFromData(data)
                if not image.isNull():
                    self.addResource(QTextDocument.ResourceType.ImageResource, name, image)
                    self.markContentsDirty(0, self.characterCount())
                    self.asset_loaded.emit()
                else:
                    self.failed.add(ref)
                reply.deleteLater()
            reply.finished.connect(finished)
        image = QImage(360, 76, QImage.Format.Format_ARGB32)
        image.fill(QColor("#f2f3f5"))
        painter = QPainter(image)
        painter.setPen(QColor("#77777d"))
        painter.drawText(image.rect().adjusted(12, 8, -12, -8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"Missing image · right-click to locate\n{ref[:48]}")
        painter.end()
        return image


class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.dark = False

    def highlightBlock(self, text):
        code = self.currentBlock().blockFormat().property(QTextFormat.Property.BlockCodeLanguage)
        if code is not None:
            if str(code).startswith("preserved-markdown"):
                fmt = QTextCharFormat()
                fmt.setForeground(QColor("#77777d"))
                self.setFormat(0, len(text), fmt)
                return
            try:
                from pygments import lex
                from pygments.lexers import get_lexer_by_name
                from pygments.token import Token
                lexer = get_lexer_by_name(str(code) or "text")
                position = 0
                for kind, value in lex(text, lexer):
                    fmt = QTextCharFormat()
                    if kind in Token.Keyword:
                        fmt.setForeground(QColor("#6ea3d2" if self.dark else "#1f4e79"))
                        fmt.setFontWeight(QFont.Weight.DemiBold)
                    elif kind in Token.Literal.String:
                        fmt.setForeground(QColor("#83b58b" if self.dark else "#527e59"))
                    elif kind in Token.Comment:
                        fmt.setForeground(QColor("#88888d"))
                    elif kind in Token.Literal.Number:
                        fmt.setForeground(QColor("#ae85bf"))
                    self.setFormat(position, len(value), fmt)
                    position += len(value)
            except ImportError:
                pass
            except Exception:
                # Unknown code languages remain untouched and editable.
                pass
        else:
            for match in re.finditer(r"(?<!\w)#[\w]+(?:/[\w]+)*", text):
                fmt = QTextCharFormat()
                fmt.setForeground(QColor("#6ea3d2" if self.dark else "#1f4e79"))
                self.setFormat(match.start(), len(match[0]), fmt)


class RichEditor(QTextEdit):
    files_dropped = Signal(list)
    image_pasted = Signal(QImage)
    locate_image = Signal(str)
    edit_image = Signal()
    link_requested = Signal()
    table_requested = Signal()
    notice = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDocument(AssetDocument(self))
        self.setAcceptRichText(True)
        self.setPlaceholderText("Start writing…")
        self.setFrameShape(QTextEdit.Shape.NoFrame)
        self.setAccessibleName("Document editor")
        self.setTabStopDistance(36)
        self.setCursorWidth(2)
        self.highlighter = CodeHighlighter(self.document())

    def insertFromMimeData(self, source):
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage):
                self.image_pasted.emit(image)
                return
        if source.hasUrls():
            paths = [url.toLocalFile() for url in source.urls() if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                return
        if source.hasHtml():
            # Reparse via the native document model: no scripts, CSS, tracking attributes,
            # or browser DOM survive conversion to standard Markdown.
            temporary = QTextDocument()
            temporary.setHtml(source.html())
            markdown = temporary.toMarkdown()
            from .markdown import MarkdownSession
            session = MarkdownSession()
            sanitized = session.prepare(markdown)
            self.textCursor().insertMarkdown(sanitized)
            return
        if source.hasText() and re.fullmatch(r"https?://\S+",source.text().strip()) and safe_url(source.text().strip()):
            fmt=QTextCharFormat()
            fmt.setAnchor(True)
            fmt.setAnchorHref(source.text().strip())
            fmt.setForeground(QColor('#1f4e79'))
            fmt.setFontUnderline(True)
            self.textCursor().insertText(source.text().strip(),fmt)
            return
        super().insertFromMimeData(source)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.setTextCursor(self.cursorForPosition(event.position().toPoint()))
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def mousePressEvent(self, event):
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            link = self.anchorAt(event.position().toPoint())
            if link and safe_url(link):
                QDesktopServices.openUrl(QUrl(link))
                return
        cursor = self.cursorForPosition(event.position().toPoint())
        marker = cursor.blockFormat().marker()
        start = QTextCursor(cursor)
        start.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        text_x = self.cursorRect(start).x()
        if marker != QTextBlockFormat.MarkerType.NoMarker and event.position().x() < text_x + 3:
            self.setTextCursor(cursor)
            fmt = cursor.blockFormat()
            fmt.setMarker(QTextBlockFormat.MarkerType.Checked if marker == QTextBlockFormat.MarkerType.Unchecked else QTextBlockFormat.MarkerType.Unchecked)
            cursor.setBlockFormat(fmt)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        cursor = self.textCursor()
        table = cursor.currentTable()
        if table and event.key() in {Qt.Key.Key_Tab, Qt.Key.Key_Backtab}:
            cell = table.cellAt(cursor)
            previous = event.key() == Qt.Key.Key_Backtab or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            index = cell.row() * table.columns() + cell.column() + (-1 if previous else 1)
            if index >= table.rows() * table.columns():
                table.appendRows(1)
            if index >= 0:
                self.setTextCursor(table.cellAt(index // table.columns(), index % table.columns()).firstCursorPosition())
            return
        if not table and cursor.currentList() and event.key() in {Qt.Key.Key_Tab,Qt.Key.Key_Backtab}:
            previous=event.key()==Qt.Key.Key_Backtab or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            fmt=cursor.currentList().format()
            indent=fmt.indent()+(-1 if previous else 1)
            if indent<=0:
                cursor.currentList().remove(cursor.block())
                block=cursor.blockFormat()
                block.setIndent(0)
                block.setMarker(QTextBlockFormat.MarkerType.NoMarker)
                cursor.setBlockFormat(block)
            else:
                fmt.setIndent(indent)
                cursor.createList(fmt)
            return
        if event.key() in {Qt.Key.Key_Return,Qt.Key.Key_Enter}:
            match=re.fullmatch(r"```([a-zA-Z0-9_+-]*)",cursor.block().text())
            if match:
                cursor.beginEditBlock()
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                cursor.removeSelectedText()
                self.setTextCursor(cursor)
                self.code_block(match[1])
                cursor.endEditBlock()
                return
            if not cursor.block().text() and cursor.blockFormat().property(QTextFormat.Property.BlockCodeLanguage) is not None:
                block=cursor.blockFormat()
                block.clearProperty(QTextFormat.Property.BlockCodeLanguage)
                cursor.setBlockFormat(block)
                self.setCurrentCharFormat(QTextCharFormat())
                return
        if event.key() == Qt.Key.Key_Space and not cursor.hasSelection() and cursor.atBlockEnd():
            prefix = cursor.block().text()
            if prefix in {"#", "##", "###", ">", "-", "*", "1.", "- [ ]", "- [x]", "[ ]", "[x]", "```"}:
                cursor.beginEditBlock()
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                cursor.removeSelectedText()
                self.setTextCursor(cursor)
                if prefix.startswith("#"):
                    self.heading(len(prefix))
                elif prefix == ">":
                    self.quote()
                elif prefix == "```":
                    self.code_block()
                else:
                    self.list_format("task" if "[" in prefix else "ordered" if prefix == "1." else "bullet")
                    if 'x' in prefix:
                        task=self.textCursor().blockFormat()
                        task.setMarker(QTextBlockFormat.MarkerType.Checked)
                        self.textCursor().setBlockFormat(task)
                cursor.endEditBlock()
                return
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and cursor.block().text() == "---":
            cursor.beginEditBlock()
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.insertHtml("<hr>")
            cursor.insertBlock()
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return
        super().keyPressEvent(event)
        if event.text() in {"*", "~", "`", "="}:
            self.inline_shortcut()

    def inline_shortcut(self):
        cursor = self.textCursor()
        if cursor.blockFormat().property(QTextFormat.Property.BlockCodeLanguage) is not None:
            return
        text = cursor.block().text()[:cursor.positionInBlock()]
        for pattern, style, width in [(r"\*\*([^*]+)\*\*$", "bold", 2), (r"(?<!\*)\*([^*]+)\*$", "italic", 1), (r"~~([^~]+)~~$", "strike", 2), (r"`([^`]+)`$", "code", 1), (r"==([^=]+)==$", "highlight", 2)]:
            match = re.search(pattern, text)
            if not match:
                continue
            begin = cursor.block().position() + len(text[:match.start()].encode("utf-16-le"))//2
            cursor.beginEditBlock()
            cursor.setPosition(begin)
            cursor.setPosition(begin + len(match[0].encode("utf-16-le"))//2, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(match[1])
            cursor.setPosition(begin)
            cursor.setPosition(begin + len(match[1].encode("utf-16-le"))//2, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
            self.mark(style)
            cursor.clearSelection()
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            self.setTextCursor(cursor)
            self.setCurrentCharFormat(QTextCharFormat())
            cursor.endEditBlock()
            break

    def mark(self, style: str):
        cursor = self.textCursor()
        current = cursor.charFormat()
        fmt = QTextCharFormat()
        if style == "bold":
            fmt.setFontWeight(QFont.Weight.Normal if current.fontWeight() >= QFont.Weight.Bold else QFont.Weight.Bold)
        elif style == "italic":
            fmt.setFontItalic(not current.fontItalic())
        elif style == "strike":
            fmt.setFontStrikeOut(not current.fontStrikeOut())
        elif style == "code":
            enabled = not current.fontFixedPitch()
            fmt.setFontFixedPitch(enabled)
            fmt.setFontFamilies(["Consolas", "Menlo", "monospace"] if enabled else [self.font().family()])
        elif style == "highlight":
            enabled = not bool(current.property(HIGHLIGHT_PROPERTY))
            fmt.setProperty(HIGHLIGHT_PROPERTY, enabled)
            fmt.setBackground(QColor("#cfe2f3") if enabled else QColor(Qt.GlobalColor.transparent))
            if enabled:
                fmt.setForeground(QColor("#193e60"))
        if cursor.hasSelection():
            cursor.mergeCharFormat(fmt)
        else:
            self.mergeCurrentCharFormat(fmt)
        self.setFocus()

    def heading(self, level: int):
        cursor = self.textCursor()
        fmt = cursor.blockFormat()
        fmt.setHeadingLevel(0 if fmt.headingLevel() == level else level)
        cursor.setBlockFormat(fmt)
        char = QTextCharFormat()
        char.setFontWeight(QFont.Weight.Bold if fmt.headingLevel() else QFont.Weight.Normal)
        char.setFontPointSize(self.font().pointSizeF() * ({1:1.9,2:1.5,3:1.22}.get(fmt.headingLevel(),1)))
        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        cursor.mergeCharFormat(char)
        self.setFocus()

    def list_format(self, kind: str):
        cursor = self.textCursor()
        cursor.beginEditBlock()
        fmt = QTextListFormat()
        fmt.setStyle(QTextListFormat.Style.ListDecimal if kind == "ordered" else QTextListFormat.Style.ListDisc)
        fmt.setIndent(1)
        if cursor.currentList():
            fmt.setIndent(cursor.currentList().format().indent())
        cursor.createList(fmt)
        if kind == "task":
            block = cursor.blockFormat()
            block.setMarker(QTextBlockFormat.MarkerType.Unchecked)
            cursor.setBlockFormat(block)
        cursor.endEditBlock()
        self.setFocus()

    def quote(self):
        cursor = self.textCursor()
        fmt = cursor.blockFormat()
        fmt.setProperty(QTextFormat.Property.BlockQuoteLevel, 0 if fmt.property(QTextFormat.Property.BlockQuoteLevel) else 1)
        fmt.setLeftMargin(24 if fmt.property(QTextFormat.Property.BlockQuoteLevel) else 0)
        cursor.setBlockFormat(fmt)
        self.setFocus()

    def code_block(self, language: str = ""):
        cursor = self.textCursor()
        fmt = cursor.blockFormat()
        fmt.setProperty(QTextFormat.Property.BlockCodeLanguage, language)
        cursor.setBlockFormat(fmt)
        char = QTextCharFormat()
        char.setFontFamilies(["Consolas", "Menlo", "monospace"])
        char.setFontFixedPitch(True)
        cursor.mergeBlockCharFormat(char)
        self.setFocus()

    def contextMenuEvent(self, event):
        cursor = self.cursorForPosition(event.pos())
        self.setTextCursor(cursor)
        menu = self.createStandardContextMenu()
        image = cursor.charFormat()
        if not image.isImageFormat():
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter)
            if cursor.charFormat().isImageFormat():
                self.setTextCursor(cursor)
                image = cursor.charFormat()
        if image.isImageFormat():
            ref = image.toImageFormat().name()
            menu.addSeparator()
            menu.addAction("Edit / replace image…", self.edit_image.emit)
            menu.addAction("Locate attachment…", lambda:self.locate_image.emit(ref))
            def copy_image():
                resource = self.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(ref))
                if isinstance(resource, QImage):
                    QApplication.clipboard().setImage(resource)
            menu.addAction("Copy image", copy_image)
            menu.addAction("Remove image", lambda: self.remove_image())
        if cursor.currentTable():
            menu.addSeparator()
            menu.addAction("Table controls…", self.table_requested.emit)
        if cursor.charFormat().isAnchor():
            menu.addAction("Edit link…", self.link_requested.emit)
            menu.addAction("Copy link", lambda:QApplication.clipboard().setText(cursor.charFormat().anchorHref()))
        if cursor.blockFormat().property(QTextFormat.Property.BlockCodeLanguage) is not None:
            def copy_code():
                code_cursor = QTextCursor(cursor)
                language = cursor.blockFormat().property(QTextFormat.Property.BlockCodeLanguage)
                block = code_cursor.block()
                while block.previous().isValid() and block.previous().blockFormat().property(QTextFormat.Property.BlockCodeLanguage) == language:
                    block = block.previous()
                values = []
                while block.isValid() and block.blockFormat().property(QTextFormat.Property.BlockCodeLanguage) == language:
                    values.append(block.text())
                    block = block.next()
                QApplication.clipboard().setText("\n".join(values))
            menu.addAction("Copy code block", copy_code)
        menu.exec(event.globalPos())

    def remove_image(self):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
