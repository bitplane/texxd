"""Newline highlighter for 0x0a (LF) and 0x0d (CR) bytes."""

from typing import List, Optional
from rich.style import Style


class NewlineHighlighter:
    """Highlights newline characters (0x0a and 0x0d) like xxd."""

    def __init__(self):
        # Style for newline characters - make them stand out
        self.newline_style = Style(color="bright_cyan", bold=True)

    def highlight(self, data: bytes, file_offset: int, styles: List[Optional[Style]]) -> None:
        """Apply highlighting to newline bytes."""
        for i, byte in enumerate(data):
            if byte in (0x0A, 0x0D):  # LF or CR
                if i < len(styles):
                    styles[i] = self._combine_styles(styles[i], self.newline_style)

    def _combine_styles(self, existing: Optional[Style], new: Style) -> Style:
        """Combine an existing style with a new style."""
        if existing is None:
            return new
        return existing + new
