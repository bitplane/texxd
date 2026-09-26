"""Cursor position and movement for the hex view."""


class Cursor:
    """A byte position in a grid of ``bytes_per_line`` columns.

    ``limit`` is the highest position the cursor may reach. For data that can
    grow that's the size itself (one past the last byte, for appending);
    otherwise it's the last byte.

    ``nibble`` is 1 when the high half of a hex byte has been typed and the
    low half is next. Any movement resets it.
    """

    def __init__(self, bytes_per_line: int = 16):
        self.position = 0
        self.nibble = 0
        self.bytes_per_line = bytes_per_line
        self.limit = 0

    @property
    def x(self) -> int:
        """Column within the line."""
        return self.position % self.bytes_per_line

    @property
    def y(self) -> int:
        """Line number."""
        return self.position // self.bytes_per_line

    def set_position(self, position: int) -> bool:
        """Move to ``position``, clamped to the valid range. Returns True if it moved."""
        position = max(0, min(position, self.limit))
        moved = position != self.position
        self.position = position
        self.nibble = 0
        return moved

    def move(self, delta: int) -> bool:
        """Move by ``delta`` bytes, wrapping across lines."""
        return self.set_position(self.position + delta)

    def move_lines(self, delta: int) -> bool:
        """Move by ``delta`` lines, keeping the column if that line reaches it."""
        target = self.position + delta * self.bytes_per_line
        if target < 0:
            target = self.x
        elif target > self.limit:
            last_line = self.limit // self.bytes_per_line
            target = min(last_line * self.bytes_per_line + self.x, self.limit)
            if target // self.bytes_per_line == self.y and delta > 0:
                target = self.position
        return self.set_position(target)

    def line_start(self) -> bool:
        return self.set_position(self.y * self.bytes_per_line)

    def line_end(self) -> bool:
        return self.set_position(self.y * self.bytes_per_line + self.bytes_per_line - 1)

    def file_start(self) -> bool:
        return self.set_position(0)

    def file_end(self) -> bool:
        return self.set_position(self.limit)
