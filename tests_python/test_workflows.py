import copy
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor, QTextBlockFormat, QTextDocument
from PySide6.QtTest import QTest
from bear_editor.models import DocumentState
from bear_editor.serialization import HIGHLIGHT_PROPERTY
from bear_editor.markdown import MarkdownSession


def test_recovery_banner_restore_and_discard(window):
    dirty=DocumentState(markdown='Recovered writing',saved_markdown='Old file')
    window.recovered=copy.deepcopy(dirty)
    window.recover(True)
    assert window.state.markdown=='Recovered writing'
    assert window.state.dirty
    window.recovered=copy.deepcopy(dirty)
    window.recover(False)
    assert window.state.markdown=='Old file'
    assert window.storage.versions(dirty.id)


def test_task_input_shortcut(window):
    QTest.keyClicks(window.editor,'- [ ] ')
    QTest.keyClicks(window.editor,'Task')
    assert window.editor.textCursor().blockFormat().marker()==QTextBlockFormat.MarkerType.Unchecked
    assert '- [ ] Task' in window.flush().markdown


def test_code_fence_input_and_language(window):
    QTest.keyClicks(window.editor,'```python')
    QTest.keyClick(window.editor,Qt.Key.Key_Return)
    QTest.keyClicks(window.editor,'print(42)')
    assert '```python\nprint(42)' in window.flush().markdown


def test_unicode_before_highlight(window):
    source='🐻 日本語 ==important== and more'
    window.install(DocumentState(markdown=source,saved_markdown=source),False)
    assert window.editor.toPlainText()=='🐻 日本語 important and more'
    cursor=window.editor.document().find('important')
    assert cursor.charFormat().property(HIGHLIGHT_PROPERTY)
    cursor=window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.editor.setTextCursor(cursor)
    window.editor.insertPlainText('!')
    assert '🐻 日本語 ==important==' in window.flush().markdown


def test_settings_do_not_change_markdown(window):
    source='# Title\n\n- [ ] Task\n'
    window.install(DocumentState(markdown=source,saved_markdown=source),False)
    window.preferences.spacing='Relaxed'
    window.toggle_dark()
    assert window.flush().markdown==source
    assert not window.state.dirty


def test_save_preserves_frontmatter_and_unknown_while_editing_body(window,tmp_path):
    source='---\ntitle: Research\nunknown: yes # keep\n---\n\nBody\n\n[^n]: Keep definition\n'
    path=tmp_path/'note.md'
    path.write_text(source)
    window.open_path(path)
    cursor=window.editor.document().find('Body')
    cursor.insertText('Edited body')
    assert window.save()
    saved=path.read_text()
    assert 'unknown: yes # keep' in saved
    assert '[^n]: Keep definition' in saved
    assert 'Edited body' in saved


def test_save_as_copies_assets_without_overwriting_conflicts(window,tmp_path):
    original=tmp_path/'original'
    original.mkdir()
    asset=original/'Attachments'/'a.png'
    asset.parent.mkdir()
    asset.write_bytes(b'image')
    window.state=DocumentState(path=str(original/'note.md'),markdown='![a](Attachments/a.png)')
    target=tmp_path/'copy'/'note.md'
    window.save_attachments(target)
    assert (target.parent/'Attachments'/'a.png').read_bytes()==b'image'
    (target.parent/'Attachments'/'a.png').write_bytes(b'different')
    import pytest
    with pytest.raises(ValueError,match='different contents'):
        window.save_attachments(target)
