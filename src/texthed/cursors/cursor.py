"""Base cursor class for hex editor navigation and highlighting."""

from typing import Optional, List
from rich.style import Style
from textual import events
from textual.widget import Widget
from textual.message import Message

from ..highlighters.highlighter import Highlighter
from ..log import get_logger

logger = get_logger(__name__)


class CursorMoved(Message):
    """Message sent when cursor position changes."""

    def __init__(self, position: int) -> None:
        self.position = position
        super().__init__()


class ScrollRequest(Message):
    """Message sent to request scrolling to a specific line."""

    def __init__(self, line: int) -> None:
        self.line = line
        super().__init__()


class Cursor(Highlighter, Widget):
    """Base cursor class that handles navigation and provides highlighting.

    A cursor is a special highlighter that:
    - Tracks position in the file
    - Handles navigation events
    - Highlights its current position
    - Can emit write events and scroll position changes
    """

    def __init__(
        self,
        bytes_per_line: int = 16,
        view_height: int = 10,
        parent_column=None,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
        disabled: bool = False,
    ):
        """Initialize cursor.

        Args:
            bytes_per_line: Number of bytes per line
            view_height: Height of the view for page up/down calculations
        """
        Highlighter.__init__(self)
        Widget.__init__(self, name=name, id=id, classes=classes, disabled=disabled)
        self.position = 0
        self.parent_column = parent_column
        self.bytes_per_line = bytes_per_line
        self.is_active = False

        # Styles for different states
        self.active_style = Style(bgcolor="bright_white", color="black")
        self.inactive_style = Style(bgcolor="grey30", color="grey70")

    @property
    def file_size(self) -> int:
        """Get current file size from parent column."""
        return self.parent_column.file_size

    @property
    def view_height(self) -> int:
        """Get current view height from hex view."""
        return self.hex_view.size.height

    @property
    def x(self) -> int:
        """Get x coordinate (column within line)."""
        return self.position % self.bytes_per_line

    @property
    def y(self) -> int:
        """Get y coordinate (line number)."""
        return self.position // self.bytes_per_line

    def highlight(self, data: bytes, file_offset: int, styles: List[Optional[Style]]) -> None:
        """Apply cursor highlighting to the styles array."""
        if not data or not self.is_active:
            return

        # Check if cursor is in this data range
        data_start = file_offset
        data_end = file_offset + len(data)

        if self.position >= data_start and self.position < data_end:
            # Calculate index within this data chunk
            cursor_index = self.position - data_start

            # Apply cursor style (only when active)
            if styles[cursor_index] is None:
                styles[cursor_index] = self.active_style
            else:
                styles[cursor_index] = styles[cursor_index] + self.active_style

    def _combine_styles(self, existing: Optional[Style], new: Style) -> Style:
        """Combine an existing style with a new style."""
        if existing is None:
            return new
        return existing + new

    def handle_event(self, event: events.Event) -> bool:
        """Handle navigation events.

        Args:
            event: The event to handle

        Returns:
            True if the event was handled, False otherwise
        """
        logger.debug(f"Cursor.handle_event: {event}")

        if isinstance(event, events.MouseDown):
            # Handle mouse clicks
            if hasattr(self.parent_column, "get_byte_position"):
                position = self.parent_column.get_byte_position(event.x, event.y)
                if position is not None:
                    self._set_position(position)
                    return True
            return False

        if not isinstance(event, events.Key):
            logger.debug("Not a key event")
            return False

        key = event.key
        logger.debug(f"Cursor handling key: {key}")
        handled = True

        if key == "left":
            self.move_x(-1)
        elif key == "right":
            self.move_x(1)
        elif key == "up":
            self.move_y(-1)
        elif key == "down":
            self.move_y(1)
        elif key == "home" or key == "\x01":  # Home or Ctrl+A
            self.set_x(0)
        elif key == "end" or key == "\x04":  # End or Ctrl+D
            self.set_x(self.bytes_per_line - 1)
        elif key == "ctrl+home":
            self._move_to_file_start()
        elif key == "ctrl+end":
            self._move_to_file_end()
        elif key == "pageup":
            self.move_y(-self.view_height)
        elif key == "pagedown":
            self.move_y(self.view_height)
        else:
            handled = False

        return handled

    def move_x(self, delta: int) -> None:
        """Move cursor horizontally with wrapping."""
        old_pos = self.position
        new_x = self.x + delta
        current_y = self.y

        # Handle wrapping
        if new_x < 0:
            # Wrap to previous line
            if current_y > 0:
                new_y = current_y - 1
                new_x = self.bytes_per_line - 1
                new_position = new_y * self.bytes_per_line + new_x
                self._set_position(max(0, min(new_position, self.file_size - 1)))
        elif new_x >= self.bytes_per_line:
            # Wrap to next line
            max_y = (self.file_size - 1) // self.bytes_per_line
            if current_y < max_y:
                new_y = current_y + 1
                new_x = 0
                new_position = new_y * self.bytes_per_line + new_x
                self._set_position(max(0, min(new_position, self.file_size - 1)))
        else:
            # Normal horizontal movement
            new_position = current_y * self.bytes_per_line + new_x
            self._set_position(max(0, min(new_position, self.file_size - 1)))

        logger.debug(f"move_x({delta}): {old_pos} -> {self.position}")

    def move_y(self, delta: int) -> None:
        """Move cursor vertically, preserving x position."""
        old_pos = self.position
        current_x = self.x
        current_y = self.y
        new_y = current_y + delta

        # Calculate max y we can reach with current x
        max_possible_y = (self.file_size - 1 - current_x) // self.bytes_per_line

        # Clamp the movement
        new_y = max(0, min(new_y, max_possible_y))

        # If no movement possible, return
        if new_y == current_y:
            logger.debug(f"move_y({delta}): no movement from {old_pos}")
            return

        new_position = new_y * self.bytes_per_line + current_x
        self._set_position(new_position)
        logger.debug(f"move_y({delta}): {old_pos} -> {self.position}")

    def set_x(self, x: int) -> None:
        """Set x coordinate (column within line)."""
        current_y = self.y
        new_x = max(0, min(x, self.bytes_per_line - 1))
        new_position = current_y * self.bytes_per_line + new_x
        self._set_position(max(0, min(new_position, self.file_size - 1)))

    def set_y(self, y: int) -> None:
        """Set y coordinate (line number), preserving x position."""
        current_x = self.x
        max_y = max(0, (self.file_size - 1) // self.bytes_per_line)
        new_y = max(0, min(y, max_y))
        new_position = new_y * self.bytes_per_line + current_x
        new_position = min(new_position, self.file_size - 1)
        self._set_position(new_position)

    def _move_to_file_start(self) -> None:
        """Move cursor to start of file."""
        self._set_position(0)

    def _move_to_file_end(self) -> None:
        """Move cursor to end of file."""
        file_size = self.file_size
        if file_size > 0:
            self._set_position(file_size - 1)

    def _set_position(self, new_position: int) -> None:
        """Set cursor position and notify callbacks."""
        logger.debug(f"_set_position: {self.position} -> {new_position}, file_size={self.file_size}")
        if new_position != self.position:
            self.position = new_position
            logger.debug(f"Position updated to {self.position}")

            # Notify position change
            self.post_message(CursorMoved(self.position))

            # Request scroll if needed
            cursor_line = self.position // self.bytes_per_line
            self.post_message(ScrollRequest(cursor_line))
        else:
            logger.debug("No position change needed")

    def on_focus(self) -> None:
        """Called when cursor gains focus."""
        self.is_active = True

    def on_blur(self) -> None:
        """Called when cursor loses focus."""
        self.is_active = False
