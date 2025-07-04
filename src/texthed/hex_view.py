"""Hex view widget for displaying binary data."""

from io import IOBase
from typing import Optional

from textual.reactive import reactive
from textual.widgets import Static


def byte_to_ascii(byte: int) -> str:
    """Convert a byte to its ASCII representation."""
    if 32 <= byte <= 127:
        return chr(byte)
    return "."


class HexView(Static):
    """A widget that displays binary data in hex format."""

    data: reactive[bytes] = reactive(b"", layout=True)
    offset: reactive[int] = reactive(0)

    def __init__(self, file: Optional[IOBase] = None) -> None:
        super().__init__()
        self._file = file
        self._height = 0
        self.can_focus = True
        # Set height to fill parent
        self.styles.height = "100%"
        # Prevent shrinking
        self.shrink = False

    def on_mount(self) -> None:
        """Handle mount event."""
        # Size might not be available immediately on mount
        self.call_after_refresh(self._update_on_mount)

    def _update_on_mount(self) -> None:
        """Update display after mount when size is available."""
        self._height = self.size.height
        if self._file:
            self._read_data()

    def on_resize(self) -> None:
        """Handle resize events to update data display."""
        self._height = self.size.height
        if self._file:
            self._read_data()

    def watch_offset(self, new_offset: int) -> None:
        """Handle offset changes."""
        # Round offset to 16-byte boundary
        self.offset = (new_offset // 16) * 16
        if self._file:
            self._read_data()

    def watch_data(self, data: bytes) -> None:
        """Handle data changes and update display."""
        self._render_hex()

    def set_file(self, file: IOBase) -> None:
        """Set the file to read from."""
        self._file = file
        # Get current height if not already set
        if self._height == 0:
            self._height = self.size.height
        self._read_data()

    def _read_data(self) -> None:
        """Read data from file based on current offset and height."""
        if not self._file or self._height == 0:
            return

        bytes_to_read = 16 * self._height
        self._file.seek(self.offset)
        self.data = self._file.read(bytes_to_read)

    def _get_row_width(self) -> int:
        """Calculate the width of a full row in characters."""
        # Format: "XXXXXXXX: XX XX XX XX XX XX XX XX  XX XX XX XX XX XX XX XX  AAAAAAAAAAAAAAAA"
        # offset(8) + colon(1) + space(1) + first_8_hex(23) + double_space(2) + next_8_hex(23) + double_space(2) + ascii(16) = 76
        return 76

    def _render_hex(self) -> None:
        """Render the hex view."""
        lines = []

        for i in range(0, len(self.data), 16):
            chunk = self.data[i : i + 16]
            offset = self.offset + i

            # Format offset
            hex_offset = f"{offset:08x}:"

            # Format hex bytes
            hex_bytes = []
            ascii_chars = []

            for j, byte in enumerate(chunk):
                hex_bytes.append(f"{byte:02x}")
                ascii_chars.append(byte_to_ascii(byte))

                # Add extra space after 8 bytes
                if j == 7 and len(chunk) > 8:
                    hex_bytes.append("")

            # Pad if less than 16 bytes
            while len(hex_bytes) < 17:  # 17 because of the extra space at position 8
                if len(hex_bytes) == 8:
                    hex_bytes.append("")
                else:
                    hex_bytes.append("  ")

            hex_part = " ".join(hex_bytes)
            ascii_part = "".join(ascii_chars)

            lines.append(f"{hex_offset} {hex_part}  {ascii_part}")

        content = "\n".join(lines)
        self.update(content)

        # Set both min and actual width to prevent wrapping
        row_width = self._get_row_width()
        self.styles.min_width = row_width
        self.styles.width = row_width
