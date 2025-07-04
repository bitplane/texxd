"""Base cursor class for hex editor navigation and highlighting."""

from typing import Optional, List, Callable
from rich.style import Style
from textual import events

from ..highlighter import Highlighter


class Cursor(Highlighter):
    """Base cursor class that handles navigation and provides highlighting.

    A cursor is a special highlighter that:
    - Tracks position in the file
    - Handles navigation events
    - Highlights its current position
    - Can emit write events and scroll position changes
    """

    def __init__(
        self,
        file_size: int = 0,
        bytes_per_line: int = 16,
        on_position_changed: Optional[Callable[[int], None]] = None,
        on_scroll_request: Optional[Callable[[int], None]] = None,
        view_height: int = 10,
    ):
        """Initialize cursor.

        Args:
            file_size: Size of the file in bytes
            bytes_per_line: Number of bytes per line
            on_position_changed: Callback when cursor position changes
            on_scroll_request: Callback to request scroll to line
        """
        self.position = 0
        self.file_size = file_size
        self.bytes_per_line = bytes_per_line
        self.view_height = 10  # Default, will be updated by view
        self.is_active = False
        self.on_position_changed = on_position_changed
        self.on_scroll_request = on_scroll_request

        # Styles for different states
        self.active_style = Style(bgcolor="bright_white", color="black")
        self.inactive_style = Style(bgcolor="grey30", color="grey70")

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
            styles[cursor_index] = self._combine_styles(styles[cursor_index], self.active_style)

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
        if not isinstance(event, events.Key):
            return False

        key = event.key
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

    def move_y(self, delta: int) -> None:
        """Move cursor vertically, preserving x position."""
        current_x = self.x
        current_y = self.y
        new_y = current_y + delta

        # Calculate max y we can reach with current x
        max_possible_y = (self.file_size - 1 - current_x) // self.bytes_per_line

        # Clamp the movement
        new_y = max(0, min(new_y, max_possible_y))

        # If no movement possible, return
        if new_y == current_y:
            return

        new_position = new_y * self.bytes_per_line + current_x
        self._set_position(new_position)

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
        if self.file_size > 0:
            self._set_position(self.file_size - 1)

    def _set_position(self, new_position: int) -> None:
        """Set cursor position and notify callbacks."""
        if new_position != self.position:
            self.position = new_position

            # Notify position change
            if self.on_position_changed:
                self.on_position_changed(self.position)

            # Request scroll if needed
            if self.on_scroll_request:
                cursor_line = self.position // self.bytes_per_line
                self.on_scroll_request(cursor_line)

    def on_focus(self) -> None:
        """Called when cursor gains focus."""
        self.is_active = True

    def on_blur(self) -> None:
        """Called when cursor loses focus."""
        self.is_active = False

    def set_file_size(self, file_size: int) -> None:
        """Update file size and clamp position if needed."""
        self.file_size = file_size
        if self.position >= file_size:
            self._set_position(max(0, file_size - 1))

    def set_view_height(self, view_height: int) -> None:
        """Update the view height for page up/down calculations."""
        self.view_height = view_height
