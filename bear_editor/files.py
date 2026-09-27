from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

MARKDOWN_SUFFIXES = {".md", ".markdown", ".txt"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"}


def read_markdown(path: Path) -> str:
    if path.suffix.lower() not in MARKDOWN_SUFFIXES:
        raise ValueError("Choose a .md, .markdown, or .txt file.")
    data = path.read_bytes()
    if b"\x00" in data:
        raise ValueError("This appears to be a binary file, not Markdown.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("This file is not UTF-8. Export it as UTF-8 text and try again.") from error


def atomic_write(path: Path, content: str, has_content: bool = False) -> None:
    if (has_content and not content.strip()) or "\x00" in content:
        raise ValueError("Save stopped because the generated Markdown appears invalid.")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_mode = path.stat().st_mode if path.exists() else None
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if previous_mode is not None:
            os.chmod(temporary, previous_mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def file_digest(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path and Path(path).is_file() else ""


def path_key(path: str) -> str:
    return unicodedata.normalize("NFC", unquote(path).replace("\\", "/")).casefold()


def resolve_attachment(reference: str, note_path: str, mappings: dict[str, str]) -> Path | None:
    ref = unquote(reference).replace("\\", "/")
    for key, mapped in mappings.items():
        if path_key(key) == path_key(ref) and Path(mapped).is_file():
            return Path(mapped)
    if urlsplit(ref).scheme or ref.startswith("//"):
        return None
    if note_path:
        candidate = Path(note_path).parent / ref
        if candidate.is_file():
            return candidate
        # Resolve each path component case-insensitively; ambiguous matches stay missing.
        current = Path(note_path).parent
        for part in PurePosixPath(ref).parts:
            if part == ".":
                continue
            if part == "..":
                current = current.parent
                continue
            if not current.is_dir():
                return None
            matches = [entry for entry in current.iterdir() if path_key(entry.name) == path_key(part)]
            if len(matches) != 1:
                return None
            current = matches[0]
        return current if current.is_file() else None
    return None


def safe_url(value: str, image: bool = False) -> bool:
    compact = "".join(c for c in value.strip() if ord(c) > 32)
    if not compact or compact.startswith("//"):
        return False
    scheme = urlsplit(compact).scheme.lower()
    return scheme in ({"", "http", "https"} if image else {"", "http", "https", "mailto", "tel", "notes"})


def unique_destination(folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    result = folder / Path(filename).name
    index = 1
    while result.exists():
        result = folder / f"{Path(filename).stem}-{index}{Path(filename).suffix}"
        index += 1
    return result


def copy_asset(source: Path, folder: Path) -> Path:
    destination = unique_destination(folder, source.name)
    shutil.copy2(source, destination)
    return destination


def write_bundle(path: Path, markdown: str, name: str, assets: dict[str, Path]) -> None:
    """A portable ZIP; unsafe external paths are rejected instead of silently renamed."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(Path(name).name, markdown)
        for ref, source in assets.items():
            relative = PurePosixPath(unquote(ref).replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or urlsplit(ref).scheme:
                raise ValueError(f"Attachment path cannot be bundled safely: {ref}")
            if source.is_file():
                archive.write(source, str(relative))
