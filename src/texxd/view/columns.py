"""Columns of the hex view.

Columns aren't widgets: the hex view draws every line itself, and asks each
column to render its part of the line and to map clicks back to bytes.
"""

from dataclasses import dataclass
from typing import List, Optional

from rich.segment import Segment
from rich.style import Style

from ..formats import Entry, Format
from .highlight import Styles, combine


@dataclass
class CursorCell:
    """Where the cursor is on the line being rendered, and how to draw it."""

    index: int
    style: Style
    nibble: int = 0


class Column:
    """Base column."""

    focusable = False
    title = ""

    def width(self, bytes_per_line: int, size: int) -> int:
        """Width in cells."""
        raise NotImplementedError()

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


class TextColumn(Column):
    """Bytes as printable ASCII, with a dot for anything else."""

    focusable = True
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


def human_size(size: int) -> str:
    """Format a byte count compactly."""
    value = float(size)
    for unit in ("", "K", "M", "G"):
        if value < 1024:
            return f"{size}" if not unit else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}T"


class EntriesColumn(Column):
    """A format's structure, drawn beside the bytes it describes.

    Each entry's name sits on the row where it starts, with a bracket running
    down to the row where it ends. It shows the same rows as the hex column,
    so it scrolls with it.
    """

    focusable = True
    name_width = 30
    size_width = 6

    def __init__(self, node, fmt: type[Format]):
        self.node = node
        self.fmt = fmt
        self.title = fmt.name
        # set by the view before rendering
        self.selected: Optional[Entry] = None
        self.selected_style: Optional[Style] = None

    def entries(self) -> list[Entry]:
        return self.node.entries(self.fmt)

    def width(self, bytes_per_line: int, size: int) -> int:
        longest = max((len(e.name) for e in self.entries()), default=0)
        return 2 + max(10, min(self.name_width, longest)) + 1 + self.size_width

    def entry_for_line(self, offset: int, bytes_per_line: int) -> Optional[Entry]:
        """The entry that starts on this line, or else the one the line is inside."""
        entry = self.node.entry_before(offset + bytes_per_line, self.fmt)
        if entry and entry.stop > offset:
            return entry
        return None

    def render(self, offset, data, styles, bytes_per_line, size, cursor, first=0) -> List[Segment]:
        width = self.width(bytes_per_line, size)
        entry = self.entry_for_line(offset, bytes_per_line)
        if entry is None:
            return [Segment(" " * width)]
        end = offset + bytes_per_line
        style = self.selected_style if self.selected and self.selected.start == entry.start else None
        if entry.start >= offset:
            marker = "─" if entry.stop <= end else "┬"
            name_width = width - 3 - self.size_width
            name = entry.name if len(entry.name) <= name_width else entry.name[: name_width - 1] + "…"
            size_text = human_size(entry.data_size) if entry.kind == "file" else entry.kind
            text = f"{marker} {name:<{name_width}} {size_text[: self.size_width]:>{self.size_width}}"
            return [Segment(marker, style), Segment(text[1:], combine(style, Style(bold=True)) if style else None)]
        marker = "└" if entry.stop <= end else "│"
        return [Segment(marker, style), Segment(" " * (width - 1))]

    def hit(self, x: int, bytes_per_line: int) -> int:
        return 0
