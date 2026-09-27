# Bear Markdown — native Python desktop edition

A single-document Markdown editor built with **Python and PySide6**. No browser, web server, Node.js, npm, or JavaScript runtime is needed to run the desktop application.

The interface keeps one centered writing canvas, a quiet navy-accented toolbar, and temporary dialogs. Markdown and split views are optional. It does not create a notes database or permanent sidebar.

## Run on your work computer

Requires **Python 3.10 or newer** and permission to install Python packages. PySide6 includes Qt; you do not need a separate Qt installation. Use a supported 64-bit Windows, macOS, or Linux machine.

Extract `bear-markdown-desktop.zip` into a folder you can write to, then open a terminal in that folder.

**Windows — PowerShell or Command Prompt:**

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python run.py
```

**macOS / Linux:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

To open a document immediately:

```powershell
.venv\Scripts\python run.py "C:\Users\you\Documents\My Note.md"
```

After the first installation, the editor runs offline. Referenced web images and opening external links still require network access. On managed work computers, use your organization's approved Python package source if public PyPI is restricted.

On Linux, a working desktop Qt platform plugin and its system libraries are required. If Qt reports an `xcb` plugin dependency error, install the missing system library using your organization's approved package manager. Headless testing uses `QT_QPA_PLATFORM=offscreen`.

## Editing

- Native rich-text document model, undo/redo, selection-preserving formatting, headings, bold, italic, strike, highlight, inline/fenced code, lists, nested lists, tasks, links, images, quotes, rules, and tables.
- `Write`, `Markdown`, and `Split` modes. Switching views does not rebuild the rich editor unless the source actually changed.
- Markdown shortcuts: headings, lists, task lists, quotes, code fences, inline marks, and horizontal rules. Tab/Shift+Tab navigates tables and indents/outdents lists. Tab from the last table cell adds a row.
- Click task checkboxes to update Markdown. Completed-task strike is a presentation option.
- Find/replace with case/whole-word options; document outline; fuzzy command palette; font, size, width, spacing, dark mode, focus mode, and optional typewriter mode.
- Code highlighting through Pygments, with code-copy in the context menu. Inline tags remain ordinary Markdown text.

## Files, Apple Notes, and attachments

Use **File → Open Markdown** for `.md`, `.markdown`, and UTF-8 Markdown `.txt` files. Normal native OS file dialogs work across supported platforms.

Use **Import Apple Notes Export** to select exported notes and optional attachments, or **Import Folder** to scan an export folder. A temporary chooser opens one note at a time. The importer does not connect to Apple Notes or require Apple software.

- YAML front matter stays hidden in Write mode, appears in Document Info, and remains editable in Markdown view. Existing unknown keys/comments are preserved.
- Relative image paths are resolved against the Markdown file, including case-insensitive component matching. `![[attachment.png]]` embeds are rendered while their original syntax is preserved. Ordinary wiki links remain visible and retain their source.
- Missing image references remain in the file. Right-click the image placeholder to locate its file.
- Uploaded/pasted images go into `Attachments/` next to an opened document. Untitled-draft images go into managed local storage. **Save/Save As copies managed images beside the Markdown file**, preserving relative references and refusing to overwrite conflicting attachment contents.
- Image controls provide URL/upload, alt text, caption metadata, and size presets. Image size presets are presentation settings and are not serialized into proprietary Markdown.
- Export a **Markdown + attachments ZIP** for a portable package. This does not embed base64 images into Markdown. HTML export embeds available local images for portability.
- Imported links with `notes://` remain intact. Ctrl/Cmd-click opens links via the OS; link controls also copy the URL.

## Markdown fidelity

Markdown is canonical. `markdown-it-py` supplies source spans, while Qt's `QTextDocument` handles native editing.

Opening and saving without edits returns the original source. After edits, unchanged top-level blocks retain their original source. Changed blocks are serialized to Markdown; whitespace inside an edited block may normalize. A dedicated serializer fixes Qt's unescaped table-pipe output and nested ordered-list indentation.

