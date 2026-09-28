"""Text editing: lines of UTF-8, wrapped to fit, and the columns that show them.

A text level's data is always UTF-8 (formats decode other encodings into
it), and its rows are lines of text, wrapped to the text column's width.
Positions are still byte offsets, but the cursor moves by character.

Anything that isn't valid UTF-8 is shown one byte at a time, so every byte
can still be reached, and edited around, without being changed.
"""

import re
from array import array
from bisect import bisect_right
from collections.abc import Iterator
from typing import TYPE_CHECKING, List, NamedTuple, Optional

from rich.cells import cell_len, get_character_cell_size
from rich.segment import Segment
from rich.style import Style

from .columns import Column
from .highlight import combine
from .rows import Rows

if TYPE_CHECKING:
    from ..data import Change, Data
    from ..node import Node
    from .view import HexView

TAB = 4
# rows per block of the layout, and bytes to read at a time when laying out
BLOCK = 1024
CHUNK = 1024 * 1024

# a line break, a UTF-8 character (surrogates too, which stand for bytes that
# weren't valid in the text's own encoding), or any other single byte
TOKEN = re.compile(
    rb"\r\n|[\x00-\x7f]|[\xc2-\xdf][\x80-\xbf]|\xe0[\xa0-\xbf][\x80-\xbf]|[\xe1-\xef][\x80-\xbf]{2}"
    rb"|\xf0[\x90-\xbf][\x80-\xbf]{2}|[\xf1-\xf3][\x80-\xbf]{3}|\xf4[\x80-\x8f][\x80-\xbf]{2}|[\x80-\xff]",
    re.S,
)

# printable ASCII, where every character is a byte and a cell
PLAIN = re.compile(r"[ -~]+")
OTHER = re.compile(r"[^ -~]+")

BAD_STYLE = Style(color="bright_red", dim=True)
CONTROL_STYLE = Style(color="bright_black")


class Token(NamedTuple):
    """One character (or stray byte) of text, as it's drawn."""

    start: int
    stop: int
    text: str
    width: int
    style: Optional[Style] = None

    @property
    def newline(self) -> bool:
        return self.text == "\n"


def tokens(data: bytes, x: int = 0, start: int = 0, stop: Optional[int] = None) -> Iterator[Token]:
    """The characters in ``data[start:stop]``, with where they are and how wide they draw from ``x``."""
    for match in TOKEN.finditer(data, start, len(data) if stop is None else stop):
        raw = match.group()
        lo, hi = match.span()
        if raw in (b"\n", b"\r\n"):
            yield Token(lo, hi, "\n", 0)
            continue
        if raw == b"\t":
            width = TAB - x % TAB
            yield Token(lo, hi, " " * width, width)
            x += width
            continue
        if len(raw) == 1 and raw[0] >= 0x80:
            yield Token(lo, hi, "·", 1, BAD_STYLE)
            x += 1
            continue
        char = raw.decode("utf-8", "surrogatepass")
        if 0xD800 <= ord(char) <= 0xDFFF:
            yield Token(lo, hi, "�", 1, BAD_STYLE)
            x += 1
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            yield Token(lo, hi, "·", 1, CONTROL_STYLE)
            x += 1
        else:
            width = cell_len(char)
            yield Token(lo, hi, char, width)
            x += width


class _Block:
    """A run of rows: where the first starts and its line number, and the rest relative to those.

    Keeping them relative means an edit only has to move the blocks after it,
    not every row.
    """

    __slots__ = ("start", "line", "starts", "lines")

    def __init__(self, rows: list[tuple[int, int]]):
        self.start, self.line = rows[0]
        self.starts = array("q", [start - self.start for start, _ in rows])
        self.lines = array("q", [line - self.line for _, line in rows])

    def __len__(self) -> int:
        return len(self.starts)

    def rows(self) -> list[tuple[int, int]]:
        return [(self.start + start, self.line + line) for start, line in zip(self.starts, self.lines)]


def _blocks(rows: list[tuple[int, int]]) -> list[_Block]:
    return [_Block(rows[i : i + BLOCK]) for i in range(0, len(rows), BLOCK)]


def _char_bytes(char: str) -> int:
    code = ord(char)
    return 1 if code < 0x80 else 2 if code < 0x800 else 3 if code < 0x10000 else 4


