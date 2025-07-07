"""Highlighter interface for styling bytes in hex editor."""

from typing import List, Optional
from rich.style import Style


class Highlighter:
    """
    A highlighter takes a block of bytes with their file offset and current styles,
    then modifies the styles array to apply highlighting effects.
    """

    def highlight(self, data: bytes, file_offset: int, styles: List[Optional[Style]]) -> None:
        """Apply highlighting to the styles array.

        Args:
            data: The bytes to be highlighted
            file_offset: Starting offset of the data in the file
            styles: List of current styles (same length as data).
                   Each element can be None or an existing Style.
                   Highlighters should modify this list in-place.
        """
        pass
