"""Hex editor widget for TextHed."""

from pathlib import Path
from typing import Optional

from textual.widgets import Static


class HexEditor(Static):
    """A hex editor widget."""

    def __init__(self) -> None:
        super().__init__()
        self.file_path: Optional[Path] = None

    def open(self, file_path: Path) -> None:
        """Open a file for hex editing."""
        self.file_path = file_path
        self.update(f"hello {file_path.name}")
