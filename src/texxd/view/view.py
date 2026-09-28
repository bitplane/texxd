"""The hex view: a strip of levels, each a view of the one to its left.

A level is one node's columns: address, hex, ascii, and whatever else the
registered views add, like a tar listing. Opening a region adds the child's
level to the right.

The level with the cursor drives the rows: it can scroll through its whole
range, and every other level shows the same bytes on the same rows, at its
own offsets. Outer levels always have something on every row; inner levels
only have bytes on the rows their data covers, and are blank elsewhere.
Moving the cursor to another level re-maps the rows to that level, keeping
bytes on the same screen rows where it can.

How a level splits into rows is up to its columns: usually a fixed number of
bytes per row, but a text level's rows are lines of text. While it has the
cursor, other levels show each line's bytes; while another level has it, the
text level shows the characters in each of that level's rows.

Levels line up by where their bytes are in the root buffer. A level whose
bytes don't map there directly (derived data, like a decompressed stream)
can't line up, so it's only drawn while it has the cursor.

The view doesn't know what kinds of column there are: columns handle their
own keys and clicks, and say what they highlight and what they can open.
"""

from collections.abc import Callable
from typing import Optional

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip

from ..data import Buffer, EditError, ResizeError
from ..dialogs import ChoiceModal, GoToOffsetModal, PickModal
from ..document import Document
from ..formats import Format, Region
from ..log import get_logger
from ..node import Node, StaleError
from .columns import Column, CursorCell
from .cursor import Cursor
from .highlight import (
    ACTIVE_STYLE,
    INACTIVE_STYLE,
    DataHighlighter,
    EditHighlighter,
    Highlights,
    RangeHighlighter,
    StaleHighlighter,
)
from .registry import HEX, View, text_in, views_for
from .rows import ByteRows, Rows

logger = get_logger(__name__)

# widest first; uses the widest that fits the rightmost level on screen
BYTES_PER_LINE = (16, 8, 4)

LEVEL_GAP = " │ "
HEADER_LINES = 1


class Level:
    """A node in the strip, and the view of it that's showing.

    ``base`` is the node the level is for: the root, or a region opened from
    the level to its left. ``node`` is what its view shows: usually ``base``
    itself, or the whole of it opened as some format (text, a JSON document).
    """

    def __init__(self, base: Node, origin: Optional[int] = None, view: Optional[View] = None):
        self.base = base
        # the column of the level to the left that opened this one
        self.origin = origin
        self.views = views_for(base)
        for choice in [view] if view else self.views:
            try:
                self.show(choice)
                return
            except Exception:
                logger.exception(f"couldn't show {base.name} as {choice.name}")
        self.show(HEX)

    def show(self, view: View) -> None:
        """Switch to ``view``. Raises if it can't show this node."""
        node = view.shown(self.base)
        self.view = view
        self.node = node
        self.columns: list[Column] = view.columns(node)
        self.insert_mode = node.resizable and any(column.prefers_insert for column in self.columns)

    @property
    def flexible(self) -> bool:
        """True if some of its columns take whatever room there is."""
        return any(column.flexible for column in self.columns)

    @property
    def byte_colours(self) -> bool:
        """True if its bytes should be coloured by kind (nulls, spaces...), as they are in hex."""
        return not any(column.prefers_insert for column in self.columns)

    def rows(self, bytes_per_line: int, limit: int) -> Rows:
        """How to split it into rows while it has the cursor."""
        for column in self.columns:
            rows = column.rows(bytes_per_line, limit)
            if rows is not None:
                return rows
        return ByteRows(bytes_per_line, limit)

    @property
    def size(self) -> int:
        return self.node.data.size

    @property
    def root_offset(self) -> Optional[int]:
        """Where the shown node's byte 0 is in the root buffer, if its bytes are there."""
        return self.node.data.to_root(0)

    @property
    def default_column(self) -> int:
        """Where the cursor goes when it arrives without a column in mind."""
        return next(i for i, c in enumerate(self.columns) if c.focusable)

    def widths(self, bytes_per_line: int) -> list[int]:
        return [column.width(bytes_per_line, self.size) for column in self.columns]

    def width(self, bytes_per_line: int) -> int:
        widths = self.widths(bytes_per_line)
        return sum(widths) + len(widths) - 1


