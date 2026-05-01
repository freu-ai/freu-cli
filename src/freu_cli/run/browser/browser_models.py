from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class BrowserSessionConfig:
    headless: bool = False


@dataclass(slots=True)
class BrowserPageInfo:
    url: str
    title: str


@dataclass(slots=True)
class BrowserElementState:
    exists: bool
    visible: bool


@dataclass(slots=True)
class BrowserDomNode:
    tag: str
    text: str
    selector: str
    visible: bool
    attributes: dict[str, str] = field(default_factory=dict)
