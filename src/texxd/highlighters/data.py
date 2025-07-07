"""Data-based highlighter for different byte types and data formats."""

from typing import List, Optional
from rich.style import Style

from .highlighter import Highlighter


class DataHighlighter(Highlighter):
    """Highlights bytes based on data types and content."""

    def __init__(self):
        # Styles for different value types
        self.null_style = Style(color="bright_black", bold=True)  # Null bytes
        self.space_style = Style(color="cyan")  # Space character
        self.control_style = Style(color="bright_cyan", bold=True)  # Control codes
        self.printable_style = Style()  # Normal printable - no styling (default)

    def highlight(self, data: bytes, file_offset: int, styles: List[Optional[Style]]) -> None:
        """Apply highlighting based on byte values."""
        for i, byte in enumerate(data):
            if i >= len(styles):
                continue

            style = None

            if byte == 0x00:  # Null
                style = self.null_style
            elif byte == 0x20:  # Space
                style = self.space_style
            elif byte < 0x20 or byte > 0x7E:  # Control codes and non-ASCII
                style = self.control_style
            # Printable ASCII (0x21-0x7E) gets no styling

            if style:
                styles[i] = self._combine_styles(styles[i], style)

    def _combine_styles(self, existing: Optional[Style], new: Style) -> Style:
        """Combine an existing style with a new style."""
        if existing is None:
            return new
        return existing + new
