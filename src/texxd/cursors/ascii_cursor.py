"""ASCII cursor class for ASCII editing."""

from typing import Optional
from textual import events

from .cursor import Cursor
from ..log import get_logger

logger = get_logger(__name__)


class AsciiCursor(Cursor):
    """ASCII cursor that handles ASCII input and delegates writing to the file class."""

    def __init__(
        self,
        bytes_per_line: int = 16,
        parent_column=None,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
        disabled: bool = False,
    ):
        super().__init__(
            bytes_per_line=bytes_per_line,
            parent_column=parent_column,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )

    def handle_event(self, event: events.Event) -> bool:
        """Handle ASCII editing events."""
        if isinstance(event, events.Key):
            # Use the actual character instead of the key name
            char = event.character

            # Handle all printable ASCII input (space through tilde)
            if char and len(char) == 1 and 32 <= ord(char) <= 126:
                self._handle_ascii_input(char)
                return True

            # Handle delete/backspace by key name
            elif event.key == "delete":
                self._delete_byte()
                return True
            elif event.key == "backspace":
                self._backspace()
                return True

        # Fall back to parent navigation
        return super().handle_event(event)

    def _handle_ascii_input(self, char: str) -> None:
        """Handle ASCII character input."""
        byte_value = ord(char)
        self._write_byte(byte_value)
        self.move_x(1)

    def _write_byte(self, byte_value: int) -> None:
        """Write a byte through the file class."""
        file_obj = self.parent_column.hex_view._file
        current_pos = file_obj.tell()
        file_obj.seek(self.position)
        file_obj.write(bytes([byte_value]))
        file_obj.seek(current_pos)

    def _delete_byte(self) -> None:
        """Delete byte at current position."""
        # For now, just overwrite with 0x00
        self._write_byte(0x00)

    def _backspace(self) -> None:
        """Handle backspace - move back and delete."""
        self.move_x(-1)
        self._delete_byte()