class TextRows(Rows):
    """Lines of text in ``data``, wrapped to ``width`` cells.

    Laid out once, then kept up to date as the data changes: an edit only lays
    out the lines it touched again, and moves the rows after them.
    """

    def __init__(self, data: "Data", width: int, limit: int):
        self.data = data
        self.width = max(1, width)
        self.limit = limit
        self._size = data.size
        self._blocks = _blocks(list(self._layout(0, 0)))
        self._index()

    def _index(self) -> None:
        # the row number each block starts at
        self._firsts = []
        count = 0
        for block in self._blocks:
            self._firsts.append(count)
            count += len(block)
        self._count = count

    # laying out

    def _lines(self, lo: int) -> Iterator[tuple[int, bytes]]:
        """The lines from ``lo`` (the start of a row) on, with their line breaks, and where each starts.

        Reads as it goes, so stopping early doesn't read the rest.
        """
        pending = b""
        pending_start = pos = lo
        while chunk := self.data.read(pos, CHUNK):
            pos += len(chunk)
            buffer = pending + chunk
            start = 0
            while (newline := buffer.find(b"\n", start)) >= 0:
                yield pending_start + start, buffer[start : newline + 1]
                start = newline + 1
            pending = buffer[start:]
            pending_start += start
        # the text after the last line break is a line too, even if it's empty
        yield pending_start, pending

    def _layout(self, lo: int, line: int) -> Iterator[tuple[int, int]]:
        """(row start, line number) for each row from ``lo`` on, numbering lines from ``line``."""
        width = self.width
        for start, text in self._lines(lo):
            if len(text) <= width and text.isascii() and b"\t" not in text:
                # the common case: fits, and one byte is one cell
                yield start, line
            else:
                for row in self._wrap(start, text):
                    yield row, line
            line += 1

    def _wrap(self, start: int, text: bytes) -> Iterator[int]:
        """Where the rows of the line ``text`` at ``start`` start."""
        body = text[:-2] if text.endswith(b"\r\n") else text[:-1] if text.endswith(b"\n") else text
        try:
            chars = body.decode("utf-8")
        except UnicodeDecodeError:
            yield from self._wrap_tokens(start, body)
            return
        yield start
        x = 0
        offset = start
        width = self.width
        index = 0
        while index < len(chars):
            run = PLAIN.match(chars, index)
            if run:
                # printable ASCII: a byte and a cell each, so the rows in it can be worked out
                length = run.end() - index
                # (a wide character can leave a narrow row already past the width)
                first = max(0, width - x) if x else width
                yield from range(offset + first, offset + length, width)
                x = x + length if length < first else (length - first) % width or width
                offset += length
                index += length
                continue
            # everything else, a character at a time, up to the next printable ASCII
            other = OTHER.match(chars, index)
            index = other.end()
            for char in other.group():
                if char == "\t":
                    cells = TAB - x % TAB
                elif char < " " or char == "\x7f":
                    cells = 1
                else:
                    cells = get_character_cell_size(char)
                if x and x + cells > width:
                    yield offset
                    x = 0
                    if char == "\t":
                        cells = TAB
                x += cells
                offset += _char_bytes(char)

    def _wrap_tokens(self, start: int, body: bytes) -> Iterator[int]:
        """_wrap for lines that aren't all valid UTF-8, a character (or stray byte) at a time."""
        yield start
        x = 0
        for token in tokens(body):
            # tabs reach the next stop across the row, not the line
            tab = body[token.start] == 0x09
            cells = TAB - x % TAB if tab else token.width
            if x and x + cells > self.width:
                yield start + token.start
                x = 0
                cells = TAB if tab else cells
            x += cells

    def update(self, change: "Change") -> None:
        """The data changed: lay out the rows it touched again.

        Wrapping is greedy, so where a row is laid out depends only on where it
        starts and what comes after. Laying out again from before the change,
        as soon as a row starts after the change at a place a row started
        before it, the rest are as they were, only moved.
        """
        if self._size + change.delta != self.data.size:
            # lost track somehow: start again
            self.__init__(self.data, self.width, self.limit)
            return
        # from a row before the change, in case the edit lets it fit more: rows
        # before that end where they did, as the characters they end at haven't
        # changed (unless the edit cut one of them in half)
        first = self.row_of(change.offset)
        while first and self.line_of_row(first - 1) == self.line_of_row(first):
            first -= 1
            if self.start(first) + 4 <= change.offset:
                break
        changed_end = change.offset + change.inserted
        rows = []
        end, lines = self._count, 0
        for start, line in self._layout(self.start(first), self.line_of_row(first)):
            if start >= changed_end:
                old = self.row_of(start - change.delta)
                if self.start(old) == start - change.delta and old >= first:
                    end, lines = old, line - self.line_of_row(old)
                    break
            rows.append((start, line))
        self._size = self.data.size
        self._splice(first, end, rows, change.delta, lines)

    def _splice(self, first: int, end: int, rows: list[tuple[int, int]], moved: int, lines: int) -> None:
        """Replace rows ``[first, end)`` with ``rows``, moving the rows after by ``moved`` bytes and ``lines``."""
        b_first = bisect_right(self._firsts, first) - 1
        b_last = bisect_right(self._firsts, max(first, end - 1)) - 1
        old = [row for block in self._blocks[b_first : b_last + 1] for row in block.rows()]
        offset = self._firsts[b_first]
        after = [(start + moved, line + lines) for start, line in old[end - offset :]]
        replacement = _blocks(old[: first - offset] + rows + after)
        for block in self._blocks[b_last + 1 :]:
            block.start += moved
            block.line += lines
        self._blocks[b_first : b_last + 1] = replacement
        self._index()

    # finding rows

    def _locate(self, row: int) -> tuple[_Block, int]:
        index = bisect_right(self._firsts, row) - 1
        return self._blocks[index], row - self._firsts[index]

    @property
    def count(self) -> int:
        return self._count

    @property
    def starts(self) -> list[int]:
        """Every row's start (slow: for tests)."""
        return [start for block in self._blocks for start, _ in block.rows()]

    @property
    def lines(self) -> list[int]:
        """Every row's line number (slow: for tests)."""
        return [line for block in self._blocks for _, line in block.rows()]

    @property
    def line_count(self) -> int:
        return self.line_of_row(self._count - 1) + 1

    @property
    def newline(self) -> bytes:
        """The line break this text uses."""
        head = self.data.read(0, CHUNK)
        first = head.find(b"\n")
        return b"\r\n" if first > 0 and head[first - 1] == 0x0D else b"\n"

    def start(self, row: int) -> int:
        block, index = self._locate(row)
        return block.start + block.starts[index]

    def stop(self, row: int) -> int:
        return self.start(row + 1) if row + 1 < self._count else self._size

    def line_of_row(self, row: int) -> int:
        block, index = self._locate(row)
        return block.line + block.lines[index]

    def row_of(self, position: int) -> int:
        index = max(0, bisect_right(self._blocks, position, key=lambda block: block.start) - 1)
        block = self._blocks[index]
        return self._firsts[index] + max(0, bisect_right(block.starts, position - block.start) - 1)

    def line_of(self, position: int) -> int:
        return self.line_of_row(self.row_of(position))

    def row_tokens(self, row: int) -> list[Token]:
        start = self.start(row)
        raw = self.data.read(start, self.stop(row) - start)
        return [token._replace(start=start + token.start, stop=start + token.stop) for token in tokens(raw)]

    def x_of(self, position: int) -> int:
        return sum(token.width for token in self.row_tokens(self.row_of(position)) if token.start < position)

    def position_at(self, row: int, x: int) -> int:
        across = 0
        for token in self.row_tokens(row):
            if token.newline or across + token.width > x:
                return token.start
            across += token.width
        return self.end_of(row)

    def end_of(self, row: int) -> int:
        row_tokens = self.row_tokens(row)
        if row + 1 == self._count:
            return min(self.stop(row), self.limit)
        if row_tokens and row_tokens[-1].newline:
            return row_tokens[-1].start
        # a wrapped row: its end is the start of the next one, so stop on its last character
        return row_tokens[-1].start if row_tokens else self.start(row)

    def step(self, position: int, delta: int) -> int:
        if delta > 0:
            match = TOKEN.match(self.data.read(position, 4))
            return position + (match.end() if match else 1)
        if position <= 0:
            return 0
        previous = self.start(self.row_of(position - 1))
        for token in self.row_tokens(self.row_of(position - 1)):
            if token.start < position:
                previous = token.start
        return previous


