"""End-to-end tests driving the app with Textual's pilot."""

import tarfile

import pytest
from conftest import Reversed

from texxd.app import TexxdApp
from texxd.dialogs import ChoiceModal, ConfirmModal, PickModal
from texxd.formats import Binary
from texxd.view import HexView

pytestmark = pytest.mark.asyncio

SIZE = (130, 30)


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


def use(view, name, index=None):
    """Switch a level (the one with the cursor, by default) to the view called ``name``."""
    index = view.active[0] if index is None else index
    view.show_view(index, next(v for v in view.levels[index].views if v.name == name))


async def test_tar_opens_as_a_listing(nested_tar):
    app = TexxdApp(nested_tar)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        assert [v.name for v in view.levels[0].views] == ["tar", "hex"]
        assert [type(c).__name__ for c in view.level.columns] == ["TreeColumn"]
        # an entry a row
        assert [view.render_line(y).text.split()[0] for y in range(1, 4)] == ["dir", "inner.tar", "README"]
        assert "outer.tar · tar ▾" in view.render_line(0).text
        # down selects the next entry; its bytes are what's selected
        await pilot.press("down")
        assert view.cursor.position == 512 and view.active_column.current.label == "inner.tar"
        assert view.selection.ranges[0][:2] == (512, 1024 + 10240)
        # the same level as hex keeps the cursor on the same byte
        await pilot.press("f2")
        await pilot.pause()
        assert isinstance(app.screen, PickModal)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert view.level.view.name == "hex" and view.active == (0, 1) and view.cursor.position == 512
        assert "0200: 69 6e 6e 65 72" in view.render_line(view.cursor.y - int(view.scroll_y) + 1).text


async def test_drill_into_nested_tar_and_edit(nested_tar):
    app = TexxdApp(nested_tar)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        # down from "dir" to inner.tar, open it
        await pilot.press("down", "enter")
        await pilot.pause()
        assert [level.base.name for level in view.levels] == ["outer.tar", "inner.tar"]
        assert view.levels[1].root_offset == 1024 and view.level.view.name == "tar"

        # inner.tar's first entry is data.json, which is JSON, or text, or bytes
        await pilot.press("enter")
        await pilot.pause()
        assert [level.base.name for level in view.levels] == ["outer.tar", "inner.tar", "data.json"]
        assert [v.name for v in view.level.views] == ["json", "text (utf-8)", "hex"]
        assert view.level.view.name == "json" and view.data.read(0, 9) == b'{"a": 1}\n'

        use(view, "hex")
        await pilot.press("tab", "X")
        await pilot.press("delete")  # resizes it, and both tars are fixed up
        await pilot.press("insert", *"!!")
        await pilot.pause()
        assert view.data.read(0, 20) == b'X!!a": 1}\n'
        await pilot.press("ctrl+s")
        await pilot.pause()

        # close it: lands on data.json in inner.tar's listing
        await pilot.press("escape")
        await pilot.pause()
        assert len(view.levels) == 2
        assert view.active_column.current.label == "data.json"

    with tarfile.open(nested_tar) as outer:
        inner = tarfile.open(fileobj=outer.extractfile("inner.tar"))
        assert inner.extractfile("data.json").read() == b'X!!a": 1}\n'


async def open_inner_as_hex(pilot, view):
    """Open inner.tar, and look at both levels as hex."""
    await pilot.press("down", "enter")
    await pilot.pause()
    use(view, "hex", 0)
    use(view, "hex", 1)
    await pilot.pause()
    assert len(view.levels) == 2 and view.active == (1, 1)


async def test_outer_levels_stay_open_and_scroll_their_whole_range(nested_tar):
    app = TexxdApp(nested_tar)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await open_inner_as_hex(pilot, view)
        # rows line up: the outer level shows the same bytes at its own offsets
        assert view.scroll_x == view.max_scroll_x > 0
        view.scroll_to(x=0, animate=False, immediate=True)
        await pilot.pause()
        assert "│ 0000: 64 61 74 61" in view.render_line(1).text
        assert view.render_line(1).text.startswith("0400: 64 61 74 61")

        # shift+tab walks back into the outer level; the same bytes stay on the same rows
        await pilot.press("shift+tab", "shift+tab")
        await pilot.pause()
        await pilot.pause()
        assert view.active == (0, 1)
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
        await pilot.press("tab", "tab")
        await pilot.pause()
        await pilot.pause()
        assert view.active == (1, 1)
        assert view.cursor.position == view.data.size  # inner levels can be appended to too


