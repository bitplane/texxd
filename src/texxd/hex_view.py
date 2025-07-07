"""Hex view widget using Textual widget-based columns."""

from textual.app import ComposeResult
from textual.scroll_view import ScrollView
from textual.reactive import reactive
from textual.geometry import Size
from textual import events
from textual.strip import Strip
from rich.segment import Segment

from .columns import AddressColumn, HexColumn, AsciiColumn, Column
from .highlighters.data import DataHighlighter
from .cursors.cursor import CursorMoved, ScrollRequest
from .log import get_logger

logger = get_logger(__name__)


class HexView(ScrollView):
    """A widget that displays binary data using widget-based columns."""

    cursor_position = reactive(0)
    file_size = reactive(0)

    DEFAULT_BYTES_PER_LINE = 16

    CSS = """
    Column {
        border: solid red;
    }
    """

    def __init__(self, file=None) -> None:
        super().__init__()
        self._file = file
        self.can_focus = True
        self.styles.height = "100%"
        self._columns: list[Column] = []
        self._address_column: AddressColumn | None = None
        self._hex_column: HexColumn | None = None
        self._ascii_column: AsciiColumn | None = None
        self._focused_column: Column | None = None

    def compose(self) -> ComposeResult:
        """Compose the hex view layout."""
        self._address_column = AddressColumn(bytes_per_line=self.DEFAULT_BYTES_PER_LINE, hex_view=self)
        self._hex_column = HexColumn(bytes_per_line=self.DEFAULT_BYTES_PER_LINE, hex_view=self)
        self._ascii_column = AsciiColumn(bytes_per_line=self.DEFAULT_BYTES_PER_LINE, hex_view=self)
        self._columns = [self._address_column, self._hex_column, self._ascii_column]
        # Don't use Horizontal container - we'll handle the layout in render_line
        return []

    def on_mount(self) -> None:
        """Handle mount event."""
        # Setup data access for columns
        self._setup_data_access()

        # Add highlighters to data columns
        self._hex_column.add_highlighter("data", DataHighlighter())
        self._ascii_column.add_highlighter("data", DataHighlighter())

        # Set hex view reference for cursor size access
        self._hex_column.cursor.hex_view = self
        self._ascii_column.cursor.hex_view = self

        # Add cursors as highlighters (last to maintain priority)
        self._hex_column.add_highlighter("cursor", self._hex_column.cursor)
        self._ascii_column.add_highlighter("cursor", self._ascii_column.cursor)

        # Set initial focused column
        self._set_active_column(self._hex_column)

        # Ensure HexView has focus to receive key events
        self.focus()

    def _setup_data_access(self) -> None:
        """Setup data access method for all columns."""

        def get_line_data(file_offset: int) -> bytes:
            return self._read_chunk(file_offset)

        # Inject data access method into all columns
        for column in self._columns:
            column._get_line_data = get_line_data

    def _set_active_column(self, column) -> None:
        """Set the active/focused column."""
        if column == self._focused_column:
            return

        # Deactivate current column's cursor
        if self._focused_column and hasattr(self._focused_column, "cursor"):
            self._focused_column.cursor.is_active = False

        # Activate new column's cursor
        self._focused_column = column
        if hasattr(column, "cursor"):
            column.cursor.is_active = True

    def _tab(self, back=False) -> bool:
        """Tab to next/previous focusable column."""
        focusable = [col for col in self._columns if hasattr(col, "cursor")]
        if not focusable or not self._focused_column:
            return False

        try:
            current_idx = focusable.index(self._focused_column)
            delta = -1 if back else 1
            new_idx = (current_idx + delta) % len(focusable)

            self._set_active_column(focusable[new_idx])
            return True
        except ValueError:
            return False

    def set_file(self, file) -> None:
        """Set the file to read from."""
        self._file = file
        if self._file:
            self.file_size = self._file.size

            # Set virtual size based on number of lines needed
            lines_needed = (self.file_size + self.DEFAULT_BYTES_PER_LINE - 1) // self.DEFAULT_BYTES_PER_LINE

            # Calculate total width from all columns
            total_width = 0
            for column in self._columns:
                column.file_size = self.file_size
                total_width += column.get_content_width()

            # Add spaces between columns
            total_width += len(self._columns) - 1

            # Add file as highlighter if it supports highlighting (before cursor due to insertion order)
            if hasattr(file, "highlight"):
                self._hex_column.add_highlighter("file", file)
                self._ascii_column.add_highlighter("file", file)

            self.virtual_size = Size(total_width, lines_needed)
            self.refresh()
            self.scroll_to(y=0)
            logger.debug(
                f"set_file: file_size={self.file_size}, lines_needed={lines_needed}, virtual_size.height={self.virtual_size.height}"
            )

    def _read_chunk(self, file_offset: int) -> bytes:
        """Read a chunk of bytes from the file at the given offset."""
        if not self._file:
            return b""

        try:
            self._file.seek(file_offset)
            data = self._file.read(self.DEFAULT_BYTES_PER_LINE)
            logger.debug(f"_read_chunk: file_offset={file_offset}, data_len={len(data)}")
            return data
        except Exception as e:
            logger.error(f"_read_chunk error: {e}")
            return b""

    def on_cursor_moved(self, message: CursorMoved) -> None:
        """Handle cursor movement messages from columns."""
        position = message.position
        self.cursor_position = position

        # Sync all columns
        for column in self._columns:
            if isinstance(column, (HexColumn, AsciiColumn)) and column.cursor and column.cursor.position != position:
                column.cursor_position = position

        # Handle scrolling to keep cursor visible
        self._scroll_to_cursor(position)

    def on_scroll_request(self, message: ScrollRequest) -> None:
        """Handle scroll request messages from columns."""
        self._scroll_to_cursor(message.line * self.DEFAULT_BYTES_PER_LINE)

    def _handle_cursor_move(self, position: int) -> None:
        """Handle cursor movement - sync between columns and scroll if needed."""
        # Sync cursor position between columns
        for column in self._columns:
            if hasattr(column, "cursor") and column.cursor.position != position:
                column.cursor.position = position

        # Update reactive cursor position
        self.cursor_position = position

        # Handle scrolling
        self._scroll_to_cursor(position)

        # Refresh to show cursor update
        self.refresh()

    def _scroll_to_cursor(self, position: int) -> None:
        """Scroll to keep cursor position visible."""
        if not hasattr(self, "size") or self.size.height <= 0:
            return

        cursor_line = position // self.DEFAULT_BYTES_PER_LINE

        # Ensure cursor y+1 is always visible (eliminates edge cases)
        visible_top = self.scroll_y
        visible_bottom = visible_top + self.size.height - 1

        if cursor_line < visible_top:
            # Cursor went above view - scroll to show it at top
            self.scroll_to(y=cursor_line, animate=False)
        elif cursor_line + 1 > visible_bottom:
            # Cursor line + 1 is not visible - scroll to show cursor y+1
            new_scroll = cursor_line + 1 - self.size.height + 1
            # Don't scroll past the end
            max_scroll = max(0, self.virtual_size.height - self.size.height)
            new_scroll = min(max(0, new_scroll), max_scroll)
            self.scroll_to(y=new_scroll, animate=False)

    def on_key(self, event: events.Key) -> None:
        """Handle key events."""
        logger.debug(f"HexView received key: {event.key}, focused: {self.has_focus}")
        # Since columns are not mounted widgets, we need to manually handle focus
        handled = False

        # Pass key events to the currently focused column
        if self._focused_column and hasattr(self._focused_column, "on_key"):
            logger.debug(f"Passing key to focused column: {self._focused_column.__class__.__name__}")
            old_position = self._focused_column.cursor.position
            handled = self._focused_column.on_key(event)

            # If cursor moved, manually sync and handle scrolling
            if self._focused_column.cursor.position != old_position:
                logger.debug(f"Cursor moved from {old_position} to {self._focused_column.cursor.position}")
                self._handle_cursor_move(self._focused_column.cursor.position)
                handled = True

        # Tab/Shift+Tab to switch columns
        if not handled and event.key in ("tab", "shift+tab"):
            handled = self._tab(back=event.key == "shift+tab")
            if handled:
                self.refresh()

        # Prevent default key handling only if the event was handled
        if handled:
            event.stop()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Handle mouse click events."""
        # Calculate which column was clicked
        x = event.x + int(self.scroll_x)
        y = event.y + int(self.scroll_y)

        # Find which column was clicked
        current_x = 0
        for column in self._columns:
            column_width = column.get_content_width()
            if x < current_x + column_width:
                if hasattr(column, "cursor"):
                    # Switch focus if needed
                    self._set_active_column(column)

                    # Get byte position directly from column
                    column_x = x - current_x
                    byte_position = column.get_byte_position(column_x, y)

                    if byte_position is not None:
                        old_position = column.cursor.position
                        column.cursor._set_position(byte_position)

                        # If cursor moved, handle the movement
                        if column.cursor.position != old_position:
                            self._handle_cursor_move(column.cursor.position)
                        self.refresh()
                break
            current_x += column_width + 1  # +1 for space between columns

    def render_line(self, y: int) -> Strip:
        """Render a single line of the hex view."""
        # The y parameter is in viewport space, need to add scroll offset
        virtual_y = y + int(self.scroll_y)

        # Get rendered strips from each column
        rendered_columns = [column.render_line(virtual_y) for column in self._columns]

        # Combine strips with spaces
        combined_segments = []
        for i, strip in enumerate(rendered_columns):
            for segment in strip:
                combined_segments.append(segment)
            if i < len(rendered_columns) - 1:
                combined_segments.append(Segment(" "))

        # Create the full strip
        full_strip = Strip(combined_segments)

        # Crop for horizontal scrolling
        scroll_x = int(self.scroll_x)
        viewport_width = self.size.width
        cropped_strip = full_strip.crop(scroll_x, scroll_x + viewport_width)

        return cropped_strip
