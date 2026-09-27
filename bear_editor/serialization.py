from __future__ import annotations

import re
from PySide6.QtGui import QTextDocument, QTextCursor, QTextTable, QTextFormat, QTextBlockFormat, QTextListFormat

DIALECT = QTextDocument.MarkdownFeature.MarkdownDialectGitHub
HIGHLIGHT_PROPERTY = int(QTextFormat.Property.UserProperty) + 100


def tables(document: QTextDocument) -> list[QTextTable]:
    found = []
    def walk(frame):
        for child in frame.childFrames():
            if isinstance(child, QTextTable):
                found.append(child)
            else:
                walk(child)
    walk(document.rootFrame())
    return found


def table_markdown(table: QTextTable) -> str:
    rows = []
    for row in range(table.rows()):
        values = []
        for column in range(table.columns()):
            cell = table.cellAt(row, column)
            cursor = cell.firstCursorPosition()
            cursor.setPosition(cell.lastCursorPosition().position(), QTextCursor.MoveMode.KeepAnchor)
            doc = QTextDocument()
            QTextCursor(doc).insertFragment(cursor.selection())
            value = doc.toMarkdown(DIALECT).strip()
            value = re.sub(r"(?<!\\)\|", r"\\|", value)
            values.append(value.replace("\n", "<br>"))
        rows.append("| " + " | ".join(values) + " |")
    if rows:
        rows.insert(1, "| " + " | ".join(["---"] * table.columns()) + " |")
    return "\n".join(rows)


def native_markdown(document: QTextDocument) -> str:
    """Patch Qt's table-pipe exporter and add a portable ==highlight== mark."""
    if not tables(document) and not list_groups(document) and not has_highlights(document):
        return document.toMarkdown(DIALECT)
    clone = document.clone()
    highlights = []
    block = clone.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().property(HIGHLIGHT_PROPERTY):
                highlights.append((fragment.position(), fragment.length()))
            iterator += 1
        block = block.next()
    for position, length in reversed(highlights):
        cursor = QTextCursor(clone)
        cursor.setPosition(position + length)
        cursor.insertText("==")
        cursor.setPosition(position)
        cursor.insertText("==")
    replacements = {}
    spans = []
    for index, table in enumerate(tables(clone)):
        key = f"BEARTABLETOKEN{index}END"
        replacements[key] = table_markdown(table)
        spans.append((table.firstPosition()-1, table.lastPosition()+1, key))
    for index, group in enumerate(list_groups(clone)):
        start, end = group[0].position(), group[-1].position()+group[-1].length()-1
        if any(a <= start <= b for a,b,_ in spans):
            continue
        key = f"BEARLISTTOKEN{index}END"
        replacements[key] = list_markdown(group)
        spans.append((start,end,key))
    for start, end, key in sorted(spans,reverse=True):
        cursor = QTextCursor(clone)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        # Detach the placeholder from the old QTextList.
        if cursor.currentList():
            cursor.currentList().remove(cursor.block())
        cursor.setBlockFormat(QTextBlockFormat())
        cursor.insertText(f"\n\n{key}\n\n")
    result = clone.toMarkdown(DIALECT)
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result


def has_highlights(document: QTextDocument) -> bool:
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().property(HIGHLIGHT_PROPERTY):
                return True
            iterator += 1
        block = block.next()
    return False


def list_groups(document):
    groups = []
    group = []
    block = document.begin()
    while block.isValid():
        if block.textList():
            group.append(block)
        elif group:
            groups.append(group)
            group=[]
        block=block.next()
    if group:
        groups.append(group)
    return groups


def inline_markdown(block):
    cursor=QTextCursor(block)
    cursor.setPosition(block.position()+block.length()-1,QTextCursor.MoveMode.KeepAnchor)
    doc=QTextDocument()
    target=QTextCursor(doc)
    target.insertFragment(cursor.selection())
    if target.currentList():
        target.currentList().remove(target.block())
    target.setBlockFormat(QTextBlockFormat())
    return doc.toMarkdown(DIALECT).strip()


def list_markdown(blocks):
    output=[]
    widths={}
    for block in blocks:
        listing=block.textList()
        fmt=listing.format()
        depth=max(1,fmt.indent())
        ordered=fmt.style() in {QTextListFormat.Style.ListDecimal,QTextListFormat.Style.ListLowerAlpha,QTextListFormat.Style.ListUpperAlpha,QTextListFormat.Style.ListLowerRoman,QTextListFormat.Style.ListUpperRoman}
        number=listing.itemNumber(block)+(fmt.start() if hasattr(fmt,'start') else 1)
        marker=f"{number}. " if ordered else "- "
        prefix=' '*sum(widths.get(level,2) for level in range(1,depth))
        widths[depth]=len(marker)
        task=block.blockFormat().marker()
        if task!=QTextBlockFormat.MarkerType.NoMarker:
            marker+='[x] ' if task==QTextBlockFormat.MarkerType.Checked else '[ ] '
        text=inline_markdown(block)
        lines=text.splitlines() or ['']
        output.append(prefix+marker+lines[0])
        output.extend(prefix+' '*widths[depth]+line for line in lines[1:])
    return '\n'.join(output)