async def test_clicking_levels(nested_tar):
    app = TexxdApp(nested_tar)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await open_inner_as_hex(pilot, view)
        view.scroll_to(x=0, animate=False, immediate=True)
        await pilot.pause()
        # the outer level's hex, on a row inside inner.tar: moves there without scrolling
        top = int(view.scroll_y)
        await pilot.click(HexView, offset=(10, 2))
        await pilot.pause()
        await pilot.pause()
        assert view.active == (0, 1)
        assert view.cursor.position == 1024 + 16 + 1
        assert int(view.scroll_y) == top + 1024 // 16

        # a level's name moves the cursor to that level; the name of the one it's on picks its view
        view.scroll_to(x=view.max_scroll_x, animate=False, immediate=True)
        await pilot.pause()
        x = view._level_x(1, 16) - int(view.scroll_x) + 2
        await pilot.click(HexView, offset=(x, 0))
        await pilot.pause()
        await pilot.pause()
        assert view.active == (1, 1)
        assert view.cursor.position == 17
        await pilot.click(HexView, offset=(x, 0))
        await pilot.pause()
        assert isinstance(app.screen, PickModal)
        await pilot.press("escape")
        await pilot.pause()

        # escape closes the rightmost level
        await pilot.press("escape")
        await pilot.pause()
        assert len(view.levels) == 1


async def test_levels_follow_resizes_and_close_when_cut(nested_tar):
    app = TexxdApp(nested_tar)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert len(view.levels) == 2
        # an edit before the open level moves it, one inside resizes it
        app.buffer.insert(0, b"\0")
        app.buffer.insert(5000, b"\0")
        await pilot.pause()
        assert len(view.levels) == 2
        assert view.levels[1].root_offset == 1025 and view.data.size == 10241
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
        assert view.level.view.name == "reversed"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert view.active == (1, 1)
        assert view.levels[1].root_offset is None
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
        assert view.active == (0, 0)
        assert view.cursor.position == 0
        assert view.render_line(1).text.startswith("  payload")


