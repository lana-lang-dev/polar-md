from pathlib import Path
import time

import pytest
from PySide6.QtGui import QTextDocument, QTextCursor, QTextBlockFormat
from bear_editor.markdown import MarkdownSession, front_matter, image_references, title_for
from bear_editor.serialization import native_markdown, tables

FIXTURES = Path(__file__).parent / 'fixtures'


def opened(markdown):
    session = MarkdownSession()
    document = QTextDocument()
    document.setMarkdown(session.prepare(markdown))
    session.baseline(document)
    return session, document


@pytest.mark.parametrize('path', sorted(FIXTURES.glob('*.md')), ids=lambda p:p.name)
def test_exact_noop_round_trip(path):
    markdown = path.read_text()
    session, document = opened(markdown)
    assert session.serialize(document) == markdown


def test_task_toggle_reopen():
    session, document = opened('- [ ] Incomplete\n- [x] Complete\n')
    cursor = QTextCursor(document)
    fmt = cursor.blockFormat()
    assert fmt.marker() == QTextBlockFormat.MarkerType.Unchecked
    fmt.setMarker(QTextBlockFormat.MarkerType.Checked)
    cursor.setBlockFormat(fmt)
    value = session.serialize(document)
    assert '- [x] Incomplete' in value
    reopened = opened(value)[1]
    assert reopened.begin().blockFormat().marker() == QTextBlockFormat.MarkerType.Checked


def test_edit_table_preserves_pipes():
    session, document = opened('| Name | Status |\n| --- | --- |\n| A \\| B | Done |\n')
    table = tables(document)[0]
    cursor = table.cellAt(1, 1).firstCursorPosition()
    cursor.insertText('Almost ')
    result = session.serialize(document)
    assert 'A \\| B' in result
    reopened = opened(result)[1]
    assert tables(reopened)[0].columns() == 2
    assert 'Almost Done' in reopened.toPlainText()


def test_unchanged_table_keeps_original_source_after_edit_elsewhere():
    table = '| A | B |\n| --- | --- |\n| x \\| y | z |\n\n'
    session, document = opened(table + 'End\n')
    cursor = QTextCursor(document)
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(' edited')
    assert session.serialize(document).startswith(table)


def test_frontmatter_and_unsupported_preserved_after_edit():
    source = '---\ntitle: "Research"\nunknown: 42 # keep\n---\n\nParagraph\n\nFootnote[^a].\n\n[^a]: Definition\n\n$$\nx^2\n$$\n\n<div onclick="alert(1)">HTML</div>\n'
    session, document = opened(source)
    cursor = QTextCursor(document)
    cursor.insertText('Edited ')
    result = session.serialize(document)
    assert result.startswith(front_matter(source)[0])
    assert 'Edited Paragraph' in result
    for raw in ['Footnote[^a].','[^a]: Definition','$$\nx^2\n$$','<div onclick="alert(1)">HTML</div>']:
        assert raw in result
    assert 'preserved-markdown' not in result


def test_unsupported_raw_edit_survives():
    session, document = opened('$$\nx^2\n$$\n')
    cursor = document.find('x^2')
    cursor.insertText('x^3')
    assert '$$\nx^3\n$$' in session.serialize(document)


def test_nested_lists_remain_nested():
    session, document = opened('1. First\n2. Second\n   - Nested\n   - Other\n')
    cursor = document.find('Nested')
    cursor.insertText('Deep')
    output = session.serialize(document)
    reopened = opened(output)[1]
    block = reopened.find('Deep').block()
    assert block.textList().format().indent() >= 2


def test_wiki_image_original_reference_preserved():
    session, document = opened('![[attachments/sketch.png]]\n\nOther\n')
    cursor = QTextCursor(document)
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(' edited')
    assert '![[attachments/sketch.png]]' in session.serialize(document)


def test_crlf_kept_after_edit():
    session, document = opened('# Title\r\n\r\nBody\r\n')
    cursor = QTextCursor(document)
    cursor.insertText('New ')
    assert session.serialize(document) == '# New Title\r\n\r\nBody\r\n'


def test_code_language_and_spacing():
    session, document = opened('```python\n  x = "🐻"\n\nprint(x)\n```\n\nEnd\n')
    cursor = QTextCursor(document)
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(' edited')
    assert '```python\n  x = "🐻"\n\nprint(x)\n```' in session.serialize(document)


def test_title_priority_and_metadata():
    assert title_for('---\ntitle: Metadata\n---\n\n# Heading','file.md') == 'Metadata'
    assert title_for('# Heading','file.md') == 'Heading'
    assert title_for('body','file.md') == 'file'
    assert title_for('') == 'Untitled'
    assert front_matter('---\nmalformed: [\n---\nBody')[0]


def test_attachment_discovery():
    assert image_references('![x](./Attachments/a.png)\n\n![[attachments/b.png]]') == ['./Attachments/a.png', 'attachments/b.png']


def test_ten_thousand_words():
    source = '\n\n'.join([' '.join(['word'] * 50)] * 200)
    start = time.monotonic()
    session, document = opened(source)
    cursor = QTextCursor(document)
    cursor.insertText('Edited ')
    assert session.serialize(document).startswith('Edited word')
    assert time.monotonic() - start < 5


def test_reference_links_survive_unrelated_edit():
    source='[Example][target]\n\n[target]: https://example.com "Title"\n\nParagraph\n'
    session,document=opened(source)
    cursor=QTextCursor(document)
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(' edited')
    output=session.serialize(document)
    assert '[Example][target]' in output
    assert '[target]: https://example.com "Title"' in output


def test_leading_blank_lines_preserved_on_edit():
    session,document=opened('\n\nBody\n')
    QTextCursor(document).insertText('New ')
    assert session.serialize(document).startswith('\n\nNew Body')
