"""Hex view widget for displaying binary data."""

from enum import Enum
from io import IOBase
from typing import Optional

from textual import events
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.geometry import Size
from textual.strip import Strip
from rich.segment import Segment
from rich.style import Style

# Constants
BYTES_PER_LINE = 16
HEX_VIEW_WIDTH = 76  # Full width of a hex line


class CursorMode(Enum):
    """Cursor modes for hex editing."""

    HEX = "hex"
    ASCII = "ascii"
    INACTIVE = "inactive"  # Widget not focused


def byte_to_ascii(byte: int) -> str:
    """Convert a byte to its ASCII representation."""
    if 32 <= byte <= 127:
        return chr(byte)
    return "."


class HexView(ScrollView):
    """A widget that displays binary data in hex format."""

    BINDINGS = [
        ("tab", "tab_mode", "Hex→ASCII→Tab Out"),
        ("shift+tab", "shift_tab_mode", "ASCII→Hex→Tab Back"),
        ("return", "toggle_cursor_mode", "Toggle Hex/ASCII"),
        ("left,right,up,down", "move_cursor", "Navigate"),
        ("home,end", "move_cursor_line", "Line Start/End"),
        ("ctrl+home,ctrl+end", "move_cursor_file", "File Start/End"),
        ("pageup,pagedown", "move_cursor_page", "Page Up/Down"),
    ]

    # Reactive properties for cursor
    cursor_offset: reactive[int] = reactive(0)
    cursor_mode: reactive[CursorMode] = reactive(CursorMode.INACTIVE)

    def __init__(self, file: Optional[IOBase] = None) -> None:
        super().__init__()
        self._file = file
        self._file_size = 0
        self.can_focus = True
        self.styles.height = "100%"

    def set_file(self, file: IOBase) -> None:
        """Set the file to read from."""
        self._file = file
        if self._file:
            # Get file size
            self._file.seek(0, 2)  # Seek to end
            self._file_size = self._file.tell()
            self._file.seek(0)

            # Set virtual size based on number of lines needed
            lines_needed = (self._file_size + BYTES_PER_LINE - 1) // BYTES_PER_LINE  # Round up
            self.virtual_size = Size(HEX_VIEW_WIDTH, lines_needed)
            self.refresh()

    def watch_cursor_offset(self, new_offset: int) -> None:
        """Handle cursor offset changes and auto-scroll."""
        if not self._file or self._file_size == 0:
            return

        # Clamp cursor to file bounds
        clamped_offset = max(0, min(new_offset, self._file_size - 1))

        # Avoid infinite recursion by checking if we're setting to the same value
        if clamped_offset == self.cursor_offset:
            return

        self.cursor_offset = clamped_offset

        # Calculate which line the cursor is on
        cursor_line = self.cursor_offset // BYTES_PER_LINE

        # Auto-scroll if cursor is off screen (but page up/down handle their own scrolling)
        visible_top = self.scroll_y
        visible_bottom = visible_top + self.size.height - 1

        if cursor_line < visible_top:
            self.scroll_to(y=cursor_line)
        elif cursor_line > visible_bottom:
            self.scroll_to(y=cursor_line - self.size.height + 1)

        self.refresh()

    def watch_cursor_mode(self, new_mode: CursorMode) -> None:
        """Handle cursor mode changes."""
        self.refresh()

    def on_focus(self, event: events.Focus) -> None:
        """Handle focus gained - start in hex mode."""
        if self._file and self.cursor_mode == CursorMode.INACTIVE:
            self.cursor_mode = CursorMode.HEX

    def on_blur(self, event: events.Blur) -> None:
        """Handle focus lost - go to inactive mode."""
        self.cursor_mode = CursorMode.INACTIVE

    def on_key(self, event: events.Key) -> None:
        """Handle key presses for cursor navigation and mode switching."""
        if not self._file or self.cursor_mode == CursorMode.INACTIVE:
            return

        key = event.key

        if key == "tab":
            if self.cursor_mode == CursorMode.HEX:
                # Switch to ASCII mode
                self.cursor_mode = CursorMode.ASCII
                event.prevent_default()
                event.stop()
            else:
                # Let tab continue to next widget
                pass
        elif key == "shift+tab":
            if self.cursor_mode == CursorMode.ASCII:
                # Switch back to hex mode
                self.cursor_mode = CursorMode.HEX
                event.prevent_default()
                event.stop()
            else:
                # Let shift+tab continue to previous widget
                pass
        elif key == "return":
            # Toggle between hex and ASCII modes
            self.cursor_mode = CursorMode.ASCII if self.cursor_mode == CursorMode.HEX else CursorMode.HEX
            event.prevent_default()
            event.stop()
        elif key == "left":
            self.cursor_offset = max(0, self.cursor_offset - 1)
            event.prevent_default()
            event.stop()
        elif key == "right":
            self.cursor_offset = min(self._file_size - 1, self.cursor_offset + 1)
            event.prevent_default()
            event.stop()
        elif key == "up":
            self.cursor_offset = max(0, self.cursor_offset - BYTES_PER_LINE)
            event.prevent_default()
            event.stop()
        elif key == "down":
            self.cursor_offset = min(self._file_size - 1, self.cursor_offset + BYTES_PER_LINE)
            event.prevent_default()
            event.stop()
        elif key == "home":
            # Move to start of current line
            line_start = (self.cursor_offset // BYTES_PER_LINE) * BYTES_PER_LINE
            self.cursor_offset = line_start
            event.prevent_default()
            event.stop()
        elif key == "end":
            # Move to end of current line
            line_start = (self.cursor_offset // BYTES_PER_LINE) * BYTES_PER_LINE
            line_end = min(line_start + BYTES_PER_LINE - 1, self._file_size - 1)
            self.cursor_offset = line_end
            event.prevent_default()
            event.stop()
        elif key == "ctrl+home":
            # Move to start of file
            self.cursor_offset = 0
            event.prevent_default()
            event.stop()
        elif key == "ctrl+end":
            # Move to end of file
            self.cursor_offset = self._file_size - 1
            event.prevent_default()
            event.stop()
        elif key == "pageup":
            # Scroll view up one page, then adjust cursor to maintain relative position
            lines_per_page = self.size.height
            current_cursor_line = self.cursor_offset // BYTES_PER_LINE
            current_view_top = self.scroll_y
            cursor_relative_to_view = current_cursor_line - current_view_top

            # Scroll up by one page
            new_view_top = max(0, current_view_top - lines_per_page)
            self.scroll_to(y=new_view_top)

            # Position cursor at same relative position in new view
            new_cursor_line = new_view_top + cursor_relative_to_view
            new_cursor_line = max(0, min(new_cursor_line, (self._file_size - 1) // BYTES_PER_LINE))
            self.cursor_offset = new_cursor_line * BYTES_PER_LINE + (self.cursor_offset % BYTES_PER_LINE)
            self.cursor_offset = max(0, min(self.cursor_offset, self._file_size - 1))

            event.prevent_default()
            event.stop()
        elif key == "pagedown":
            # Scroll view down one page, then adjust cursor to maintain relative position
            lines_per_page = self.size.height
            current_cursor_line = self.cursor_offset // BYTES_PER_LINE
            current_view_top = self.scroll_y
            cursor_relative_to_view = current_cursor_line - current_view_top

            # Scroll down by one page
            max_scroll = max(0, self.virtual_size.height - lines_per_page)
            new_view_top = min(max_scroll, current_view_top + lines_per_page)
            self.scroll_to(y=new_view_top)

            # Position cursor at same relative position in new view
            new_cursor_line = new_view_top + cursor_relative_to_view
            new_cursor_line = max(0, min(new_cursor_line, (self._file_size - 1) // BYTES_PER_LINE))
            self.cursor_offset = new_cursor_line * BYTES_PER_LINE + (self.cursor_offset % BYTES_PER_LINE)
            self.cursor_offset = max(0, min(self.cursor_offset, self._file_size - 1))

            event.prevent_default()
            event.stop()

    def render_line(self, y: int) -> Strip:
        """Render a line of the hex view."""
        # Get scroll offset and adjust y accordingly
        scroll_x, scroll_y = self.scroll_offset
        actual_line = y + scroll_y

        # Handle empty file or out of bounds
        if not self._file:
            line = f"No file loaded (y={y})"
            segment = Segment(line, Style())
            return Strip([segment])

        if actual_line >= self.virtual_size.height:
            return Strip.blank(self.size.width)

        # Calculate file offset for this line
        file_offset = actual_line * BYTES_PER_LINE

        # Check if offset is beyond file size
        if file_offset >= self._file_size:
            return Strip.blank(self.size.width)

        # Read data for this line
        chunk = self._read_chunk(file_offset)
        if not chunk:
            return Strip.blank(self.size.width)

        # Format the hex line with cursor highlighting
        segments = self._format_hex_line_with_cursor(file_offset, chunk, actual_line)

        # Create strip
        strip = Strip(segments)

        # Crop to widget width
        strip = strip.crop(0, self.size.width)
        return strip

    def _read_chunk(self, file_offset: int) -> bytes:
        """Read a chunk of bytes from the file at the given offset."""
        try:
            self._file.seek(file_offset)
            return self._file.read(BYTES_PER_LINE)
        except Exception:
            return b""

    def _format_hex_line_with_cursor(self, file_offset: int, chunk: bytes, line_number: int) -> list[Segment]:
        """Format a line of hex data with cursor highlighting."""
        segments = []

        # Calculate cursor position on this line (if any)
        cursor_line = self.cursor_offset // BYTES_PER_LINE
        cursor_on_this_line = cursor_line == line_number
        cursor_byte_index = self.cursor_offset % BYTES_PER_LINE if cursor_on_this_line else -1

        # Format offset
        hex_offset = f"{file_offset:08x}:"
        segments.append(Segment(hex_offset, Style()))
        segments.append(Segment(" ", Style()))

        # Format hex bytes with highlighting
        for j, byte in enumerate(chunk):
            if j == 8:
                segments.append(Segment(" ", Style()))  # Extra space after 8 bytes

            byte_str = f"{byte:02x}"

            if cursor_on_this_line and j == cursor_byte_index:
                # Highlight for cursor position
                if self.cursor_mode == CursorMode.INACTIVE:
                    style = Style(bgcolor="grey30", color="grey70")  # Grey when inactive
                elif self.cursor_mode == CursorMode.HEX:
                    style = Style(bgcolor="bright_white", color="black")  # Bright when active
                else:
                    style = Style(bgcolor="white", color="black")  # Dim when other mode active
            else:
                style = Style()

            segments.append(Segment(byte_str, style))
            if j < len(chunk) - 1:
                segments.append(Segment(" ", Style()))

        # Pad hex section if less than 16 bytes
        for j in range(len(chunk), BYTES_PER_LINE):
            if j == 8:
                segments.append(Segment(" ", Style()))
            segments.append(Segment("  ", Style()))
            if j < BYTES_PER_LINE - 1:
                segments.append(Segment(" ", Style()))

        # Double space before ASCII
        segments.append(Segment("  ", Style()))

        # Format ASCII with highlighting
        for j, byte in enumerate(chunk):
            ascii_char = byte_to_ascii(byte)

            if cursor_on_this_line and j == cursor_byte_index:
                # Highlight for cursor position
                if self.cursor_mode == CursorMode.INACTIVE:
                    style = Style(bgcolor="grey30", color="grey70")  # Grey when inactive
                elif self.cursor_mode == CursorMode.ASCII:
                    style = Style(bgcolor="bright_white", color="black")  # Bright when active
                else:
                    style = Style(bgcolor="white", color="black")  # Dim when other mode active
            else:
                style = Style()

            segments.append(Segment(ascii_char, style))

        return segments
