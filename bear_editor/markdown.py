from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from hashlib import sha256

import yaml
from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin
from PySide6.QtGui import QTextDocument
from .serialization import native_markdown

PARSER = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"]).use(footnote_plugin).use(dollarmath_plugin)
DIALECT = QTextDocument.MarkdownFeature.MarkdownDialectGitHub
RAW_LANGUAGE = "preserved-markdown"


@dataclass
class Section:
    raw: str
    gap: str = "\n\n"
    rendered: str = ""
    canonical: str = ""


def front_matter(markdown: str) -> tuple[str, str, dict]:
    match = re.match(r"\A(?:\ufeff)?---[^\S\r\n]*\r?\n.*?\r?\n(?:---|\.\.\.)[^\S\r\n]*(?:\r?\n|$)", markdown, re.S)
    if not match:
        return "", markdown, {}
    raw = match.group(0)
    remainder = markdown[len(raw):]
    leading = re.match(r"(?:[ \t]*\r?\n)*", remainder).group(0)
    prefix = raw + leading
    try:
        metadata = yaml.safe_load("\n".join(raw.splitlines()[1:-1]))
        if not isinstance(metadata, dict):
            metadata = {}
    except yaml.YAMLError:
        metadata = {}
    return prefix, remainder[len(leading):], metadata


def title_for(markdown: str, filename: str = "", override: str = "", heading_title: bool = True) -> str:
    if override:
        return override
    _, body, metadata = front_matter(markdown)
    if metadata.get("title"):
        return str(metadata["title"])
    if heading_title:
        tokens = PARSER.parse(body)
        for index, token in enumerate(tokens):
            if token.type == "heading_open" and token.tag == "h1":
                return tokens[index + 1].content
    from pathlib import Path
    return Path(filename).stem if filename else "Untitled"


def sections(markdown: str) -> list[Section]:
    """Use parser source spans, including unmatched reference-definition gaps."""
    lines = markdown.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    spans = []
    for token in PARSER.parse(markdown):
        if token.level == 0 and token.map and token.type not in {"inline", "footnote_block_open"}:
            a, b = token.map
            if b <= len(lines) and not any(a >= start and b <= end for start, end in spans):
                spans.append((a, b))
    spans.sort()
    result: list[Section] = []
    end_offset = 0
    for start, end in spans:
        a, b = offsets[start], offsets[end]
        between = markdown[end_offset:a]
        if between.strip():
            result.append(Section(between.rstrip("\r\n"), between[len(between.rstrip("\r\n")):]))
        elif result:
            result[-1].gap += between
        raw = markdown[a:b]
        trimmed = raw.rstrip("\r\n")
        result.append(Section(trimmed, raw[len(trimmed):]))
        end_offset = b
    trailing = markdown[end_offset:]
    if trailing.strip():
        result.append(Section(trailing.rstrip("\r\n"), trailing[len(trailing.rstrip("\r\n")):]))
    elif result:
        result[-1].gap += trailing
    elif markdown:
        result.append(Section(markdown, ""))
    return result


def is_unsupported(raw: str) -> bool:
    tokens = PARSER.parse(raw)
    for token in tokens:
        if token.type.startswith(("html", "math", "footnote")):
            return True
        for child in token.children or []:
            if child.type.startswith(("html", "math", "footnote")):
                return True
    # Preserve references, definition lists, unusual directives, and dangerous URLs.
    return bool(re.search(r"(^\s*\[[^\]]+\]:|\[\^[^\]]+\]|^\s*:\s|^:::+|\]\(\s*(?:javascript|data|file):)", raw, re.M | re.I))


def fence(raw: str, language: str) -> str:
    ticks = "`" * max(3, max((len(x) + 1 for x in re.findall(r"`+", raw)), default=3))
    return f"{ticks}{language}\n{raw}\n{ticks}"


def canonical(markdown: str) -> str:
    document = QTextDocument()
    document.setMarkdown(markdown, DIALECT)
    return native_markdown(document).strip()


def signature(markdown: str) -> str:
    return sha256(markdown.replace("\r\n", "\n").strip().encode()).hexdigest()


