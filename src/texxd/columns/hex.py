"""Hex column widget for displaying data in hexadecimal format."""

from typing import Optional, List
from textual.strip import Strip
from rich.segment import Segment
from rich.style import Style
from textual import events

from .column import Column
from ..cursors.hex_cursor import HexCursor
from ..log import get_logger

logger = get_logger(__name__)


class HexColumn(Column):
    """Widget that displays data in hexadecimal format."""

    def __init__(self, bytes_per_line: int = 16, hex_view=None):
        super().__init__(bytes_per_line=bytes_per_line, hex_view=hex_view)
        self.can_focus = True
        self.cursor = HexCursor(
            bytes_per_line=bytes_per_line,
            parent_column=self,
        )

    def get_content_width(self) -> int:
        """Calculate content width."""
        # 16 bytes = 32 hex chars + 15 spaces + 1 extra space + 1 trailing space = 49 chars
        return self.bytes_per_line * 3 - 1 + (1 if self.bytes_per_line > 8 else 0) + 1

    def _render_hex_line_segments(self, data: bytes, file_offset: int, styles: List[Optional[Style]]) -> List[Segment]:
        """Helper to render a line of hex data into segments."""
        segments = []
        for i, byte in enumerate(data):
            # Add extra space after 8 bytes
            if i == 8:
                segments.append(Segment(" "))

            # Get style for this byte
            style = styles[i] if i < len(styles) else Style()

            # Add hex representation
            hex_text = f"{byte:02x}"
            segments.append(Segment(hex_text, style))

            # Add space after each byte (except when at end of line)
            if i < self.bytes_per_line - 1:
                segments.append(Segment(" "))

        # Pad with spaces if line is shorter than bytes_per_line
        for i in range(len(data), self.bytes_per_line):
            if i == 8:
                segments.append(Segment(" "))
            segments.append(Segment("  "))
            if i < self.bytes_per_line - 1:
                segments.append(Segment(" "))

        # Add trailing space
        segments.append(Segment(" "))

        return segments

    def render_line(self, y: int) -> Strip:
        """Render a line of hex data."""
        # Get data for this line
        file_offset = int(y) * self.bytes_per_line
        data = self._get_line_data(file_offset)

        if not data:
            return Strip.blank(self.get_content_width())

        # Apply highlighting
        styles = self._apply_highlighting(data, file_offset)

        segments = self._render_hex_line_segments(data, file_offset, styles)
        return Strip(segments)

    def calculate_click_position(self, click_offset: int) -> Optional[int]:
        """Calculate byte position within hex column from click offset."""
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

    def on_key(self, event: events.Key) -> bool:
        """Handle key events for the column."""
        logger.debug(f"HexColumn.on_key: {event.key}, cursor exists: {self.cursor is not None}")
        if self.cursor:
            result = self.cursor.handle_event(event)
            logger.debug(f"HexColumn.on_key result: {result}")
            return result
        else:
            logger.debug("No cursor found!")
            return False

    def get_byte_position(self, x: int, y: int) -> Optional[int]:
        """Get byte position from column coordinates."""
        byte_in_line = self.calculate_click_position(x)
        if byte_in_line is not None:
            return y * self.bytes_per_line + byte_in_line
        return None
