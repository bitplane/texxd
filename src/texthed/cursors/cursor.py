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
        self.is_active = False
        self.on_position_changed = on_position_changed
        self.on_scroll_request = on_scroll_request

        # Styles for different states
        self.active_style = Style(bgcolor="bright_white", color="black")
        self.inactive_style = Style(bgcolor="grey30", color="grey70")

    def highlight(self, data: bytes, file_offset: int, styles: List[Optional[Style]]) -> None:
        """Apply cursor highlighting to the styles array."""
        if not data:
            return

        # Check if cursor is in this data range
        data_start = file_offset
        data_end = file_offset + len(data)

        if self.position >= data_start and self.position < data_end:
            # Calculate index within this data chunk
            cursor_index = self.position - data_start

            # Apply cursor style
            cursor_style = self.active_style if self.is_active else self.inactive_style
            styles[cursor_index] = self._combine_styles(styles[cursor_index], cursor_style)

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
            self._move_cursor(-1)
        elif key == "right":
            self._move_cursor(1)
        elif key == "up":
            self._move_cursor(-self.bytes_per_line)
        elif key == "down":
            self._move_cursor(self.bytes_per_line)
        elif key == "home":
            self._move_to_line_start()
        elif key == "end":
            self._move_to_line_end()
        elif key == "ctrl+home":
            self._move_to_file_start()
        elif key == "ctrl+end":
            self._move_to_file_end()
        elif key == "pageup":
            self._page_up()
        elif key == "pagedown":
            self._page_down()
        else:
            handled = False

        return handled

    def _move_cursor(self, delta: int) -> None:
        """Move cursor by delta bytes, respecting file bounds."""
        new_position = self.position + delta
        self._set_position(max(0, min(new_position, self.file_size - 1)))

    def _move_to_line_start(self) -> None:
        """Move cursor to start of current line."""
        current_line = self.position // self.bytes_per_line
        line_start = current_line * self.bytes_per_line
        self._set_position(line_start)

    def _move_to_line_end(self) -> None:
        """Move cursor to end of current line."""
        current_line = self.position // self.bytes_per_line
        line_start = current_line * self.bytes_per_line
        line_end = min(line_start + self.bytes_per_line - 1, self.file_size - 1)
        self._set_position(line_end)

    def _move_to_file_start(self) -> None:
        """Move cursor to start of file."""
        self._set_position(0)

    def _move_to_file_end(self) -> None:
        """Move cursor to end of file."""
        self._set_position(self.file_size - 1)

    def _page_up(self) -> None:
        """Move cursor up one page."""
        # For now, just move up 10 lines (this would need view height info)
        self._move_cursor(-self.bytes_per_line * 10)

    def _page_down(self) -> None:
        """Move cursor down one page."""
        # For now, just move down 10 lines (this would need view height info)
        self._move_cursor(self.bytes_per_line * 10)

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