class MarkdownSession:
    """Retain original source of unchanged top-level blocks after native editing.

    QTextDocument is the rich text model. markdown-it supplies source spans.
    Unsupported syntax is rendered in editable, explicitly labelled code blocks.
    """
    def __init__(self) -> None:
        self.original = ""
        self.prefix = ""
        self.initial = ""
        self.blocks: list[Section] = []
        self.eol = "\n"
        self.raw_languages: set[str] = set()

    def prepare(self, markdown: str) -> str:
        self.original = markdown
        self.eol = "\r\n" if "\r\n" in markdown else "\n"
        self.prefix, body, _ = front_matter(markdown)
        leading = re.match(r"\s*", body).group(0)
        if leading:
            self.prefix += leading
            body = body[len(leading):]
        self.blocks = sections(body)
        environment = {}
        PARSER.parse(body, environment)
        references = environment.get("references", {})
        self.raw_languages = set()
        for index, block in enumerate(self.blocks):
            raw = block.raw
            reference_use = bool(re.search(r"!?\[[^\]\n]+\]\s*\[[^\]\n]*\]", raw))
            if not reference_use:
                reference_use = any(re.search(r"\[" + re.escape(key) + r"\]", raw, re.I) for key in references)
            if is_unsupported(raw) or reference_use:
                language = f"{RAW_LANGUAGE}-{index}"
                self.raw_languages.add(language)
                block.rendered = fence(raw, language)
            else:
                # Apple-style embeds are rendered with their actual relative reference.
                block.rendered = re.sub(r"!\[\[([^\]\n]+)\]\]", lambda m: f"![](<{m[1]}>)", raw)
            block.canonical = canonical(block.rendered)
        return "\n\n".join(block.rendered for block in self.blocks)

    def baseline(self, document: QTextDocument) -> None:
        self.initial = native_markdown(document)

    def serialize(self, document: QTextDocument, normalize: bool = False) -> str:
        markdown = native_markdown(document)
        if markdown == self.initial and not normalize:
            return self.original
        current = sections(markdown)
        originals = [signature(block.canonical) for block in self.blocks]
        current_keys = [signature(block.raw) for block in current]
        matcher = difflib.SequenceMatcher(a=originals, b=current_keys, autojunk=False)
        preserved: dict[int, Section] = {}
        if not normalize:
            for matching in matcher.get_matching_blocks():
                for offset in range(matching.size):
                    preserved[matching.b + offset] = self.blocks[matching.a + offset]
        chunks: list[str] = []
        for index, block in enumerate(current):
            old = preserved.get(index)
            if old:
                value, gap = old.raw, old.gap
            else:
                value = self.unwrap_raw(block.raw)
                # Maintain Apple wikilink embeds even if another part of the block changed.
                for original in self.blocks:
                    for match in re.finditer(r"!\[\[([^\]\n]+)\]\]", original.raw):
                        normalized = canonical(f"![](<{match[1]}>)")
                        value = value.replace(normalized, match[0])
                value = value.replace("\r\n", "\n").replace("\n", self.eol)
                gap = self.eol * 2 if index < len(current) - 1 else self.eol
            if index < len(current) - 1 and not gap:
                gap = self.eol * 2
            chunks.append(value + gap)
        output = self.prefix + "".join(chunks)
        if not output.strip() and document.toPlainText().strip():
            raise ValueError("Save stopped because the generated Markdown appears invalid.")
        return output

    def unwrap_raw(self, value: str) -> str:
        tokens = PARSER.parse(value)
        if len(tokens) == 1 and tokens[0].type == "fence" and tokens[0].info in self.raw_languages:
            return tokens[0].content.rstrip("\n")
        return value


def image_references(markdown: str) -> list[str]:
    _, body, _ = front_matter(markdown)
    refs = []
    for token in PARSER.parse(body):
        for child in token.children or []:
            if child.type == "image":
                refs.append(child.attrGet("src") or "")
    refs.extend(re.findall(r"!\[\[([^\]\n]+)\]\]", body))
    return list(dict.fromkeys(refs))
