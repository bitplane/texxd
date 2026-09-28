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
from .highlight import ACTIVE_STYLE, INACTIVE_STYLE, Styles, combine

if TYPE_CHECKING:
    from ..node import Node
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

    def click(self, line: int, x: int, bytes_per_line: int, size: int) -> Optional[int]:
        """Where a click ``x`` cells in, on the line starting at offset ``line``, puts the cursor.

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


def human_size(size: int) -> str:
    """Format a byte count compactly."""
    value = float(size)
    for unit in ("", "K", "M", "G"):
        if value < 1024:
            return f"{size}" if not unit else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}T"


class StructureColumn(Column):
    """A format's structure, drawn beside the bytes it describes.

    Each region's name sits on the row where it starts, with a bracket running
    down to the row where it ends. It shows the same rows as the hex column,
    so it scrolls with it. With the cursor in it, the region under the cursor
    is selected and its bytes are tinted: header and contents separately.
    """

    focusable = True
    name_width = 30
    size_width = 6
    header_style = Style(bgcolor="orange4")
    contents_style = Style(bgcolor="deep_sky_blue4")

    def __init__(self, node: "Node", fmt: type[Format]):
        self.node = node
        self.fmt = fmt
        self.title = fmt.name
        self.selected: Optional[Region] = None
        self.selected_style: Optional[Style] = None

    def regions(self) -> list[Region]:
        return self.node.regions(self.fmt)

    def width(self, bytes_per_line: int, size: int) -> int:
        longest = max((len(r.name) for r in self.regions()), default=0)
        return 2 + max(10, min(self.name_width, longest)) + 1 + self.size_width

    def region_for_line(self, offset: int, bytes_per_line: int) -> Optional[Region]:
        """The region that starts on this line, or else the one the line is inside."""
        region = self.node.region_before(offset + bytes_per_line, self.fmt)
        if region and region.stop > offset:
            return region
        return None

    def render(self, offset, data, styles, bytes_per_line, size, cursor, first=0) -> List[Segment]:
        width = self.width(bytes_per_line, size)
        region = self.region_for_line(offset, bytes_per_line)
        if region is None:
            return [Segment(" " * width)]
        end = offset + bytes_per_line
        style = self.selected_style if self.selected and self.selected.start == region.start else None
        if region.start >= offset:
            marker = "─" if region.stop <= end else "┬"
            name_width = width - 3 - self.size_width
            name = region.name if len(region.name) <= name_width else region.name[: name_width - 1] + "…"
            size_text = human_size(region.data_size) if region.kind == "file" else region.kind
            text = f"{marker} {name:<{name_width}} {size_text[: self.size_width]:>{self.size_width}}"
            return [Segment(marker, style), Segment(text[1:], combine(style, Style(bold=True)) if style else None)]
        marker = "└" if region.stop <= end else "│"
        return [Segment(marker, style), Segment(" " * (width - 1))]

    def click(self, line: int, x: int, bytes_per_line: int, size: int) -> Optional[int]:
        # stay on the clicked line so the view doesn't jump, but inside the region
        region = self.region_for_line(line, bytes_per_line)
        if region is None:
            return None
        return min(max(line, region.start), region.stop - 1)

    def on_key(self, view: "HexView", key: str, char: Optional[str]) -> bool:
        if key not in ("up", "down"):
            return False
        target = self._step(view.cursor.position, -1 if key == "up" else 1)
        if target is not None:
            view.go_to(target)
        return True

    def _step(self, position: int, direction: int) -> Optional[int]:
        """The start of the previous or next region."""
        regions = self.regions()
        if direction > 0:
            return next((r.start for r in regions if r.start > position), None)
        current = self.node.region_at(position, self.fmt)
        if current and current.start < position:
            return current.start
        return next((r.start for r in reversed(regions) if r.start < position), None)

    def sync(self, cursor: Optional[int], opened: Optional[Region], focused: bool) -> Ranges:
        if cursor is None:
            self.selected = opened
            self.selected_style = INACTIVE_STYLE
            return []
        self.selected = region = self.node.region_at(cursor, self.fmt)
        self.selected_style = ACTIVE_STYLE if focused else INACTIVE_STYLE
        if region is None:
            return []
        return [
            (region.start, region.data_start, self.header_style),
            (region.data_start, region.data_stop, self.contents_style),
        ]

    def region_at(self, offset: int) -> Optional[tuple[type[Format], Region]]:
        region = self.node.region_at(offset, self.fmt)
        return (self.fmt, region) if region else None

    def describe(self, offset: int) -> Optional[str]:
        region = self.node.region_at(offset, self.fmt)
        if region is None:
            return None
        return f"{region.name}: {region.data_size} bytes at 0x{region.data_start:x}"
