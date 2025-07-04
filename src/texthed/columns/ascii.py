"""ASCII column for displaying data in ASCII format."""

from typing import List
from rich.segment import Segment
from rich.style import Style

from .column import DataColumn


class AsciiColumn(DataColumn):
    """Column that displays data in ASCII format."""

    def __init__(self, bytes_per_line: int = 16, cursor=None):
        super().__init__(cursor=cursor)
        self.bytes_per_line = bytes_per_line

    @property
    def width(self) -> int:
        """ASCII column width: 1 char per byte."""
        return self.bytes_per_line

    def render_line(self, data: bytes, file_offset: int, line_number: int) -> List[Segment]:
        """Render the ASCII representation of the data."""
        segments = []
        styles = self._apply_highlighting(data, file_offset)

        for i, byte in enumerate(data):
            # Get style for this byte
            style = styles[i] if i < len(styles) else Style()

            # Convert byte to ASCII
            ascii_char = chr(byte) if 32 <= byte <= 127 else "."
            segments.append(Segment(ascii_char, style))

        # Pad with spaces if line is shorter than bytes_per_line
        for i in range(len(data), self.bytes_per_line):
            segments.append(Segment(" ", Style()))

        return segments
