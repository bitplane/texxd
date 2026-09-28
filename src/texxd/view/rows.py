"""Row layouts: how a level's bytes are split into rows on screen.

The level with the cursor decides the rows, and every other level shows the
same bytes on each row. Hex levels have a fixed number of bytes per row;
other layouts (like lines of text) can have any number.
"""


class Rows:
    """A split of a level's bytes into rows, and how the cursor moves through them.

    Positions are offsets in the level. ``x`` is a position across a row, in
    whatever units suit the layout, so moving up and down can keep it.
    """

    @property
    def count(self) -> int:
        raise NotImplementedError()

    def start(self, row: int) -> int:
        """Offset of the first byte on ``row``."""
        raise NotImplementedError()

    def stop(self, row: int) -> int:
        """Offset just after the last byte on ``row``."""
        raise NotImplementedError()

    def row_of(self, position: int) -> int:
        raise NotImplementedError()

    def x_of(self, position: int) -> int:
        raise NotImplementedError()

    def position_at(self, row: int, x: int) -> int:
        """The position on ``row`` nearest ``x``."""
        raise NotImplementedError()

    def end_of(self, row: int) -> int:
        """The last position the cursor can be at on ``row``."""
        raise NotImplementedError()

    def step(self, position: int, delta: int) -> int:
        """The position ``delta`` steps (bytes, characters) away."""
        raise NotImplementedError()


class ByteRows(Rows):
    """A fixed number of bytes per row, up to the cursor's ``limit``."""

    def __init__(self, bytes_per_line: int, limit: int):
        self.bytes_per_line = bytes_per_line
        self.limit = limit

    @property
    def count(self) -> int:
        return self.limit // self.bytes_per_line + 1

    def start(self, row: int) -> int:
        return row * self.bytes_per_line

    def stop(self, row: int) -> int:
        return (row + 1) * self.bytes_per_line

    def row_of(self, position: int) -> int:
        return position // self.bytes_per_line

    def x_of(self, position: int) -> int:
        return position % self.bytes_per_line

    def position_at(self, row: int, x: int) -> int:
        return min(self.start(row) + min(x, self.bytes_per_line - 1), self.limit)

    def end_of(self, row: int) -> int:
        return min(self.stop(row) - 1, self.limit)

    def step(self, position: int, delta: int) -> int:
        return position + delta
