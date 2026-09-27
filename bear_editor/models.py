from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass
class Preferences:
    dark: bool = False
    font: str = "Sans"
    size: str = "Medium"
    width: str = "Normal"
    spacing: str = "Normal"
    bubble: bool = True
    strike_tasks: bool = True
    typewriter: bool = False
    auto_save_disk: bool = False
    heading_title: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "Preferences":
        return cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})


@dataclass
class DocumentState:
    id: str = field(default_factory=lambda: uuid4().hex)
    markdown: str = ""
    saved_markdown: str = ""
    title: str = ""
    path: str = ""
    mode: str = "Write"
    scroll: int = 0
    cursor: int = 0
    # Markdown reference -> managed absolute file, never written into Markdown.
    attachments: dict[str, str] = field(default_factory=dict)

    @property
    def dirty(self) -> bool:
        return self.markdown != self.saved_markdown

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentState":
        return cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})
