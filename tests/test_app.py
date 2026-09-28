"""End-to-end tests driving the app with Textual's pilot."""

import tarfile

import pytest
from conftest import Reversed

from texxd.app import TexxdApp
from texxd.dialogs import ConfirmModal
from texxd.formats import Binary
from texxd.view import HexView
from texxd.view.columns import StructureColumn

pytestmark = pytest.mark.asyncio

SIZE = (130, 30)
# x positions in a 16 byte wide level: address 0-4, hex 6-53, ascii 55-70, structure from 72
TAR_X = 75


def hex_view(app) -> HexView:
    view = app.focused
    assert isinstance(view, HexView)
    return view


@pytest.fixture
def small(tmp_path):
    path = tmp_path / "small.bin"
    path.write_bytes(b"Hello\x7f\x00\x01 world, texxd!")
    return path


async def test_opens_hex_view(small):
    app = TexxdApp(small)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        assert view.cursor.bytes_per_line == 16
        assert view.render_line(0).text.startswith(" small.bin ")
        line = view.render_line(1).text
        assert line.startswith("0000: 48 65 6c 6c 6f 7f 00 01  20 77")
        assert "Hello... world," in line


async def test_click_past_end_clamps(small):
    app = TexxdApp(small)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        # below the last line, in the hex column
        await pilot.click(HexView, offset=(40, 10))
        assert view.cursor.position == view.data.size
        # the ascii column of the short last line
        await pilot.click(HexView, offset=(65, 2))
        assert view.active == (0, 2)
        assert view.cursor.position == view.data.size


async def test_nibbles_and_movement(small):
    app = TexxdApp(small)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await pilot.press("a")
        assert view.data.read(0, 1) == b"\xa8"  # high nibble typed, low one kept
        assert view.cursor.nibble == 1
        await pilot.press("right")
        assert view.cursor.nibble == 0
        await pilot.press("1", "2")
        assert view.data.read(0, 2) == b"\xa8\x12"
        assert view.cursor.position == 2


async def test_fast_keys_stay_in_order(small):
    app = TexxdApp(small)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await pilot.press("down", "insert", "home", "f", "f", "tab", "Z", "Y", "ctrl+z")
        await pilot.pause()
        assert view.data.read(16, 4) == b"\xffZte"
        assert view.cursor.position == 18


async def test_delete_and_backspace_remove_bytes(small):
    app = TexxdApp(small)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        size = view.data.size
        await pilot.press("delete")
        assert view.data.read(0, 3) == b"ell"
        await pilot.press("right", "right", "backspace")
        assert view.data.read(0, 3) == b"elo"
        assert view.data.size == size - 2
        await pilot.press("ctrl+z", "ctrl+z")
        assert view.data.read(0, 5) == b"Hello"


async def test_append_at_end_and_save(small):
    app = TexxdApp(small)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+end", "4", "1", "ctrl+s")
        await pilot.pause()
        assert small.read_bytes().endswith(b"texxd!A")
        assert not app.buffer.modified


