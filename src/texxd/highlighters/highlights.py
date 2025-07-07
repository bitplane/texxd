"""Composite highlighter that manages multiple highlighters in order."""

from typing import List, Optional
from rich.style import Style

from .highlighter import Highlighter


class Highlights(dict, Highlighter):
    """A composite highlighter that manages multiple highlighters in insertion order.

    Acts as both a dict (for managing highlighters by name) and a Highlighter
    (for applying all highlighters in order).
    """

    def __init__(self):
        dict.__init__(self)
        Highlighter.__init__(self)

    def highlight(self, data: bytes, file_offset: int, styles: List[Optional[Style]]) -> None:
        """Apply all highlighters in insertion order."""
        if not data:
            return

        # Apply each highlighter in the order they were added
        for highlighter in self.values():
            highlighter.highlight(data, file_offset, styles)
