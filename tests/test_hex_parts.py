"""Tests for the hex view's cursor, columns and highlighters."""

from conftest import make_tar
from rich.style import Style

from texxd.data import Buffer, BytesSource
from texxd.formats.tar import Tar
from texxd.node import Node
from texxd.view.columns import AddressColumn, CursorCell, EntriesColumn, HexColumn, TextColumn
from texxd.view.cursor import Cursor
from texxd.view.highlight import DataHighlighter, EditHighlighter, Highlights

CURSOR = Style(bgcolor="white")


def cursor(limit=255, bytes_per_line=16):
    c = Cursor(bytes_per_line)
    c.limit = limit
    return c


def text(segments):
    return "".join(s.text for s in segments)


def test_cursor_clamps():
    c = cursor(limit=20)
    assert c.set_position(100)
    assert c.position == 20
    assert c.set_position(-5)
    assert c.position == 0
    assert not c.set_position(0)


def test_cursor_movement_resets_nibble():
    c = cursor()
    c.nibble = 1
    c.move(1)
    assert c.nibble == 0


def test_cursor_lines():
    c = cursor(limit=40)
    c.set_position(5)
    c.move_lines(1)
    assert c.position == 21
    c.move_lines(1)
    assert c.position == 37
    c.move_lines(1)  # already on the last line
    assert c.position == 37
    c.set_position(12)
    c.move_lines(2)  # line 2 is too short to reach column 12
    assert c.position == 40
    c.move_lines(-10)
    assert c.position == 8


def test_cursor_line_and_file_ends():
    c = cursor(limit=40)
    c.set_position(20)
    c.line_start()
    assert c.position == 16
    c.line_end()
    assert c.position == 31
    c.file_end()
    assert c.position == 40
    c.line_end()  # past the limit on a short line
    assert c.position == 40
    c.file_start()
    assert c.position == 0


def test_address_column():
    col = AddressColumn()
    assert text(col.render(0x20, b"", [], 16, 0x100, None)) == "0020:"
    assert text(col.render(0x20, b"", [], 16, 0x12345, None)) == "00020:"
    assert col.width(16, 0x12345) == 6


def test_hex_column_layout():
    col = HexColumn()
    data = bytes(range(16))
    out = text(col.render(0, data, [None] * 16, 16, 16, None))
    assert out == "00 01 02 03 04 05 06 07  08 09 0a 0b 0c 0d 0e 0f"
    assert len(out) == col.width(16, 16)
    short = text(col.render(0, b"\xff", [None], 16, 1, None))
    assert short.startswith("ff ") and len(short) == col.width(16, 1)
    assert text(col.render(0, bytes(8), [None] * 8, 8, 8, None)) == "00 00 00 00 00 00 00 00"


def test_hex_column_hits():
    col = HexColumn()
    assert col.hit(0, 16) == 0
    assert col.hit(1, 16) == 0
    assert col.hit(3, 16) == 1
    assert col.hit(23, 16) == 7
    assert col.hit(24, 16) == 8  # the double gap
    assert col.hit(25, 16) == 8
    assert col.hit(200, 16) == 15


def test_hex_column_cursor_and_nibble():
    col = HexColumn()
    segments = col.render(0, b"\xab\xcd", [None, None], 4, 2, CursorCell(1, CURSOR, nibble=1))
    styled = [(s.text, s.style) for s in segments if s.style]
    assert styled == [("d", CURSOR)]
    # the append slot past the end is drawn as a blank cursor cell
    segments = col.render(0, b"\xab", [None], 4, 1, CursorCell(1, CURSOR))
    assert [(s.text, s.style) for s in segments if s.style] == [("  ", CURSOR)]


def test_text_column_shows_only_printable():
    col = TextColumn()
    out = text(col.render(0, b"A\x7f\x00~ \x80", [None] * 6, 8, 6, None))
    assert out == "A..~ .  "


def test_cursor_wins_over_highlights():
    buf = Buffer(BytesSource(b"\x00\x01"))
    buf.write(0, b"\x00")
    highlights = Highlights()
    highlights["data"] = DataHighlighter()
    highlights["edits"] = EditHighlighter(buf)
    styles = [None, None]
    highlights.highlight(buf.read(0, 2), 0, styles)
    assert styles[0].color.name == "bright_red"
    cursor_style = Style(bgcolor="bright_white", color="black")
    segments = HexColumn().render(0, b"\x00\x01", styles, 2, 2, CursorCell(0, cursor_style))
    first = segments[0].style
    assert first.color.name == "black" and first.bgcolor.name == "bright_white"


def test_entries_column_brackets():
    node = Node("t.tar", Buffer(BytesSource(make_tar({"dir": None, "a.txt": b"hello"}))))
    col = EntriesColumn(node, Tar)
    width = col.width(16, node.data.size)

    def row(offset):
        out = text(col.render(offset, b"", [], 16, node.data.size, None))
        assert len(out) == width
        return out.rstrip()

    assert row(0).startswith("┬ dir") and row(0).endswith("dir")
    assert row(16) == "│"
    assert row(496) == "└"
    assert row(512).startswith("┬ a.txt") and row(512).endswith(" 5")
    assert row(1520) == "└"
    assert row(1536) == ""  # end of archive padding
    assert col.entry_for_line(520, 16).name == "a.txt"
