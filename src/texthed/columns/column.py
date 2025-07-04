"""Base data column interface for hex editor display."""

from abc import ABC, abstractmethod
from typing import Optional, List
from rich.segment import Segment
from rich.style import Style
from textual import events

from ..highlighter import Highlighter


class DataColumn(ABC):
    """Base class for data columns in the hex editor.

    A data column is responsible for:
    - Defining its display width
    - Rendering its portion of each line
    - Handling events (forwarding to its cursor if applicable)
    - Managing highlighting for its data
    """

    def __init__(self, cursor=None):
        """Initialize data column.

        Args:
            cursor: Optional cursor for this column
        """
        self.cursor = cursor
        self.highlighters: List[Highlighter] = []
        self.has_focus = False

    @property
    @abstractmethod
    def width(self) -> int:
        """Get the display width of this column."""
        pass

    @abstractmethod
    def render_line(self, data: bytes, file_offset: int, line_number: int) -> List[Segment]:
        """Render this column's portion of a line.

        Args:
            data: The bytes for this line
            file_offset: Starting file offset for this line
            line_number: Line number being rendered

        Returns:
            List of segments representing this column's display
        """
        pass

    def add_highlighter(self, highlighter: Highlighter) -> None:
        """Add a highlighter to this column."""
        self.highlighters.append(highlighter)

    def remove_highlighter(self, highlighter: Highlighter) -> None:
        """Remove a highlighter from this column."""
        if highlighter in self.highlighters:
            self.highlighters.remove(highlighter)

    def _apply_highlighting(self, data: bytes, file_offset: int) -> List[Optional[Style]]:
        """Apply all highlighters to the data and return styles.

        Args:
            data: The bytes to highlight
            file_offset: Starting offset of the data in the file

        Returns:
            List of styles (same length as data)
        """
        styles: List[Optional[Style]] = [None] * len(data)

        for highlighter in self.highlighters:
            highlighter.highlight(data, file_offset, styles)

        return styles

    def handle_event(self, event: events.Event) -> bool:
        """Handle an event, forwarding to cursor if applicable.

        Args:
            event: The event to handle

        Returns:
            True if the event was handled, False otherwise
        """
        if self.cursor and self.has_focus:
            return self.cursor.handle_event(event)
        return False

    def focus(self) -> None:
        """Give focus to this column."""
        self.has_focus = True
        if self.cursor:
            self.cursor.on_focus()

    def blur(self) -> None:
        """Remove focus from this column."""
        self.has_focus = False
        if self.cursor:
            self.cursor.on_blur()

    def calculate_click_position(self, click_offset: int) -> Optional[int]:
        """Calculate byte position within this column from click offset.

        Args:
            click_offset: X position within this column

        Returns:
            Byte position within the line (0-15), or None if invalid
        """
        return None  # Base implementation - only override in clickable columns
