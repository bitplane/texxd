"""Address column for displaying file offsets."""

from typing import List
from rich.segment import Segment
from rich.style import Style

from .column import DataColumn


class AddressColumn(DataColumn):
    """Column that displays file offset addresses."""

    def __init__(self):
        super().__init__(cursor=None)  # Address column doesn't need a cursor

    @property
    def width(self) -> int:
        """Address column width: 8 hex digits + colon + space = 10 chars."""
        return 10

    def render_line(self, data: bytes, file_offset: int, line_number: int) -> List[Segment]:
        """Render the address for this line."""
        address_text = f"{file_offset:08x}: "
        return [Segment(address_text, Style())]
