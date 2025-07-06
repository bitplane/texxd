"""ASCII column widget for displaying data in ASCII format."""

from typing import Optional, List
from textual.strip import Strip
from rich.segment import Segment
from rich.style import Style
from textual import events

from .column import Column
from ..cursors.ascii_cursor import AsciiCursor


class AsciiColumn(Column):
    """Widget that displays data in ASCII format."""

    def __init__(self, bytes_per_line: int = 16, hex_view=None):
        super().__init__(bytes_per_line=bytes_per_line, hex_view=hex_view)
        self.can_focus = True
        self.cursor = AsciiCursor(
            bytes_per_line=bytes_per_line,
            parent_column=self,
        )

    def get_content_width(self) -> int:
        """Calculate content width."""
        # ASCII column: 1 char per byte
        return self.bytes_per_line

    def _render_ascii_line_segments(
        self, data: bytes, file_offset: int, styles: List[Optional[Style]]
    ) -> List[Segment]:
        """Helper to render a line of ASCII data into segments."""
        segments = []
        for i, byte in enumerate(data):
            # Get style for this byte
            style = styles[i] if i < len(styles) else Style()

            # Convert byte to ASCII
            ascii_char = chr(byte) if 32 <= byte <= 127 else "."
            segments.append(Segment(ascii_char, style))

        # Pad with spaces if line is shorter than bytes_per_line
        for i in range(len(data), self.bytes_per_line):
            segments.append(Segment(" "))
        return segments

    def render_line(self, y: int) -> Strip:
        """Render a line of ASCII data."""
        # Get data for this line
        file_offset = int(y) * self.bytes_per_line
        data = self._get_line_data(file_offset)

        if not data:
            return Strip.blank(self.get_content_width())

        # Apply highlighting
        styles = self._apply_highlighting(data, file_offset)

        segments = self._render_ascii_line_segments(data, file_offset, styles)
        return Strip(segments)

    def calculate_click_position(self, click_offset: int) -> Optional[int]:
        """Calculate byte position within ASCII column from click offset."""
        # ASCII column: simple 1 char per byte
        return min(click_offset, self.bytes_per_line - 1)

    def on_key(self, event: events.Key) -> bool:
        """Handle key events for the column."""
        return self.cursor.handle_event(event)

    def get_byte_position(self, x: int, y: int) -> Optional[int]:
        """Get byte position from column coordinates."""
        byte_in_line = self.calculate_click_position(x)
        if byte_in_line is not None:
            return y * self.bytes_per_line + byte_in_line
        return None