class LocationChanged(Message):
    """The cursor, level or mode changed, so status displays need updating."""


class HexView(ScrollView, can_focus=True):
    """Shows a node's bytes as a hex dump, and lets you dig into its structure."""

    DEFAULT_CSS = """
    HexView {
        width: 1fr;
        height: 1fr;
    }
    """

    # These are here for the footer. The keys are really handled in on_key, because
    # bindings run out of order with typed keys: priority ones run as soon as the key
    # arrives, others once it has bubbled up, and either way a fast burst like
    # "insert f f" or "a ctrl+z" gets reordered.
    BINDINGS = [
        Binding("tab", "next_column", "Column"),
        Binding("enter", "open", "Open"),
        Binding("escape", "close_level", "Close"),
        Binding("insert", "toggle_insert", "Ins/Ovr"),
        Binding("f2", "choose_view", "View"),
        Binding("ctrl+g", "goto", "Go to"),
        Binding("ctrl+k", "commit", "Commit"),
    ]

    ACTION_KEYS = {
        "tab": "next_column",
        "shift+tab": "previous_column",
        "insert": "toggle_insert",
        "ctrl+g": "goto",
        "ctrl+k": "commit",
        "f2": "choose_view",
        "enter": "open",
        "escape": "close_level",
        "ctrl+z": "app.undo",
        "ctrl+y": "app.redo",
        "ctrl+s": "app.save",
    }

    gap_style = Style(color="grey42")

    def __init__(self, document: Document, **kwargs) -> None:
        super().__init__(**kwargs)
        self.document = document
        node = document.root
        self.levels = [Level(node)]
        node = self.levels[0].node
        self.cursor = Cursor()
        # (level index, column index) of the column with the cursor; that level drives the rows
        self.active = (0, self.levels[0].default_column)
        # centre the cursor next time it has to scroll, for jumps
        self._center = False
        self.edits = EditHighlighter(node.data)
        self.stale = StaleHighlighter(document, node.data)
        self.selection = RangeHighlighter()
        self.highlights = Highlights()
        self.highlights["data"] = DataHighlighter()
        self.highlights["edits"] = self.edits
        self.highlights["stale"] = self.stale
        self.highlights["selection"] = self.selection
        self._update_layout()

    @property
    def level(self) -> Level:
        """The level with the cursor, which decides what each row shows."""
        return self.levels[self.active[0]]

    @property
    def node(self) -> Node:
        return self.level.node

    @property
    def data(self):
        return self.level.node.data

    @property
    def insert_mode(self) -> bool:
        return self.level.insert_mode

    @insert_mode.setter
    def insert_mode(self, value: bool) -> None:
        self.level.insert_mode = value

    @property
    def active_column(self) -> Column:
        level, column = self.active
        return self.levels[level].columns[column]

    @property
    def location(self) -> int:
        return self.cursor.position

    @property
    def status(self) -> str:
        """A description of where the cursor is, for status bars."""
        size = self.data.size
        pos = self.cursor.position
        mode = "INS" if self.insert_mode else "OVR"
        path = " › ".join(f"[{lv.base.name}]" if lv is self.level else lv.base.name for lv in self.levels)
        if size == 0:
            return f"{path} │ empty │ {mode}"
        percent = min(100, (pos + 1) * 100 // size)
        where = f"0x{pos:x} ({pos}) of 0x{size:x} │ {percent:3d}%"
        described = self.active_column.describe(pos)
        if described:
            where = f"{described} │ {where}"
        return f"{path} │ {where} │ {mode}"

    def relayout(self, position: Optional[int] = None) -> None:
        """The rows changed (like a tree folding): lay them out again, and move the cursor to ``position``."""
        self._update_layout()
        if position is not None:
            self.cursor.set_position(position)
        self._cursor_moved()

    def go_to(self, offset: int) -> None:
        """Move the cursor to ``offset`` in the level with the cursor."""
        self.cursor.set_position(offset)
        self._center = True
        self._cursor_moved()

    def reveal(self, buffer: Buffer, offset: int) -> None:
        """Move the cursor to ``offset`` in ``buffer``.

        Stays on the level with the cursor if it shows that byte, otherwise
        goes to the innermost level that does.
        """
        here = self.active[0]
        for index in [here, *reversed(range(len(self.levels)))]:
            data = self.levels[index].node.data
            position = data.from_buffer(offset) if data.root is buffer else None
            if position is None:
                continue
            if index == here:
                self.go_to(position)
            else:
                self._center = True
                self.set_active(index, self.levels[index].default_column, position)
            return

    def on_mount(self) -> None:
        self.document.history.subscribe(self._on_data_change)
        self.post_message(LocationChanged())

    def on_unmount(self) -> None:
        self.document.history.unsubscribe(self._on_data_change)

    def on_resize(self, event: events.Resize) -> None:
        self._update_layout()
        self._scroll_to_cursor()

    def on_focus(self) -> None:
        self._update_selection()
        self.refresh()

    def on_blur(self) -> None:
        self._update_selection()
        self.refresh()

    # layout

    def _shift(self, old: Level, new: Level) -> Optional[int]:
        """What to add to an offset in ``old`` to get the same byte in ``new``, if they line up."""
        if old is new:
            return 0
        old_base, new_base = old.root_offset, new.root_offset
        if old_base is None or new_base is None:
            return None
        return old_base - new_base

    def _level_x(self, index: int, bytes_per_line: int) -> int:
        """Where level ``index`` starts in the strip."""
        return sum(level.width(bytes_per_line) + len(LEVEL_GAP) for level in self.levels[:index])

    def _update_layout(self) -> None:
        """Fit the rightmost level to the screen and update the scrollable size."""
        size = self.data.size
        self.cursor.limit = size if self.node.resizable else max(0, size - 1)
        if self.cursor.position > self.cursor.limit:
            self.cursor.set_position(self.cursor.limit)
        # the bytes at the top, to keep there if the rows change
        old_rows = self.cursor.rows
        top = old_rows.start(max(0, min(int(self.scroll_y), old_rows.count - 1)))

        available = self.scrollable_content_region.width if self.is_mounted else 0
        bytes_per_line = BYTES_PER_LINE[0]
        # flexible levels fit whatever's left, so size the rows for the rightmost other one
        sizing = next((level for level in reversed(self.levels) if not level.flexible), None)
        if available and sizing is not None:
            bytes_per_line = BYTES_PER_LINE[-1]
            for candidate in BYTES_PER_LINE:
                if sizing.width(candidate) <= available:
                    bytes_per_line = candidate
                    break
        self.cursor.bytes_per_line = bytes_per_line
        self._fit_columns(bytes_per_line, available)

        rows = self.level.rows(bytes_per_line, self.cursor.limit)
        self.cursor.layout = None if isinstance(rows, ByteRows) else rows
        self.highlights["data"].enabled = self.level.byte_colours
        width = self._level_x(len(self.levels), bytes_per_line) - len(LEVEL_GAP)
        self.virtual_size = Size(width, rows.count + HEADER_LINES)
        if self.is_mounted and rows.row_of(top) != int(self.scroll_y):
            self.scroll_to(y=rows.row_of(top), animate=False, immediate=True)
        self._update_selection()
        self.refresh()

    def _fit_columns(self, bytes_per_line: int, available: int) -> None:
        """Give the rightmost level's flexible columns the room left on screen.

        Flexible columns further left are kept compact, to leave room for it.
        """
        if not available:
            return
        for level in self.levels[:-1]:
            for column in level.columns:
                if column.flexible:
                    column.fit(max(column.min_width, available // 3))
        index = len(self.levels) - 1
        level = self.levels[index]
        if not level.flexible:
            return
        fixed = [w for column, w in zip(level.columns, level.widths(bytes_per_line)) if not column.flexible]
        taken = sum(fixed) + len(level.columns) - 1
        room = available - self._level_x(index, bytes_per_line) - taken
        if room < max(column.min_width for column in level.columns if column.flexible):
            # not much left: it'll be scrolled to anyway, so fill the screen
            room = available - taken
        for column in level.columns:
            if column.flexible:
                column.fit(room)

    def _update_selection(self) -> None:
        """Tell every column where the cursor is, and highlight what the active one selects."""
        self.selection.ranges = []
        for index, level in enumerate(self.levels):
            opened_by = self.levels[index + 1] if index + 1 < len(self.levels) else None
            for column_index, column in enumerate(level.columns):
                active = (index, column_index) == self.active
                opened = opened_by.node.region if opened_by and opened_by.origin == column_index else None
                ranges = column.sync(self.cursor.position if active else None, opened, self.has_focus)
                if active:
                    self.selection.ranges = ranges

    # scrolling

    def _visible_lines(self) -> int:
        return self.scrollable_content_region.height - HEADER_LINES

    def _scroll_to_cursor(self) -> None:
        height = self._visible_lines()
        if height > 0:
            line = self.cursor.y
            top = int(self.scroll_y)
            if not top <= line < top + height:
                if self._center:
                    self.scroll_to(y=max(0, line - height // 2), animate=False)
                elif line < top:
                    self.scroll_to(y=line, animate=False)
                else:
                    self.scroll_to(y=line - height + 1, animate=False)
        self._center = False
        self._scroll_to_level()

    def _scroll_to_level(self) -> None:
        """Scroll sideways so the level with the cursor is in view, or at least its column."""
        bytes_per_line = self.cursor.bytes_per_line
        level_index, column_index = self.active
        level = self.levels[level_index]
        left = self._level_x(level_index, bytes_per_line)
        right = left + level.width(bytes_per_line)
        view_width = self.scrollable_content_region.width
        if right - left > view_width:
            # too wide to show it all: settle for the column
            widths = level.widths(bytes_per_line)
            left += sum(w + 1 for w in widths[:column_index])
            right = left + widths[column_index]
        x = int(self.scroll_x)
        if left < x:
            self.scroll_to(x=left, animate=False)
        elif right > x + view_width:
            self.scroll_to(x=right - view_width, animate=False)

    def _after_level_change(self, top_line: int) -> None:
        """Scroll so ``top_line`` is at the top, then make sure the cursor is in view.

        Waits for a refresh first: until then the scrollable size is still the old
        level's, and scrolling would be clamped to it.
        """

        def scroll() -> None:
            self.scroll_to(y=max(0, top_line), animate=False, immediate=True)
            self._scroll_to_cursor()
            self.refresh()

        self.call_after_refresh(scroll)

    def _cursor_moved(self) -> None:
        self._update_selection()
        self._scroll_to_cursor()
        self.refresh()
        self.post_message(LocationChanged())

    def _on_data_change(self) -> None:
        # after whatever made the edit has finished moving the cursor
        self.call_later(self._check_levels)
        self._update_layout()
        self.post_message(LocationChanged())

    def _check_levels(self) -> None:
        """Close levels whose bytes moved out from under them."""
        for index, level in enumerate(self.levels):
            if not level.node.valid:
                self.notify(f"{level.node.name} moved or changed size, so it was closed", severity="warning")
                self.close_levels_after(index - 1)
                return

    # levels

    def set_active(self, level_index: int, column_index: int, position: Optional[int] = None) -> None:
        """Put the cursor in a column, maybe on another level.

        ``position`` is where it goes, as an offset in that level; by default it
        stays on the same byte, or the nearest one the level has. Changing level
        re-maps the rows to the new level, keeping the bytes on screen on the
        same rows.
        """
        old = self.level
        if position is None:
            shift = self._shift(old, self.levels[level_index])
            position = 0 if shift is None else self.cursor.position + shift
        self._switch(old, level_index, column_index, position)

    def _switch(self, old: Level, level_index: int, column_index: int, position: int) -> None:
        """Move the cursor from level ``old`` (which may have just been closed) to a column."""
        self.active = (level_index, column_index)
        self.cursor.nibble = 0
        new = self.level
        if new is old:
            self.cursor.set_position(position)
            self._cursor_moved()
            return
        old_rows = self.cursor.rows
        top = old_rows.start(max(0, min(int(self.scroll_y), old_rows.count - 1)))
        shift = self._shift(old, new)
        self.edits.data = self.stale.data = self.data
        self._update_layout()
        self.cursor.set_position(position)
        self._update_selection()
        self.post_message(LocationChanged())
        if shift is None:
            self._center = True
            self._after_level_change(0)
        else:
            self._after_level_change(self.cursor.rows.row_of(max(0, top + shift)))

    def open_region(self, fmt: type[Format], region: Region, origin: int) -> None:
        """Open a region of the level with the cursor as a new level to its right.

        ``origin`` is the column it was opened from.
        """
        index = self.active[0]
        position = self.cursor.position
        try:
            child = self.node.child(fmt, region)
            level = Level(child, origin)
        except Exception as e:
            logger.exception(f"couldn't open {region.name}")
            self.notify(f"Couldn't open {region.name}: {e}", severity="error")
            return
        del self.levels[index + 1 :]
        self.levels.append(level)
        # stay on the same byte if it's in there
        root = self.data.to_root(position)
        target = level.node.data.from_root(root) if root is not None else None
        if target is None or target >= level.node.data.size:
            target = 0
        self.set_active(len(self.levels) - 1, self.levels[-1].default_column, target)

    def close_levels_after(self, index: int) -> None:
        """Close the levels to the right of ``index``.

        Closing a level means being done with it, so edits to derived data in
        it (a decompressed stream, a string's text) are written back first.
        """
        if index < 0 or index >= len(self.levels) - 1:
            return
        for level in reversed(self.levels[index + 1 :]):
            if level.node.stale:
                try:
                    level.node.commit()
                except (EditError, NotImplementedError) as e:
                    self.notify(f"Couldn't write {level.node.name} back: {e}", severity="error")
        old = self.level
        closing_active = self.active[0] > index
        origin = self.levels[index + 1].origin
        kept = self.levels[index]
        shift = self._shift(old, kept)
        position = self.cursor.position + shift if shift is not None else self.levels[index + 1].base.region.start
        del self.levels[index + 1 :]
        if closing_active:
            # land in the column it was opened from, on the region we came out of
            self._switch(old, index, origin if origin is not None else kept.default_column, position)
        else:
            self._update_layout()
            self._cursor_moved()

    # rendering

    def _render_header(self, bytes_per_line: int) -> list[Segment]:
        segments = []
        modified = self.document.modified
        for index, level in enumerate(self.levels):
            if index:
                segments.append(Segment(LEVEL_GAP, self.gap_style))
            name = level.base.name + ("*" if index == 0 and modified else "")
            if len(level.views) > 1:
                name += f" · {level.view.name} ▾"
            width = level.width(bytes_per_line)
            label = f" {name} "[:width]
            style = Style(bold=True, reverse=level is self.level)
            segments.append(Segment(label, style))
            segments.append(Segment("─" * (width - len(label)), self.gap_style))
        return segments

    def render_line(self, y: int) -> Strip:
        width = self.scrollable_content_region.width
        scroll_x = int(self.scroll_x)
        bytes_per_line = self.cursor.bytes_per_line

        if y < HEADER_LINES:
            strip = Strip(self._render_header(bytes_per_line))
            return strip.crop_extend(scroll_x, scroll_x + width, None).apply_style(self.rich_style)

        rows = self.cursor.rows
        row = y - HEADER_LINES + int(self.scroll_y)
        if row >= rows.count:
            return Strip.blank(width, self.rich_style)
        offset = rows.start(row)

        driving = self.level
        data = self.data.read(offset, rows.stop(row) - offset)
        styles = [None] * len(data)
        self.highlights.highlight(data, offset, styles)

        # where the cursor is on this row, if it's on it
        cursor_index = self.cursor.position - offset if self.cursor.y == row else -1
        segments: list[Segment] = []
        for level_index, level in enumerate(self.levels):
            if level_index:
                segments.append(Segment(LEVEL_GAP, self.gap_style))
            # this line in the level's own offsets, and which of its bytes are the level's
            shift = self._shift(driving, level)
            if shift is None:
                segments.append(Segment(" " * level.width(bytes_per_line)))
                continue
            level_offset = offset + shift
            first = max(0, -level_offset)
            end = min(len(data), level.size - level_offset)
            if level is not driving and first >= end:
                segments.append(Segment(" " * level.width(bytes_per_line)))
                continue
            level_data = data[: max(end, 0)]
            for column_index, column in enumerate(level.columns):
                if column_index:
                    segments.append(Segment(" "))
                cell = None
                in_level = first <= cursor_index < end or (level is driving and cursor_index == len(data))
                if column.byte_cursor and 0 <= cursor_index and in_level:
                    is_active = (level_index, column_index) == self.active and self.has_focus
                    style = ACTIVE_STYLE if is_active else INACTIVE_STYLE
                    cell = CursorCell(cursor_index, style, self.cursor.nibble if is_active else 0)
                segments.extend(
                    column.render(level_offset, level_data, styles, bytes_per_line, level.size, cell, first)
                )

        strip = Strip(segments).crop_extend(scroll_x, scroll_x + width, None)
        return strip.apply_style(self.rich_style)

    # keyboard

    async def on_key(self, event: events.Key) -> None:
        key = event.key
        char = event.character
        if self.active_column.on_key(self, key, char):
            pass
        elif key in self.ACTION_KEYS:
            event.stop()
            event.prevent_default()
            await self.run_action(self.ACTION_KEYS[key])
            return
        elif self._navigate(key):
            pass
        elif key == "delete":
            self._delete()
        elif key == "backspace":
            self._backspace()
        else:
            return
        event.stop()
        event.prevent_default()

    def _navigate(self, key: str) -> bool:
        cursor = self.cursor
        page = max(1, self._visible_lines())
        navigation: dict[str, Callable[[], bool]] = {
            "left": lambda: cursor.move(-1),
            "right": lambda: cursor.move(1),
            "up": lambda: cursor.move_lines(-1),
            "down": lambda: cursor.move_lines(1),
            "home": cursor.line_start,
            "end": cursor.line_end,
            "ctrl+home": cursor.file_start,
            "ctrl+end": cursor.file_end,
            "ctrl+left": lambda: cursor.move(-4),
            "ctrl+right": lambda: cursor.move(4),
            "pageup": lambda: cursor.move_lines(-page),
            "pagedown": lambda: cursor.move_lines(page),
            "ctrl+u": lambda: cursor.move_lines(-(page // 2 or 1)),
            "ctrl+d": lambda: cursor.move_lines(page // 2 or 1),
        }
        if key not in navigation:
            return False
        navigation[key]()
        self._cursor_moved()
        return True

    # editing

    def _edit(self, operation: Callable[[], None]) -> bool:
        """Run an edit, reporting edits that aren't possible here. Returns True if it worked."""
        try:
            operation()
            return True
        except ResizeError:
            where = f" inside {self.node.parent.name}" if self.node.parent else ""
            self.notify(f"{self.node.name}{where} can't change size (yet)", severity="warning")
            return False
        except StaleError as e:
            self._resolve_stale(e.node)
            return False

    def _resolve_stale(self, node: Node) -> None:
        """Ask what to do about derived data's uncommitted edits, for an edit to the bytes they'll replace."""

        def answer(choice: Optional[str]) -> None:
            try:
                if choice == "commit":
                    node.commit()
                elif choice == "discard":
                    node.discard()
            except (EditError, NotImplementedError) as e:
                self.notify(f"Couldn't commit {node.name}: {e}", severity="error")
            self.refresh()

        question = (
            f"{node.name} has edits that haven't been written back to {node.parent.name}, "
            "so these bytes are out of date. Commit them first, or discard them?"
        )
        choices = [("commit", "Commit", "c"), ("discard", "Discard", "d"), ("cancel", "Cancel", "escape")]
        self.app.push_screen(ChoiceModal(question, choices), answer)

    def action_commit(self) -> None:
        """Write edits to derived data (like decompressed contents) back into their parents."""
        if not self.document.stale():
            self.notify("Nothing to commit")
            return
        try:
            self.document.commit()
        except (EditError, NotImplementedError) as e:
            self.notify(f"Commit failed: {e}", severity="error")
        self.refresh()

    def type_nibble(self, digit: int) -> None:
        """Type a hex digit at the cursor."""
        cursor = self.cursor
        position = cursor.position
        if cursor.nibble == 0:
            if self.insert_mode:
                ok = self._edit(lambda: self.data.insert(position, bytes([digit << 4])))
            else:
                old = self.data.read(position, 1)
                low = old[0] & 0x0F if old else 0
                ok = self._edit(lambda: self.data.write(position, bytes([digit << 4 | low])))
            if ok:
                cursor.nibble = 1
        else:
            old = self.data.read(position, 1)[0]
            if self._edit(lambda: self.data.write(position, bytes([old & 0xF0 | digit]))):
                cursor.move(1)
        self._cursor_moved()

    def type_byte(self, value: int) -> None:
        """Type a byte at the cursor."""
        position = self.cursor.position
        if self.insert_mode:
            ok = self._edit(lambda: self.data.insert(position, bytes([value])))
        else:
            ok = self._edit(lambda: self.data.write(position, bytes([value])))
        if ok:
            self.cursor.move(1)
        self._cursor_moved()

    def replace(self, start: int, stop: int, data: bytes) -> bool:
        """Replace bytes ``start`` to ``stop`` of the level with the cursor with ``data``, as one edit.

        Returns True if it worked.
        """
        target = self.data

        def edit() -> None:
            if len(data) == stop - start:
                # the same size: an overwrite, which works even where things can't be resized
                target.write(start, data)
                return
            with target.root.transaction():
                target.delete(start, stop - start)
                target.insert(start, data)

        return self._edit(edit)

    def type_text(self, text: str) -> None:
        """Type text at the cursor, for levels whose data is UTF-8 text."""
        encoded = text.encode("utf-8", "surrogatepass")
        position = self.cursor.position
        rows = self.cursor.rows
        # overwriting replaces a character, but not the end of a line
        end = position
        if not self.insert_mode and position < self.data.size and self.data.read(position, 1) not in b"\r\n":
            end = rows.step(position, 1)

        def edit() -> None:
            with self.data.root.transaction():
                if end > position:
                    self.data.delete(position, end - position)
                self.data.insert(position, encoded)

        if self._edit(edit):
            self.cursor.set_position(position + len(encoded))
        self._cursor_moved()

    def _delete(self) -> None:
        position = self.cursor.position
        if position < self.data.size:
            end = self.cursor.rows.step(position, 1)
            self._edit(lambda: self.data.delete(position, end - position))
        self._cursor_moved()

    def _backspace(self) -> None:
        cursor = self.cursor
        if cursor.nibble:
            cursor.nibble = 0
        elif cursor.position > 0:
            start = cursor.rows.step(cursor.position, -1)
            if self._edit(lambda: self.data.delete(start, cursor.position - start)):
                cursor.set_position(start)
        self._cursor_moved()

    # mouse

    def _hit(self, x: int) -> Optional[tuple[int, int, int]]:
        """Find (level, column, x within column) for a strip x position."""
        bytes_per_line = self.cursor.bytes_per_line
        left = 0
        for level_index, level in enumerate(self.levels):
            if level_index:
                left += len(LEVEL_GAP)
                if x < left:
                    return None
            for column_index, width in enumerate(level.widths(bytes_per_line)):
                if x < left + width + 1:
                    return level_index, column_index, x - left
                left += width + 1
            left -= 1
        return None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.focus()
        hit = self._hit(event.x + int(self.scroll_x))
        if hit is None:
            return
        level_index, column_index, column_x = hit
        level = self.levels[level_index]
        if event.y < HEADER_LINES:
            # a level's name: move to it, on the same byte if it has it; again, to choose its view
            if level is self.level:
                self.action_choose_view()
            else:
                self.set_active(level_index, level.default_column)
            return

        shift = self._shift(self.level, level)
        if shift is None:
            return  # a level that doesn't line up with this one, so it's blank
        bytes_per_line = self.cursor.bytes_per_line
        rows = self.cursor.rows
        row = event.y - HEADER_LINES + int(self.scroll_y)
        below = row >= rows.count
        row = min(row, rows.count - 1)
        line, stop = rows.start(row) + shift, rows.stop(row) + shift
        column = level.columns[column_index]
        position = column.click(line, stop, column_x, bytes_per_line, level.size)
        if position is None:
            return
        if below:
            # past the last row: the end
            position = self.cursor.limit + shift
        if level is not self.level and not 0 <= position < level.size:
            return  # a blank part of another level
        if not column.focusable:
            column_index = level.default_column if level is not self.level else self.active[1]
        self.set_active(level_index, column_index, position)

    # actions

    def _focusable(self) -> list[tuple[int, int]]:
        """Every column the cursor can be in, left to right."""
        return [
            (level_index, column_index)
            for level_index, level in enumerate(self.levels)
            for column_index, column in enumerate(level.columns)
            if column.focusable
        ]

    def _cycle_column(self, step: int) -> None:
        columns = self._focusable()
        index = columns.index(self.active) if self.active in columns else 0
        self.set_active(*columns[(index + step) % len(columns)])

    def action_next_column(self) -> None:
        self._cycle_column(1)

    def action_previous_column(self) -> None:
        self._cycle_column(-1)

    def choose_encoding(self) -> None:
        """Ask which encoding to read the text in the level with the cursor as."""
        node = self.node
        if not hasattr(node.fmt, "using"):
            return

        def chosen(encoding: Optional[str]) -> None:
            if encoding:
                self.show_view(self.active[0], text_in(encoding))

        encodings = node.fmt.suggestions(self.level.base.data)
        self.app.push_screen(PickModal(f"Read as (now {node.fmt.encoding}):", encodings, node.fmt.encoding), chosen)

    def action_choose_view(self) -> None:
        """Ask how to look at the level with the cursor."""
        level = self.level
        level.views = views_for(level.base)
        names = [view.name for view in level.views]

        def chosen(name: Optional[str]) -> None:
            if name:
                self.show_view(self.active[0], level.views[names.index(name)])

        self.app.push_screen(PickModal(f"View {level.base.name} as:", names, level.view.name), chosen)

    def show_view(self, index: int, view: View) -> None:
        """Look at level ``index`` a different way, keeping the cursor on the same byte where it can."""
        level = self.levels[index]
        try:
            shown = view.shown(level.base)
        except Exception as e:
            self.notify(f"Can't show {level.base.name} as {view.name}: {e}", severity="error")
            return
        if level.node.stale and shown is not level.node:
            self.notify(f"{level.node.name} has edits: commit (^k) or undo them first", severity="warning")
            return
        root = self.level.node.data.to_root(self.cursor.position)
        if shown is not level.node:
            # what's open to the right was opened from what it showed before
            self.close_levels_after(index)
        level.show(view)
        position = level.node.data.from_root(root) if root is not None else None
        self.active = (index, level.default_column)
        self.cursor.nibble = 0
        self.edits.data = self.stale.data = self.data
        self._update_layout()
        self.cursor.set_position(position if position is not None else 0)
        self._center = True
        self._cursor_moved()
        # once the new widths are in, so scrolling to it isn't cut short
        self.call_after_refresh(self._scroll_to_cursor)

    def action_toggle_insert(self) -> None:
        if not self.node.resizable:
            self.notify(f"{self.node.name} can't change size, so only overwrite works here", severity="warning")
            return
        self.insert_mode = not self.insert_mode
        self.post_message(LocationChanged())

    def action_goto(self) -> None:
        def done(offset: int | None) -> None:
            if offset is not None:
                self.go_to(offset)

        self.app.push_screen(GoToOffsetModal(self.cursor.limit), done)

    def action_open(self) -> None:
        """Open the region under the cursor, from the active column or else the first that has one."""
        position = self.cursor.position
        level_index, active = self.active
        columns = self.level.columns
        for column_index in [active] + [i for i in range(len(columns)) if i != active]:
            found = columns[column_index].region_at(position)
            if found is None:
                continue
            fmt, region = found
            if region.openable:
                self.open_region(fmt, region, column_index)
            else:
                self.notify(f"{region.name} is a {region.kind}, nothing to open")
            return

    def action_close_level(self) -> None:
        self.close_levels_after(len(self.levels) - 2)
