"""Hex view widget for displaying binary data using column system."""

from typing import List
from textual import events
from textual.scroll_view import ScrollView
from textual.geometry import Size
from textual.strip import Strip
from rich.segment import Segment

from .columns import DataColumn, AddressColumn, HexColumn, AsciiColumn
from .cursors import Cursor
from .highlighters import NewlineHighlighter

# Constants
BYTES_PER_LINE = 16


class HexView(ScrollView):
    """A widget that displays binary data using a column-based system."""

    BINDINGS = [
        ("tab", "next_column", "Next Column"),
        ("shift+tab", "prev_column", "Previous Column"),
        ("left,right,up,down", "handle_navigation", "Navigate"),
        ("home,end", "handle_navigation", "Line Start/End"),
        ("ctrl+home,ctrl+end", "handle_navigation", "File Start/End"),
        ("pageup,pagedown", "handle_navigation", "Page Up/Down"),
    ]

    def __init__(self, file=None) -> None:
        super().__init__()
        self._file = file
        self._file_size = 0
        self.can_focus = True
        self.styles.height = "100%"

        # Initialize columns
        self.columns: List[DataColumn] = []
        self.current_column_index = 0
        self._setup_columns()

        # Calculate total width
        self._calculate_width()

    def _setup_columns(self) -> None:
        """Setup the standard hex editor columns."""
        # Create cursors that will be shared (they track the same position)
        hex_cursor = Cursor(
            file_size=self._file_size,
            bytes_per_line=BYTES_PER_LINE,
            on_position_changed=self._on_cursor_position_changed,
            on_scroll_request=self._on_scroll_request,
        )

        ascii_cursor = Cursor(
            file_size=self._file_size,
            bytes_per_line=BYTES_PER_LINE,
            on_position_changed=self._on_cursor_position_changed,
            on_scroll_request=self._on_scroll_request,
        )

        # Create columns
        self.columns = [
            AddressColumn(),
            HexColumn(bytes_per_line=BYTES_PER_LINE, cursor=hex_cursor),
            AsciiColumn(bytes_per_line=BYTES_PER_LINE, cursor=ascii_cursor),
        ]

        # Add highlighters to data columns (not address)
        newline_highlighter = NewlineHighlighter()
        self.columns[1].add_highlighter(newline_highlighter)  # Hex column
        self.columns[2].add_highlighter(newline_highlighter)  # ASCII column

        # Add cursors as highlighters to their respective columns
        self.columns[1].add_highlighter(hex_cursor)  # Hex column
        self.columns[2].add_highlighter(ascii_cursor)  # ASCII column

        # Focus first interactive column
        for i, column in enumerate(self.columns):
            if column.cursor:
                self.current_column_index = i
                column.focus()
                break

    def _calculate_width(self) -> None:
        """Calculate total display width from all columns."""
        total_width = sum(col.width for col in self.columns)
        # Add spaces between columns
        total_width += len(self.columns) - 1
        self.virtual_size = Size(total_width, self.virtual_size.height if hasattr(self, "virtual_size") else 0)

    def set_file(self, file) -> None:
        """Set the file to read from."""
        self._file = file
        if self._file:
            # Get file size
            self._file_size = self._file.size

            # Set virtual size based on number of lines needed
            lines_needed = (self._file_size + BYTES_PER_LINE - 1) // BYTES_PER_LINE
            self.virtual_size = Size(self.virtual_size.width, lines_needed)

            # Update cursor file sizes
            for column in self.columns:
                if column.cursor:
                    column.cursor.set_file_size(self._file_size)

            self.refresh()

    def _on_cursor_position_changed(self, position: int) -> None:
        """Handle cursor position changes - sync all cursors."""
        for column in self.columns:
            if column.cursor:
                column.cursor.position = position
        self.refresh()

    def _on_scroll_request(self, line: int) -> None:
        """Handle scroll requests from cursors."""
        if hasattr(self, "size") and self.size.height > 0:
            visible_top = self.scroll_y
            visible_bottom = visible_top + self.size.height - 1

            if line < visible_top:
                self.scroll_to(y=line)
            elif line > visible_bottom:
                self.scroll_to(y=line - self.size.height + 1)

    def on_focus(self, event: events.Focus) -> None:
        """Handle focus gained."""
        if self.columns and self.current_column_index < len(self.columns):
            self.columns[self.current_column_index].focus()

    def on_blur(self, event: events.Blur) -> None:
        """Handle focus lost."""
        for column in self.columns:
            column.blur()

    def action_next_column(self) -> None:
        """Move to next interactive column."""
        if not self.columns:
            return

        # Find next column with cursor
        start_index = self.current_column_index
        for i in range(len(self.columns)):
            next_index = (start_index + 1 + i) % len(self.columns)
            if self.columns[next_index].cursor:
                self._switch_to_column(next_index)
                return

    def action_prev_column(self) -> None:
        """Move to previous interactive column."""
        if not self.columns:
            return

        # Find previous column with cursor
        start_index = self.current_column_index
        for i in range(len(self.columns)):
            prev_index = (start_index - 1 - i) % len(self.columns)
            if self.columns[prev_index].cursor:
                self._switch_to_column(prev_index)
                return

    def _switch_to_column(self, column_index: int) -> None:
        """Switch focus to specified column."""
        if 0 <= column_index < len(self.columns):
            # Blur current column
            if self.current_column_index < len(self.columns):
                self.columns[self.current_column_index].blur()

            # Focus new column
            self.current_column_index = column_index
            self.columns[column_index].focus()
            self.refresh()

    def action_handle_navigation(self, event: events.Key) -> None:
        """Handle navigation events by forwarding to current column."""
        if self.current_column_index < len(self.columns) and self.columns[self.current_column_index].cursor:
            self.columns[self.current_column_index].handle_event(event)

    def on_key(self, event: events.Key) -> None:
        """Handle key events by forwarding to current column."""
        if self.current_column_index < len(self.columns) and self.columns[self.current_column_index].handle_event(
            event
        ):
            event.prevent_default()
            event.stop()

    def render_line(self, y: int) -> Strip:
        """Render a line using the column system."""
        # Get scroll offset and adjust y accordingly
        scroll_x, scroll_y = self.scroll_offset
        actual_line = y + scroll_y

        # Handle empty file or out of bounds
        if not self._file:
            segment = Segment("No file loaded", None)
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

        # Render each column
        all_segments = []
        for i, column in enumerate(self.columns):
            if i > 0:
                # Add space between columns
                all_segments.append(Segment(" ", None))

            column_segments = column.render_line(chunk, file_offset, actual_line)
            all_segments.extend(column_segments)

        # Create and crop strip
        strip = Strip(all_segments)
        return strip.crop(0, self.size.width)

    def _read_chunk(self, file_offset: int) -> bytes:
        """Read a chunk of bytes from the file at the given offset."""
        try:
            self._file.seek(file_offset)
            return self._file.read(BYTES_PER_LINE)
        except Exception:
            return b""
