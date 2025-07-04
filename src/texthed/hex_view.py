"""Hex view widget for displaying binary data."""

from io import IOBase
from typing import Optional

from textual.scroll_view import ScrollView
from textual.geometry import Size
from textual.strip import Strip
from rich.segment import Segment
from rich.style import Style


def byte_to_ascii(byte: int) -> str:
    """Convert a byte to its ASCII representation."""
    if 32 <= byte <= 127:
        return chr(byte)
    return "."


class HexView(ScrollView):
    """A widget that displays binary data in hex format."""

    def __init__(self, file: Optional[IOBase] = None) -> None:
        super().__init__()
        self._file = file
        self._file_size = 0
        self.can_focus = True
        # Set height to fill parent
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
            lines_needed = (self._file_size + 15) // 16  # Round up
            self.virtual_size = Size(self._get_row_width(), lines_needed)

            # Debug info
            self.log(f"File size: {self._file_size}, Lines needed: {lines_needed}, Virtual size: {self.virtual_size}")

            self.refresh()

    def _get_row_width(self) -> int:
        """Calculate the width of a full row in characters."""
        # Format: "XXXXXXXX: XX XX XX XX XX XX XX XX  XX XX XX XX XX XX XX XX  AAAAAAAAAAAAAAAA"
        # offset(8) + colon(1) + space(1) + first_8_hex(23) + double_space(2) + next_8_hex(23) + double_space(2) + ascii(16) = 76
        return 76

    def render_line(self, y: int) -> Strip:
        """Render a line of the hex view."""
        # Debug: log render_line calls for first few lines
        if y < 5:
            self.log(
                f"render_line called with y={y}, file={self._file is not None}, virtual_height={self.virtual_size.height if hasattr(self, 'virtual_size') else 'unset'}"
            )

        # Handle empty file or out of bounds
        if not self._file:
            line = f"No file loaded (y={y})"
            segment = Segment(line, Style())
            return Strip([segment])

        if y >= self.virtual_size.height:
            return Strip.blank(self.size.width)

        # Calculate file offset for this line
        file_offset = y * 16

        # Check if offset is beyond file size
        if file_offset >= self._file_size:
            return Strip.blank(self.size.width)

        try:
            # Read 16 bytes for this line
            self._file.seek(file_offset)
            chunk = self._file.read(16)

            if not chunk:
                return Strip.blank(self.size.width)
        except Exception as e:
            line = f"Error reading file: {e}"
            segment = Segment(line, Style())
            return Strip([segment])

        # Format offset
        hex_offset = f"{file_offset:08x}:"

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

        # Build the complete line
        line = f"{hex_offset} {hex_part}  {ascii_part}"

        # Create segment and strip
        segment = Segment(line, Style())
        strip = Strip([segment])

        # Crop to widget width
        strip = strip.crop(0, self.size.width)

        return strip
