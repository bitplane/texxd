"""Cursor position and movement for the hex view."""

from typing import Optional

from .rows import ByteRows, Rows


class Cursor:
    """A byte position, moving through rows of ``bytes_per_line`` bytes, or whatever ``rows`` says.

    ``limit`` is the highest position the cursor may reach. For data that can
    grow that's the size itself (one past the last byte, for appending);
    otherwise it's the last byte.

    ``nibble`` is 1 when the high half of a hex byte has been typed and the
    low half is next. Any movement resets it.

    Moving up and down keeps to the column it started in, as far as the rows
    it passes through allow.
    """

    def __init__(self, bytes_per_line: int = 16):
        self.position = 0
        self.nibble = 0
        self.bytes_per_line = bytes_per_line
        self.limit = 0
        # set for layouts other than fixed rows of bytes
        self.layout: Optional[Rows] = None
        self._goal: Optional[int] = None

    @property
    def rows(self) -> Rows:
        return self.layout or ByteRows(self.bytes_per_line, self.limit)

    @property
    def x(self) -> int:
        """Where across its row the cursor is."""
        return self.rows.x_of(self.position)

    @property
    def y(self) -> int:
        """Row number."""
        return self.rows.row_of(self.position)

    def set_position(self, position: int) -> bool:
        """Move to ``position``, clamped to the valid range. Returns True if it moved."""
        position = max(0, min(position, self.limit))
        moved = position != self.position
        self.position = position
        self.nibble = 0
        self._goal = None
        return moved

    def move(self, delta: int) -> bool:
        """Move by ``delta`` steps (bytes, or characters), wrapping across rows."""
        rows = self.rows
        position = self.position
        for _ in range(abs(delta)):
            position = rows.step(position, 1 if delta > 0 else -1)
        return self.set_position(position)

    def move_lines(self, delta: int) -> bool:
        """Move by ``delta`` rows, keeping to the same column where the rows are long enough."""
        rows = self.rows
        goal = self.x if self._goal is None else self._goal
        row = max(0, min(self.y + delta, rows.count - 1))
        moved = self.set_position(rows.position_at(row, goal))
        self._goal = goal
        return moved

    def line_start(self) -> bool:
        return self.set_position(self.rows.start(self.y))

    def line_end(self) -> bool:
        return self.set_position(self.rows.end_of(self.y))

    def file_start(self) -> bool:
        return self.set_position(0)

    def file_end(self) -> bool:
        return self.set_position(self.limit)
