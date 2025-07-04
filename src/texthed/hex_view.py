"""Hex view widget for displaying binary data."""

from io import IOBase
from typing import Optional

from textual.scroll_view import ScrollView
from textual.geometry import Size
from textual.strip import Strip
from rich.segment import Segment
from rich.style import Style

# Constants
BYTES_PER_LINE = 16
HEX_VIEW_WIDTH = 76  # Full width of a hex line


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

        # Format the hex line
        line = self._format_hex_line(file_offset, chunk)

        # Create segment and strip
        segment = Segment(line, Style())
        strip = Strip([segment])

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

    def _format_hex_line(self, file_offset: int, chunk: bytes) -> str:
        """Format a line of hex data with offset, hex bytes, and ASCII."""
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

        return f"{hex_offset} {hex_part}  {ascii_part}"