Footnotes, math, raw HTML, reference definitions, and other recognized unsupported constructs appear as editable **preserved Markdown code blocks**. They are restored to their original raw syntax on save; internal preservation labels are not exported. Raw HTML is not executed. Unknown syntax is kept conservatively rather than silently removed.

Caveats of the native editor:

- This is a native Qt implementation, not TipTap embedded in a desktop browser.
- Standard Markdown tables always serialize with a first-row header. Complex merged cells and multiline rich table content are outside the standard Markdown table model.
- Syntax highlighting is line-oriented. It is presentation only and does not change code.
- Network images load asynchronously. A blocked or unsupported image stays a placeholder and retains its reference.
- Preference changes can add a formatting-only undo step; they do not change Markdown.

## Saving and recovery

**Save** writes the opened file; **Save As** chooses a new path. Saves use a temporary file, flush it to disk, then atomically replace the destination. An unexpectedly empty serialization will not overwrite a nonempty editor. External file changes are detected before overwriting.

Local recovery uses **SQLite**, not IndexedDB. Autosave is debounced by 600 ms. It records Markdown, title, document identity, preferences, mode, cursor/scroll, and attachment mappings. It does not write to your Markdown file unless **Auto-save opened files** is enabled; that preference defaults to off.

Unsaved recovery prompts Restore/Discard on startup. History stores the last 30 distinct snapshots per document, periodically and before replacement/restoration. Restoring creates a backup first. Closing/replacing dirty documents prompts for save, discard, or cancel.

Data lives in Qt's application-local data directory, typically:

- Windows: `%LOCALAPPDATA%\BearMarkdown\Bear Markdown`
- macOS: `~/Library/Application Support/BearMarkdown/Bear Markdown`
- Linux: `~/.local/share/BearMarkdown/Bear Markdown`

Document Info shows the actual location for untitled drafts. To choose a different directory:

```bash
python run.py --data-dir /path/to/editor-data
```

The editor does not automatically delete stored image files, since older snapshots may still reference them. Keep the data directory if you want recovery/history to remain available.

## Shortcuts

| Action | Shortcut |
| --- | --- |
| Open / Save / Save As | Ctrl/Cmd+O / S / Shift+S |
| New document | Ctrl/Cmd+N |
| Bold / Italic | Ctrl/Cmd+B / I |
| Strike / Highlight | Ctrl/Cmd+Shift+X / H |
| Heading 1–3 | Ctrl/Cmd+Alt+1–3 |
| Commands | Ctrl/Cmd+K |
| Find | Ctrl/Cmd+F |
| Outline | Ctrl/Cmd+Shift+O |
| Focus mode | Ctrl/Cmd+Shift+F |
| Undo / Redo | Native platform editor shortcuts |
| Print / PDF | Ctrl/Cmd+P |
| Close dialog | Escape |

## Development and verification

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q
python -m compileall -q bear_editor run.py
```

Headless Linux:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

Tests exercise the actual Qt document/widgets as well as Markdown serialization, tables, tasks, selection formatting/undo, keyboard shortcuts, metadata and unsupported-syntax preservation, images, atomic saves, recovery/history, and 10,000-word documents. Windows/macOS native dialogs and printer drivers still require testing on those platforms.

Source layout:

- `run.py`, `bear_editor/app.py`: launch and application lifecycle
- `bear_editor/window.py`: single-document window and workflows
- `bear_editor/editor.py`: native editing behaviors, attachment loading, highlighting
- `bear_editor/markdown.py`, `serialization.py`: source preservation and safe Markdown output
- `bear_editor/files.py`: atomic saves, imports, asset resolution, portable bundles
- `bear_editor/storage.py`: SQLite recovery, preferences, version history
- `bear_editor/dialogs.py`: transient dialogs and command palette
- `tests_python/`: native unit/integration tests and Markdown fixtures

The earlier web source remains in the repository for reference. It is excluded from the Python distribution and is not used by `run.py`.
