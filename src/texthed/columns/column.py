"""Base class for all columns."""

from typing import List, Optional
from textual.widgets import Static
from textual.strip import Strip
from rich.style import Style
from textual import events

from ..highlighters.highlighter import Highlighter


class Column(Static):
    """Base class for all columns."""

    def __init__(self, *args, bytes_per_line: int = 16, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.file_size = 0
        self.bytes_per_line = bytes_per_line
        self._highlighters: List[Highlighter] = []

    def add_highlighter(self, highlighter: Highlighter) -> None:
        """Add a highlighter to this column."""
        self._highlighters.append(highlighter)

    def _apply_highlighting(self, data: bytes, file_offset: int) -> List[Optional[Style]]:
        """Apply all registered highlighters to the data."""
        styles: List[Optional[Style]] = [None] * len(data)
        for highlighter in self._highlighters:
            highlighter.highlight(data, file_offset, styles)
        return styles

    def _get_line_data(self, file_offset: int) -> bytes:
        """Get data for a specific line."""
        raise NotImplementedError

    def get_content_width(self) -> int:
        """Get the width of the column's content."""
        raise NotImplementedError

    def render_line(self, y: int) -> Strip:
        """Render a single line of the column."""
        raise NotImplementedError

    def on_key(self, event: events.Key) -> bool:
        """Handle key events for the column.

        Args:
            event: The key event.

        Returns:
            True if the event was handled, False otherwise.
        """
        return False
