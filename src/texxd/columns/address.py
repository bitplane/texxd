"""Address column widget for displaying file offsets."""

from textual.strip import Strip
from rich.segment import Segment

from .column import Column


class AddressColumn(Column):
    """Widget that displays file offset addresses."""

    def __init__(self, bytes_per_line: int = 16, hex_view=None):
        super().__init__(bytes_per_line=bytes_per_line, hex_view=hex_view)

    def get_content_width(self) -> int:
        """Calculate content width based on file size."""
        if self.file_size == 0:
            return 10  # Default: 8 hex digits + colon + space

        # Calculate number of hex digits needed for file size
        hex_digits = max(4, len(f"{self.file_size:x}"))
        return hex_digits + 2  # + colon + space

    def render_line(self, y: int) -> Strip:
        """Render a line of addresses."""
        # Calculate file offset for this line
        file_offset = int(y) * self.bytes_per_line

        # Format address
        if self.file_size == 0:
            address_text = f"{file_offset:08x}: "
        else:
            hex_digits = max(4, len(f"{self.file_size:x}"))
            address_text = f"{file_offset:0{hex_digits}x}: "

        segment = Segment(address_text)
        return Strip([segment])
