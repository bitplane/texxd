"""Columns of the hex view.

Columns aren't widgets: the hex view draws every line itself, and asks each
column to render its part of the line. Columns also handle their own keys and
clicks, and say what they highlight and what can be opened from them, so the
view doesn't need to know what kinds of column there are.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from rich.segment import Segment
from rich.style import Style

from ..formats import Format, Region
from .highlight import Styles, combine

if TYPE_CHECKING:
    from .rows import Rows
    from .view import HexView

HEX_DIGITS = "0123456789abcdef"

# (start, stop, style) ranges to highlight, in the level's offsets
Ranges = list[tuple[int, int, Style]]


@dataclass
class CursorCell:
    """Where the cursor is on the line being rendered, and how to draw it."""

    index: int
    style: Style
    nibble: int = 0


class Column:
    """Base column."""

    # the cursor can be put in it
    focusable = False
    # it shows bytes, and draws the cursor on the byte it's on
    byte_cursor = False
    # typing should insert rather than overwrite, where the data can grow
    prefers_insert = False
    # it can be as wide as there's room for (but no narrower than min_width): see fit()
    flexible = False
    min_width = 0
    title = ""

    def width(self, bytes_per_line: int, size: int) -> int:
        """Width in cells."""
        raise NotImplementedError()

    def fit(self, width: int) -> None:
        """For flexible columns: there are ``width`` cells to fill."""

    def rows(self, bytes_per_line: int, limit: int) -> Optional["Rows"]:
        """How to split the level into rows while it has the cursor, if not in rows of bytes."""
        return None

    def render(
        self,
        offset: int,
        data: bytes,
        styles: Styles,
        bytes_per_line: int,
        size: int,
        cursor: Optional[CursorCell],
        first: int = 0,
    ) -> List[Segment]:
        """Render the column's part of the line for ``data`` at ``offset``.

        Bytes before index ``first`` aren't part of this column's data (the line
        starts before it does), so they're drawn blank.
        """
        raise NotImplementedError()

    def hit(self, x: int, bytes_per_line: int) -> int:
        """The byte index within the line for a click at ``x`` cells into the column."""
        return 0

    def click(self, line: int, stop: int, x: int, bytes_per_line: int, size: int) -> Optional[int]:
        """Where a click ``x`` cells in, on the row of bytes from ``line`` to ``stop``, puts the cursor.

        Returns an offset in the level, or None to ignore the click.
        """
        return line + self.hit(x, bytes_per_line)

    def on_key(self, view: "HexView", key: str, char: Optional[str]) -> bool:
        """Handle a key while the cursor is in this column. Returns True if it did."""
        return False

    def sync(self, cursor: Optional[int], opened: Optional[Region], focused: bool) -> Ranges:
        """Update for a new cursor position, before rendering.

        ``cursor`` is the cursor's offset if it's in this column, else None.
        ``opened`` is the region this column opened as the next level, if it did.
        Returns ranges to highlight while the cursor is here.
        """
        return []

    def region_at(self, offset: int) -> Optional[tuple[type[Format], Region]]:
        """The region at ``offset`` this column can open, and its format."""
        return None

    def describe(self, offset: int) -> Optional[str]:
        """Something to say about ``offset`` in the status bar."""
        return None


class AddressColumn(Column):
    """The offset of the start of each line."""

    title = "addr"

    @staticmethod
    def digits(size: int) -> int:
        return max(4, len(f"{size:x}"))

    def width(self, bytes_per_line: int, size: int) -> int:
        return self.digits(size) + 1

    def render(self, offset, data, styles, bytes_per_line, size, cursor, first=0) -> List[Segment]:
        return [Segment(f"{max(0, offset):0{self.digits(size)}x}:", Style(color="bright_black"))]


class HexColumn(Column):
    """Bytes as pairs of hex digits, with an extra gap every 8 bytes."""

    focusable = True
    byte_cursor = True
    title = "hex"

    @staticmethod
    def cell_x(i: int, bytes_per_line: int) -> int:
        """Where byte ``i`` starts."""
        return i * 3 + (i // 8 if bytes_per_line > 8 else 0)

    def width(self, bytes_per_line: int, size: int) -> int:
        return self.cell_x(bytes_per_line - 1, bytes_per_line) + 2

    def render(self, offset, data, styles, bytes_per_line, size, cursor, first=0) -> List[Segment]:
        segments = []
        for i in range(bytes_per_line):
            if i:
                gap = " " if bytes_per_line <= 8 or i % 8 else "  "
                segments.append(Segment(gap))
            present = first <= i < len(data)
            style = styles[i] if present else None
            text = f"{data[i]:02x}" if present else "  "
            if cursor is None or cursor.index != i:
                segments.append(Segment(text, style))
            elif cursor.nibble and present:
                segments.append(Segment(text[0], style))
                segments.append(Segment(text[1], combine(style, cursor.style)))
            else:
                segments.append(Segment(text, combine(style, cursor.style)))
        return segments

    def hit(self, x: int, bytes_per_line: int) -> int:
        for i in range(bytes_per_line):
            if x < self.cell_x(i, bytes_per_line) + 2:
                return i
            if i + 1 < bytes_per_line and x < self.cell_x(i + 1, bytes_per_line):
                # in the gap: pick whichever byte is nearer
                return i if x - self.cell_x(i, bytes_per_line) < 3 else i + 1
        return bytes_per_line - 1

    def on_key(self, view: "HexView", key: str, char: Optional[str]) -> bool:
        if char and len(char) == 1 and char.lower() in HEX_DIGITS:
            view.type_nibble(HEX_DIGITS.index(char.lower()))
            return True
        return False


class TextColumn(Column):
    """Bytes as printable ASCII, with a dot for anything else."""

    focusable = True
    byte_cursor = True
    title = "ascii"

    def width(self, bytes_per_line: int, size: int) -> int:
        return bytes_per_line

    def render(self, offset, data, styles, bytes_per_line, size, cursor, first=0) -> List[Segment]:
        segments = []
        for i in range(bytes_per_line):
            if first <= i < len(data):
                byte = data[i]
                text = chr(byte) if 0x20 <= byte <= 0x7E else "."
                style = styles[i]
            else:
                text, style = " ", None
            if cursor is not None and cursor.index == i:
                style = combine(style, cursor.style)
            segments.append(Segment(text, style))
        return segments

    def hit(self, x: int, bytes_per_line: int) -> int:
        return max(0, min(x, bytes_per_line - 1))

    def on_key(self, view: "HexView", key: str, char: Optional[str]) -> bool:
        if char and len(char) == 1 and 0x20 <= ord(char) <= 0x7E:
            view.type_byte(ord(char))
            return True
        return False