async def test_quit_asks_when_modified(small):
    app = TexxdApp(small)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("0", "0", "ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("n")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal)
        assert app.is_running
        await pilot.press("ctrl+q")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
    assert small.read_bytes().startswith(b"Hello")


async def test_goto(small):
    app = TexxdApp(small)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+g", *"0x12", "enter")
        await pilot.pause()
        assert hex_view(app).cursor.position == 0x12


async def test_read_only_file(small):
    small.chmod(0o444)
    app = TexxdApp(small)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("0", "0", "ctrl+s")
        await pilot.pause()
        assert app.buffer.modified
        assert small.read_bytes().startswith(b"Hello")


async def test_new_file(tmp_path):
    path = tmp_path / "new.bin"
    app = TexxdApp(path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("tab", *"hi", "ctrl+s")
        await pilot.pause()
    assert path.read_bytes() == b"hi"


async def test_tar_column_selects_and_highlights(nested_tar):
    app = TexxdApp(nested_tar)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        assert [type(c).__name__ for c in view.level.columns] == [
            "AddressColumn",
            "HexColumn",
            "TextColumn",
            "StructureColumn",
        ]
        # inner.tar's header is at 512, line 32: scroll so it's on the second row, then click its name
        view.scroll_to(y=31, animate=False, immediate=True)
        await pilot.pause()
        await pilot.click(HexView, offset=(TAR_X, 2))
        await pilot.pause()
        assert view.active == (0, 3)
        assert view.cursor.position == 512
        assert view.active_column.selected.name == "inner.tar"
        header, contents = view.selection.ranges
        assert header[:2] == (512, 1024)
        assert contents[:2] == (1024, 1024 + 10240)
        # the hex rows of that entry are tinted
        styles = [None] * 16
        view.highlights.highlight(view.data.read(1024, 16), 1024, styles)
        assert all(s.bgcolor == StructureColumn.contents_style.bgcolor for s in styles)
        # down steps to the next entry, and clicking the hex drops the highlight
        await pilot.press("down")
        assert view.active_column.selected.name == "README"
        await pilot.click(HexView, offset=(10, 2))
        assert view.selection.ranges == []


async def test_drill_into_nested_tar_and_edit(nested_tar):
    app = TexxdApp(nested_tar)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        # tar column, down from "dir" to inner.tar, open it
        await pilot.press("tab", "tab", "down", "enter")
        await pilot.pause()
        assert [level.node.name for level in view.levels] == ["outer.tar", "inner.tar"]
        assert view.levels[1].base == 1024
        # scrolled right so the new level is in view
        assert view.scroll_x == view.max_scroll_x > 0
        assert "│ 0000: 64 61 74 61" in view.render_line(1).text
        # rows line up: the outer level shows the same bytes at its own offsets
        view.scroll_to(x=0, animate=False, immediate=True)
        await pilot.pause()
        assert view.render_line(1).text.startswith("0400: 64 61 74 61")

        # inner.tar's first entry is data.json
        await pilot.press("tab", "tab", "enter")
        await pilot.pause()
        assert [level.node.name for level in view.levels] == ["outer.tar", "inner.tar", "data.json"]
        assert view.data.read(0, 9) == b'{"a": 1}\n'
        assert not any(isinstance(c, StructureColumn) for c in view.level.columns)

        await pilot.press("tab", "X")
        await pilot.press("delete")  # resizes it, and both tars are fixed up
        await pilot.press("insert", *"!!")
        await pilot.pause()
        assert view.data.read(0, 20) == b'X!!a": 1}\n'
        await pilot.press("ctrl+s")
        await pilot.pause()

        # close it: lands on data.json in inner.tar's structure column
        await pilot.press("escape")
        await pilot.pause()
        assert len(view.levels) == 2
        assert isinstance(view.active_column, StructureColumn)
        assert view.active_column.selected.name == "data.json"

    with tarfile.open(nested_tar) as outer:
        inner = tarfile.open(fileobj=outer.extractfile("inner.tar"))
        assert inner.extractfile("data.json").read() == b'X!!a": 1}\n'


async def test_outer_levels_stay_open_and_scroll_their_whole_range(nested_tar):
    app = TexxdApp(nested_tar)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await pilot.press("tab", "tab", "down", "enter")
        await pilot.pause()
        assert view.active == (1, 1)
        await pilot.pause()

        # shift+tab walks back into the outer level; the same bytes stay on the same rows
        await pilot.press("shift+tab", "shift+tab", "shift+tab")
        await pilot.pause()
        await pilot.pause()
        assert view.active == (0, 1)
        assert len(view.levels) == 2
        assert view.cursor.position == 1024
        view.scroll_to(x=0, animate=False, immediate=True)
        await pilot.pause()
        assert view.render_line(1).text.startswith("0400: 64 61 74 61")

        # the outer level can go anywhere in its range; the inner one is blank where it has no data
        await pilot.press("ctrl+home")
        await pilot.pause()
        row = view.render_line(1).text
        assert row.startswith("0000: 64 69 72")
        assert row.rstrip().endswith("│")
        await pilot.press("ctrl+end")
        await pilot.pause()
        assert view.cursor.position == view.data.size  # the root can be appended to

        # tabbing into the inner level from outside it lands on its nearest byte
        await pilot.press("tab", "tab", "tab")
        await pilot.pause()
        await pilot.pause()
        assert view.active == (1, 1)
        assert view.cursor.position == view.data.size  # inner levels can be appended to too


async def test_clicking_levels(nested_tar):
    app = TexxdApp(nested_tar)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await pilot.press("tab", "tab", "down", "enter")
        await pilot.pause()
        await pilot.pause()
        view.scroll_to(x=0, animate=False, immediate=True)
        await pilot.pause()
        # the outer level's structure column, on a row inside inner.tar: selects it without moving
        top = int(view.scroll_y)
        await pilot.click(HexView, offset=(TAR_X, 2))
        await pilot.pause()
        await pilot.pause()
        assert len(view.levels) == 2
        assert view.active == (0, 3)
        assert view.active_column.selected.name == "inner.tar"
        assert view.cursor.position == 1024 + 16
        assert int(view.scroll_y) == top + 1024 // 16

        # a level's name moves the cursor to that level
        view.scroll_to(x=view.max_scroll_x, animate=False, immediate=True)
        await pilot.pause()
        x = view._level_x(1, 16) - int(view.scroll_x) + 2
        await pilot.click(HexView, offset=(x, 0))
        await pilot.pause()
        await pilot.pause()
        assert view.active == (1, 1)
        assert view.cursor.position == 16

        # escape closes the rightmost level
        await pilot.press("escape")
        await pilot.pause()
        assert len(view.levels) == 1
        assert view.active_column.selected.name == "inner.tar"


async def test_levels_follow_resizes_and_close_when_cut(nested_tar):
    app = TexxdApp(nested_tar)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await pilot.press("tab", "tab", "down", "enter")
        await pilot.pause()
        assert len(view.levels) == 2
        # an edit before the open level moves it, one inside resizes it
        app.buffer.insert(0, b"\0")
        app.buffer.insert(5000, b"\0")
        await pilot.pause()
        assert len(view.levels) == 2
        assert view.levels[1].base == 1025 and view.data.size == 10241
        # one across its edge loses track of its bytes, so it closes
        app.buffer.delete(1000, 100)
        await pilot.pause()
        assert len(view.levels) == 1


async def test_derived_level_does_not_line_up(tmp_path, monkeypatch):
    monkeypatch.setattr("texxd.formats.registry", lambda: [Reversed, Binary])
    path = tmp_path / "rev.bin"
    path.write_bytes(b"REV" + bytes(range(64)))
    app = TexxdApp(path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        assert isinstance(view.level.columns[3], StructureColumn)
        await pilot.press("tab", "tab", "enter")
        await pilot.pause()
        await pilot.pause()
        assert view.active == (1, 1)
        assert view.levels[1].base is None
        assert view.data.read(0, 2) == b"\x3f\x3e"
        # the outer level is blank while the derived one drives the rows, and clicks on it do nothing
        view.scroll_to(x=0, animate=False, immediate=True)
        await pilot.pause()
        assert view.render_line(1).text.startswith(" " * 20)
        await pilot.click(HexView, offset=(10, 1))
        assert view.active == (1, 1)
        # going back out lands on the region it was opened from
        await pilot.press("escape")
        await pilot.pause()
        assert view.active == (0, 3)
        assert view.cursor.position == 0
        assert view.render_line(1).text.startswith("0000: 52 45 56")


async def test_derived_edits_undo_and_save(tmp_path, monkeypatch):
    monkeypatch.setattr("texxd.formats.registry", lambda: [Reversed, Binary])
    path = tmp_path / "rev.bin"
    path.write_bytes(b"REV" + b"olleh")
    app = TexxdApp(path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await pilot.press("tab", "tab", "enter")
        await pilot.pause()
        await pilot.press("tab", "J")
        await pilot.pause()
        assert view.data.read(0, 5) == b"Jello"
        # the edit is the document's, though the file itself hasn't changed yet
        assert app.document.modified and not app.buffer.modified
        header = "".join(segment.text for segment in view._render_header(view.cursor.bytes_per_line))
        assert "rev.bin*" in header
        # undo from the outer level goes back in to show it
        await pilot.press("shift+tab", "shift+tab", "shift+tab")
        await pilot.pause()
        assert len(view.levels) == 2 and view.active[0] == 0
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert view.active[0] == 1 and view.data.read(0, 5) == b"hello"
        await pilot.press("ctrl+y", "ctrl+s")
        await pilot.pause()
        assert path.read_bytes() == b"REV" + b"olleJ"
        assert not app.document.modified
