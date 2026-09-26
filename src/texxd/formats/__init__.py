"""Format detection and structure.

A Format recognises a kind of data and can describe its structure as a list of
entries, some of which can be opened as child nodes.
"""

from dataclasses import dataclass
from typing import Optional

from ..data import Data


@dataclass(frozen=True)
class Entry:
    """A named region inside some data, like a file in an archive.

    Attributes:
        name: Display name.
        start: Offset of the entry's whole region, including any header.
        stop: End of the whole region.
        data_start: Offset of the entry's contents.
        data_size: Size of the entry's contents.
        kind: Short description, like "file" or "dir".
        openable: True if the contents can be opened as a child node.
        info: Extra format-specific columns for display.
    """

    name: str
    start: int
    stop: int
    data_start: int
    data_size: int
    kind: str = "file"
    openable: bool = True
    info: tuple = ()

    def contains(self, offset: int) -> bool:
        """True if ``offset`` falls within this entry's whole region."""
        return self.start <= offset < self.stop


class Format:
    """Base class for formats."""

    name = "binary"
    # True if entries() describes structure worth showing in its own column
    has_entries = False

    @classmethod
    def sniff(cls, data: Data) -> float:
        """How confident we are that ``data`` is this format, 0 to 1."""
        return 0.0

    @classmethod
    def entries(cls, data: Data) -> list[Entry]:
        """Parse the structure into entries. Formats without structure return []."""
        return []


class Binary(Format):
    """Raw bytes. Everything is binary."""

    name = "binary"

    @classmethod
    def sniff(cls, data: Data) -> float:
        return 0.01


def registry() -> list[type[Format]]:
    """All known formats."""
    from .tar import Tar

    return [Tar, Binary]


def detect(data: Data) -> list[type[Format]]:
    """Formats that ``data`` could be, most likely first. Binary is always last."""
    scored = []
    for fmt in registry():
        try:
            score = fmt.sniff(data)
        except Exception:
            score = 0.0
        if score > 0 and fmt is not Binary:
            scored.append((score, fmt))
    scored.sort(key=lambda s: -s[0])
    return [fmt for _, fmt in scored] + [Binary]


def by_name(name: str) -> Optional[type[Format]]:
    """Look up a format by name."""
    for fmt in registry():
        if fmt.name == name:
            return fmt
    return None
