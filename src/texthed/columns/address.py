"""Address column for displaying file offsets."""

from typing import List
from rich.segment import Segment
from rich.style import Style

from .column import DataColumn


class AddressColumn(DataColumn):
    """Column that displays file offset addresses."""

    def __init__(self, file_size: int = 0):
        super().__init__(cursor=None)  # Address column doesn't need a cursor
        self.file_size = file_size

    @property
    def width(self) -> int:
        """Address column width: hex digits for file size + colon + space."""
        if self.file_size == 0:
            return 10  # Default: 8 hex digits + colon + space

        # Calculate number of hex digits needed for file size
        hex_digits = max(4, len(f"{self.file_size:x}"))
        return hex_digits + 2  # + colon + space

    def render_line(self, data: bytes, file_offset: int, line_number: int) -> List[Segment]:
        """Render the address for this line."""
        if self.file_size == 0:
            address_text = f"{file_offset:08x}: "
        else:
            hex_digits = max(4, len(f"{self.file_size:x}"))
            address_text = f"{file_offset:0{hex_digits}x}: "
        return [Segment(address_text, Style())]

    def set_file_size(self, file_size: int) -> None:
        """Update the file size for address formatting."""
        self.file_size = file_size
