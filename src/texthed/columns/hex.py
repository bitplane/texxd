"""Hex column for displaying data in hexadecimal format."""

from typing import List, Optional
from rich.segment import Segment
from rich.style import Style

from .column import DataColumn


class HexColumn(DataColumn):
    """Column that displays data in hexadecimal format."""

    def __init__(self, bytes_per_line: int = 16, cursor=None):
        super().__init__(cursor=cursor)
        self.bytes_per_line = bytes_per_line

    @property
    def width(self) -> int:
        """Hex column width: 2 chars per byte + space + extra space after 8 bytes + trailing space."""
        # 16 bytes = 32 hex chars + 15 spaces + 1 extra space + 1 trailing space = 49 chars
        return self.bytes_per_line * 3 - 1 + (1 if self.bytes_per_line > 8 else 0) + 1

    def render_line(self, data: bytes, file_offset: int, line_number: int) -> List[Segment]:
        """Render the hex representation of the data."""
        segments = []
        styles = self._apply_highlighting(data, file_offset)

        for i, byte in enumerate(data):
            # Add extra space after 8 bytes
            if i == 8:
                segments.append(Segment(" ", Style()))

            # Get style for this byte
            style = styles[i] if i < len(styles) else Style()

            # Add hex representation
            hex_text = f"{byte:02x}"
            segments.append(Segment(hex_text, style))

            # Add space after each byte (except when at end of line)
            if i < self.bytes_per_line - 1:
                segments.append(Segment(" ", Style()))

        # Pad with spaces if line is shorter than bytes_per_line
        for i in range(len(data), self.bytes_per_line):
            if i == 8:
                segments.append(Segment(" ", Style()))
            segments.append(Segment("  ", Style()))
            if i < self.bytes_per_line - 1:
                segments.append(Segment(" ", Style()))

        # Add trailing space
        segments.append(Segment(" ", Style()))

        return segments

    def calculate_click_position(self, click_offset: int) -> Optional[int]:
        """Calculate byte position within hex column from click offset."""
        # Hex column: "XX XX XX XX  XX XX XX XX XX XX XX XX XX XX XX XX "
        # Each byte is 3 chars (XX + space), with extra space after 8 bytes

        pos = 0
        current_offset = 0

        while pos < self.bytes_per_line and current_offset < click_offset:
            # Add extra space after 8 bytes
            if pos == 8:
                current_offset += 1
                if current_offset >= click_offset:
                    break

            # Each byte takes 2 chars for hex + 1 space (except last)
            byte_end = current_offset + 2
            if pos < self.bytes_per_line - 1:
                byte_end += 1  # Add space

            if click_offset <= byte_end:
                break

            current_offset = byte_end
            pos += 1

        return min(pos, self.bytes_per_line - 1)