class TextEditColumn(Column):
    """Text, wrapped to fit, that you can type into.

    It's as wide as the view has room for, within limits. When its level has
    the cursor, its rows are lines of text; otherwise it shows whatever
    characters are in each row of the level that has it.
    """

    focusable = True
    byte_cursor = True
    prefers_insert = True
    flexible = True
    title = "text"
    min_width = 40
    max_width = 120

    def __init__(self, node: "Node"):
        self.node = node
        self.wrap = 80
        self._rows: Optional[TextRows] = None

    def width(self, bytes_per_line: int, size: int) -> int:
        # and one to type at the end of a full row
        return self.wrap + 1

    def fit(self, width: int) -> None:
        """Use ``width`` cells, as far as the limits allow."""
        self.wrap = max(self.min_width, min(self.max_width, width - 1))

    def rows(self, bytes_per_line: int, limit: int) -> TextRows:
        data = self.node.data
        if self._rows is None or self._rows.width != self.wrap:
            if self._rows is not None:
                data.unsubscribe(self._rows.update)
            self._rows = TextRows(data, self.wrap, limit)
            data.subscribe(self._rows.update)
        self._rows.limit = limit
        return self._rows

    def render(self, offset, data, styles, bytes_per_line, size, cursor, first=0) -> List[Segment]:
        segments = []
        x = 0
        drawn_cursor = False
        for token in tokens(data, 0, first):
            if token.newline:
                text, width = " ", 1
            else:
                text, width = token.text, token.width
            if x + width > self.wrap + 1:
                break
            style = styles[token.start] if token.start < len(styles) else None
            if token.style:
                style = combine(style, token.style)
            if cursor is not None and token.start <= cursor.index < token.stop:
                style = combine(style, cursor.style)
                drawn_cursor = True
            elif token.newline:
                # only there to show the cursor, or a selection
                text = " " if style else ""
                width = len(text)
            segments.append(Segment(text, style))
            x += width
        if cursor is not None and not drawn_cursor and cursor.index == len(data) and x <= self.wrap:
            segments.append(Segment(" ", cursor.style))
            x += 1
        segments.append(Segment(" " * max(0, self.wrap + 1 - x)))
        return segments

    def click(self, line: int, stop: int, x: int, bytes_per_line: int, size: int) -> Optional[int]:
        data = self.node.data.read(line, stop - line)
        across = 0
        for token in tokens(data):
            if token.newline or across + token.width > x:
                return line + token.start
            across += token.width
        return line + len(data)

    def on_key(self, view: "HexView", key: str, char: Optional[str]) -> bool:
        if key == "enter":
            view.type_text(self._newline(view))
            return True
        if key == "ctrl+e":
            view.choose_encoding()
            return True
        if char and len(char) == 1 and (char.isprintable() or char == "\t") and key != "tab":
            view.type_text(char)
            return True
        return False

    def _newline(self, view: "HexView") -> str:
        rows = view.cursor.layout
        return rows.newline.decode() if isinstance(rows, TextRows) else "\n"

    def describe(self, offset: int) -> Optional[str]:
        rows = self._rows
        encoding = getattr(self.node.fmt, "encoding", None)
        where = f"line {rows.line_of(offset) + 1}" if rows else None
        return " │ ".join(part for part in (where, encoding and f"{encoding} (^e)") if part)


class LineColumn(Column):
    """Line numbers, on the row each line starts on."""

    title = "line"
    style = Style(color="bright_black")

    def __init__(self, text: TextEditColumn):
        self.text = text

    def width(self, bytes_per_line: int, size: int) -> int:
        rows = self.text._rows
        return max(4, len(str(rows.line_count)) if rows else 4) + 1

    def render(self, offset, data, styles, bytes_per_line, size, cursor, first=0) -> List[Segment]:
        width = self.width(bytes_per_line, size)
        rows = self.text._rows
        if rows is None or offset < 0 or offset > size:
            return [Segment(" " * width)]
        row = rows.row_of(offset)
        line = rows.line_of_row(row)
        if rows.start(row) != offset or (row and rows.line_of_row(row - 1) == line):
            return [Segment(" " * width)]
        return [Segment(f"{line + 1:>{width - 1}} ", self.style)]
