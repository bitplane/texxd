"""Format detection and structure.

A Format recognises a kind of data and describes its structure as a tree of
regions. Some regions can be opened, giving the data for a child node.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from ..data import Data, ResizeError, Window


@dataclass(frozen=True)
class Region:
    """A named range of some data, like a file in an archive or a field in a header.

    Attributes:
        name: Display name.
        start: Offset of the whole region, including any header.
        stop: End of the whole region.
        data_start: Offset of the region's contents (after its header).
        data_size: Size of the region's contents.
        kind: Short description, like "file" or "dir".
        openable: True if the contents can be opened as a child node.
        value: The decoded value, for fields.
        info: Extra format-specific columns for display.
        children: Regions nested inside this one, in order.
    """

    name: str
    start: int
    stop: int
    data_start: int
    data_size: int
    kind: str = "file"
    openable: bool = False
    value: Any = None
    info: tuple = ()
    children: tuple["Region", ...] = field(default=(), compare=False)

    @property
    def data_stop(self) -> int:
        return self.data_start + self.data_size

    def contains(self, offset: int) -> bool:
        """True if ``offset`` falls within the whole region."""
        return self.start <= offset < self.stop


class Format:
    """Base class for formats."""

    name = "binary"
    # True if regions() describes structure worth showing
    has_regions = False
    # True if fixup() can cope with an opened region's contents changing size
    can_resize = False
    # what the data opened from its regions is: "bytes", or "text" (always UTF-8)
    contents = "bytes"
    # False if data opened by this format shouldn't be detected as this format again,
    # or as any other non-nesting format with the same contents (text as text again)
    nests = True
    # True if regions() lists every region it can open; if not, find_region() finds one
    listed = True

    @classmethod
    def sniff(cls, data: Data) -> float:
        """How confident we are that ``data`` is this format, 0 to 1."""
        return 0.0

    @classmethod
    def regions(cls, data: Data) -> list[Region]:
        """Parse the structure into top level regions, in order. Formats without structure return []."""
        return []

    @classmethod
    def find_region(cls, data: Data, data_start: int, name: str) -> Optional[Region]:
        """For formats that don't list their regions: the region whose contents start at ``data_start``."""
        return None

    @classmethod
    def for_region(cls, region: Region) -> type["Format"]:
        """The format to open ``region`` with: this one, or a more particular one it found."""
        return cls

    @classmethod
    def open(cls, data: Data, region: Region) -> Data:
        """The data for an openable region: by default, its contents, in place."""
        return Window(data, region.data_start, region.data_size)

    @classmethod
    def fixup(cls, data: Data, region: Region, size: int) -> Region:
        """An opened region's contents are now ``size`` bytes: fix up whatever depends on that.

        ``region`` is the region as it was, at its current position, so its
        ``data_size`` is the old size; its contents already have the new one.
        Edits ``data`` as needed (headers, padding) and returns the region as it
        is now. Raises ResizeError if it can't, which rolls the edit back.
        """
        raise ResizeError(f"{cls.name} can't resize its contents")

    @classmethod
    def encode(cls, data: Data, region: Region, contents: Data) -> bytes:
        """The bytes to write over ``region``'s contents in ``data`` for edited derived ``contents``.

        Only needed by formats whose open() gives derived data, like a
        decompressed stream. ``region`` is where the contents are now.
        """
        raise NotImplementedError(f"{cls.name} can't write back edited contents")


class Binary(Format):
    """Raw bytes. Everything is binary."""

    name = "binary"

    @classmethod
    def sniff(cls, data: Data) -> float:
        return 0.01


def registry() -> list[type[Format]]:
    """All known formats."""
    from .gzip import Gzip
    from .json import Json
    from .tar import Tar
    from .text import Text

    return [Gzip, Tar, Json, Text, Binary]


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
