"""Hex cursor class for hex editing."""

from typing import Optional
from textual import events

from .cursor import Cursor
from ..log import get_logger

logger = get_logger(__name__)


class HexCursor(Cursor):
    """Hex cursor that handles hex input and delegates writing to the file class."""

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
        self.pending_byte = None  # For partial hex input (single digit)

    def handle_event(self, event: events.Event) -> bool:
        """Handle hex editing events."""
        if isinstance(event, events.Key):
            key = event.key

            # Handle hex input
            if key in "0123456789abcdefABCDEF":
                self._handle_hex_input(key.lower())
                return True

            # Handle delete/backspace
            elif key == "delete":
                self._delete_byte()
                return True
            elif key == "backspace":
                self._backspace()
                return True

        # Fall back to parent navigation
        return super().handle_event(event)

    def _handle_hex_input(self, hex_char: str) -> None:
        """Handle hex character input."""
        if self.pending_byte is None:
            # First hex digit
            self.pending_byte = hex_char
        else:
            # Second hex digit - complete the byte
            hex_str = self.pending_byte + hex_char
            byte_value = int(hex_str, 16)
            self._write_byte(byte_value)
            self.pending_byte = None
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
        if self.pending_byte is not None:
            # Cancel pending input
            self.pending_byte = None
        else:
            # Move back and delete
            self.move_x(-1)
            self._delete_byte()
