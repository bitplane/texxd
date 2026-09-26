"""Highlighters style the bytes on a line.

Each takes the line's bytes, their offset and a list of styles (one per byte,
None for unstyled) and modifies the styles in place. They run in order, so
later ones win where they set the same attributes.
"""

from typing import List, Optional

from rich.style import Style

from ..data import Data

Styles = List[Optional[Style]]


def combine(existing: Optional[Style], new: Style) -> Style:
    """Layer ``new`` on top of ``existing``."""
    return new if existing is None else existing + new


class Highlighter:
    """Base highlighter: does nothing."""

    def highlight(self, data: bytes, offset: int, styles: Styles) -> None:
        """Apply highlighting to ``styles`` for ``data`` found at ``offset``."""


class Highlights(dict, Highlighter):
    """A named, ordered stack of highlighters that runs them all in insertion order."""

    def highlight(self, data: bytes, offset: int, styles: Styles) -> None:
        for highlighter in self.values():
            highlighter.highlight(data, offset, styles)


class DataHighlighter(Highlighter):
    """Colours bytes by kind: null, whitespace, control/high bytes."""

    null_style = Style(color="bright_black", bold=True)
    space_style = Style(color="cyan")
    control_style = Style(color="bright_cyan", bold=True)

    def highlight(self, data: bytes, offset: int, styles: Styles) -> None:
        for i, byte in enumerate(data):
            if byte == 0x00:
                style = self.null_style
            elif byte == 0x20:
                style = self.space_style
            elif byte < 0x20 or byte > 0x7E:
                style = self.control_style
            else:
                continue
            styles[i] = combine(styles[i], style)


class EditHighlighter(Highlighter):
    """Marks bytes with unsaved changes."""

    style = Style(color="bright_red", bold=True)

    def __init__(self, data: Data):
        self.data = data

    def highlight(self, data: bytes, offset: int, styles: Styles) -> None:
        for start, stop in self.data.edits(offset, offset + len(data)):
            for i in range(start - offset, stop - offset):
                styles[i] = combine(styles[i], self.style)


class RangeHighlighter(Highlighter):
    """Styles whatever (start, stop, style) ranges it's given, like a selection."""

    def __init__(self) -> None:
        self.ranges: list[tuple[int, int, Style]] = []

    def highlight(self, data: bytes, offset: int, styles: Styles) -> None:
        end = offset + len(data)
        for start, stop, style in self.ranges:
            for i in range(max(start, offset), min(stop, end)):
                styles[i - offset] = combine(styles[i - offset], style)
