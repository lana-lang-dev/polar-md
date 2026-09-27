from pathlib import Path
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QTextCursor, QTextBlockFormat, QImage, QColor
from PySide6.QtTest import QTest
from bear_editor.models import DocumentState
from bear_editor.serialization import tables


def test_typing_bold_and_undo(window):
    window.editor.insertPlainText('Hello world')
    cursor = window.editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(5,QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)
    window.editor.mark('bold')
    assert '**Hello**' in window.flush().markdown
    window.editor.undo()
    assert '**Hello**' not in window.flush().markdown


def test_task_click_autosave_recovery(window, app):
    markdown = '- [ ] Incomplete\n- [x] Complete\n'
    window.install(DocumentState(markdown=markdown,saved_markdown=markdown),False)
    cursor=window.editor.textCursor()
    cursor.setPosition(0)
    window.editor.setTextCursor(cursor)
    app.processEvents()
    rect=window.editor.cursorRect(cursor)
    QTest.mouseClick(window.editor.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(max(2,rect.x()-12),rect.center().y()))
    window.autosave()
    assert '- [x] Incomplete' in window.storage.load_draft().markdown
    window.set_mode('Markdown')
    window.set_mode('Write')
    assert window.editor.document().begin().blockFormat().marker() == QTextBlockFormat.MarkerType.Checked


def test_source_sync_and_cursor_not_reset_by_rich_typing(window):
    window.set_mode('Split')
    window.source.setPlainText('# Title\n\nBody')
    window.flush()
    assert 'Title' in window.editor.toPlainText()
    cursor=window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.editor.setTextCursor(cursor)
    window.editor.insertPlainText(' edited')
    position=window.editor.textCursor().position()
    window.flush()
    assert window.editor.textCursor().position()==position
    assert 'Body edited' in window.source.toPlainText()


def test_new_table_and_tab_adds_row(window):
    cursor=window.editor.textCursor()
    table=cursor.insertTable(4,3)
    table.cellAt(1,0).firstCursorPosition().insertText('A | B')
    window.editor.setTextCursor(table.cellAt(3,2).lastCursorPosition())
    QTest.keyClick(window.editor,Qt.Key.Key_Tab)
    assert table.rows()==5
    markdown=window.flush().markdown
    assert 'A \\| B' in markdown
    window.load_rich(markdown)
    assert tables(window.editor.document())[0].columns()==3


def test_highlight_roundtrip(window):
    window.editor.insertPlainText('Important')
    window.editor.selectAll()
    window.editor.mark('highlight')
    markdown=window.flush().markdown
    assert '==Important==' in markdown
    window.load_rich(markdown)
    window.editor.moveCursor(QTextCursor.MoveOperation.End)
    window.editor.insertPlainText(' more')
    assert '==Important' in window.flush().markdown


def test_save_and_image_strategy(window,tmp_path):
    image=QImage(20,20,QImage.Format.Format_ARGB32)
    image.fill(QColor('blue'))
    window.paste_image(image)
    window.flush()
    assert 'Attachments/image-' in window.state.markdown
    destination=tmp_path/'saved'/'note.md'
    destination.parent.mkdir()
    window.state.path=str(destination)
    assert window.save()
    assert destination.is_file()
    assert list((destination.parent/'Attachments').glob('*.png'))
    assert 'data:' not in destination.read_text()


def test_markdown_shortcuts(window):
    QTest.keyClicks(window.editor,'# ')
    QTest.keyClicks(window.editor,'Heading')
    assert window.editor.textCursor().blockFormat().headingLevel()==1
    assert '# Heading' in window.flush().markdown