async def test_derived_edits_undo_and_save(tmp_path, monkeypatch):
    monkeypatch.setattr("texxd.formats.registry", lambda: [Reversed, Binary])
    path = tmp_path / "rev.bin"
    path.write_bytes(b"REV" + b"olleh")
    app = TexxdApp(path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("tab", "J")
        await pilot.pause()
        assert view.data.read(0, 5) == b"Jello"
        # the edit is the document's, though the file itself hasn't changed yet
        assert app.document.modified and not app.buffer.modified
        header = "".join(segment.text for segment in view._render_header(view.cursor.bytes_per_line))
        assert "rev.bin*" in header
        # undo from the outer level goes back in to show it
        await pilot.press("shift+tab", "shift+tab")
        await pilot.pause()
        assert len(view.levels) == 2 and view.active[0] == 0
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert view.active[0] == 1 and view.data.read(0, 5) == b"hello"
        await pilot.press("ctrl+y", "ctrl+s")
        await pilot.pause()
        assert path.read_bytes() == b"REV" + b"olleJ"
        assert not app.document.modified


async def test_stale_bytes_are_dimmed_and_ask_before_editing(tmp_path, monkeypatch):
    monkeypatch.setattr("texxd.formats.registry", lambda: [Reversed, Binary])
    path = tmp_path / "rev.bin"
    path.write_bytes(b"REV" + b"olleh")
    app = TexxdApp(path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("tab", "J")
        await pilot.press("shift+tab", "shift+tab")
        await pilot.pause()
        assert view.active == (0, 0)
        use(view, "hex")
        styles = [None] * 8
        view.highlights.highlight(view.data.read(0, 8), 0, styles)
        assert [bool(s and s.dim) for s in styles] == [False] * 3 + [True] * 5
        # typing over them asks first; committing writes the edit back, and the typing is dropped
        view.set_active(0, 1, 4)
        await pilot.press("0")
        await pilot.pause()
        assert isinstance(app.screen, ChoiceModal)
        await pilot.press("c")
        await pilot.pause()
        assert not isinstance(app.screen, ChoiceModal)
        assert app.buffer.read(0, 8) == b"REVolleJ" and not app.document.stale()
        await pilot.press("0", "0")
        await pilot.pause()
        assert app.buffer.read(4, 1) == b"\0" and view.levels[1].node.data.read(0, 5) == b"Jel\0o"


async def test_text_editing(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_bytes("one\ntwo\n日本\n".encode())
    app = TexxdApp(path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = hex_view(app)
        # a text file opens as text
        assert view.level.view.name == "text (utf-8)" and view.active == (0, 1)
        assert view.node.fmt.contents == "text" and view.insert_mode
        # rows are lines
        assert "2  two" in view.render_line(2).text
        # typing inserts characters, the arrows move by them, backspace removes a whole one
        await pilot.press("down", "down", "end", "left")
        assert view.cursor.position == 11
        await pilot.press("x", "enter", "y")
        await pilot.pause()
        assert view.data.read(0, 30) == "one\ntwo\n日x\ny本\n".encode()
        await pilot.press("backspace", "backspace", "backspace")
        await pilot.pause()
        assert view.data.read(0, 30) == "one\ntwo\n日本\n".encode()
        await pilot.press("backspace")  # all three bytes of it
        await pilot.pause()
        assert view.data.read(0, 30) == "one\ntwo\n本\n".encode()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert path.read_bytes() == "one\ntwo\n本\n".encode()
        # the same bytes as hex, with the cursor where it was
        use(view, "hex")
        await pilot.pause()
        assert view.cursor.position == 8 and view.render_line(1).text.startswith("0000: 6f 6e 65 0a")


async def test_json_tree(tmp_path):
    path = tmp_path / "app.json"
    path.write_bytes(b'{\n  "name": "texxd",\n  "authors": [{"name": "Gaz"}, {"name": "Claude"}],\n  "n": 3\n}\n')
    app = TexxdApp(path)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        view = hex_view(app)
        # a JSON file opens as its tree
        assert [v.name for v in view.level.views] == ["json", "text (utf-8)", "hex"]
        assert view.active == (0, 0) and view.cursor.position == 0
        lines = [view.render_line(y).text for y in range(1, 6)]
        # each node on its row
        assert "▾ { 3 keys" in lines[0]
        assert 'name: "texxd"' in lines[1] and "▸ authors: [{" in lines[2] and "n: 3" in lines[3]
        # unfold, go in, and back out
        await pilot.press("down", "down", "right")
        await pilot.pause()
        assert "▾ authors: [ 2 items" in view.render_line(3).text and "▸ [0]: {" in view.render_line(4).text
        await pilot.press("right", "right", "right")
        await pilot.pause()
        assert "▸ [0]" not in view.render_line(4).text and 'name: "Gaz"' in view.render_line(5).text
        assert "$.authors[0].name" in view.status
        # out to [0], fold it, out to authors, fold that
        await pilot.press("left", "left", "left", "left")
        await pilot.pause()
        assert "▸ authors: [{" in view.render_line(3).text and view.cursor.position == 0x17
        # the selected node's bytes are highlighted in the other levels
        assert view.selection.ranges[0][:2] == (0x17, 0x17 + len('"authors": [{"name": "Gaz"}, {"name": "Claude"}]'))


async def open_tree(pilot, view):
    await pilot.pause()
    assert view.active == (0, 0) and view.level.view.name == "json"


async def test_editing_json_values_in_place(tmp_path):
    path = tmp_path / "app.json"
    path.write_bytes(b'{\n  "v": "1.0",\n  "n": 3,\n  "ok": false\n}\n')
    app = TexxdApp(path)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await open_tree(pilot, view)
        tree = view.active_column
        # Enter edits a number; something that isn't JSON isn't written, and editing carries on
        await pilot.press("down", "down", "enter", "backspace", "x", "enter")
        await pilot.pause()
        assert tree.editing is not None and app.buffer.read(0, 40).count(b"3") == 1
        await pilot.press("backspace", "4", "2", "enter")
        await pilot.pause()
        assert tree.editing is None and b'"n": 42,' in app.buffer.read(0, 60)
        # typing starts over; Escape forgets it
        await pilot.press("down", "n", "u", "escape")
        assert tree.editing is None and b'"ok": false' in app.buffer.read(0, 60)
        await pilot.press("t", "r", "u", "e", "enter")
        await pilot.pause()
        # strings are edited as their text
        await pilot.press("up", "up", "enter", "home", *'a "big" ', "enter")
        await pilot.pause()
        assert app.buffer.read(0, 60) == b'{\n  "v": "a \\"big\\" 1.0",\n  "n": 42,\n  "ok": true\n}\n'
        # each value was one edit
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert b'"v": "1.0"' in app.buffer.read(0, 60)


async def test_opening_a_json_string_as_text(tmp_path):
    path = tmp_path / "app.json"
    path.write_bytes(b'{"notes": "line one", "n": 1}')
    app = TexxdApp(path)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        view = hex_view(app)
        await open_tree(pilot, view)
        await pilot.press("down", "right")
        await pilot.pause()
        assert view.node.name == "$.notes" and view.data.read(0, 20) == b"line one"
        await pilot.press("ctrl+end", "enter", *'two "2"')
        await pilot.pause()
        assert view.data.read(0, 30) == b'line one\ntwo "2"'
        # until it's written back, the tree marks the value as edited
        await pilot.press("shift+tab")
        await pilot.pause()
        assert view.active == (0, 0)
        assert any("✎" in view.render_line(y).text for y in range(1, 4))
        # closing the level writes it back
        view.set_active(1, 1)
        await pilot.press("escape")
        await pilot.pause()
        assert len(view.levels) == 1
        assert app.buffer.read(0, 60) == b'{"notes": "line one\\ntwo \\"2\\"", "n": 1}'
        assert not any("✎" in view.render_line(y).text for y in range(1, 4))
