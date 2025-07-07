"""Hex editor widget."""

from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.containers import HorizontalScroll
from textual.widgets import Static

from .hex_view import HexView
from .hex_file import HexFile
from .log import get_logger

logger = get_logger(__name__)


class HexEditor(Static):
    """A hex editor widget."""

    def __init__(self) -> None:
        super().__init__()
        self.file_path: Optional[Path] = None
        self._file_handle = None
        # Make the hex editor expand to fill available space
        self.styles.width = "100%"
        self.styles.height = "100%"

    def compose(self) -> ComposeResult:
        """Compose the hex editor layout."""
        hex_view = HexView()
        with HorizontalScroll():
            yield hex_view

    def open(self, file_path: Path) -> None:
        """Open a file for hex editing."""
        self.file_path = file_path

        # Close previous file if open
        if self._file_handle:
            self._file_handle.close()

        # Open new file and wrap with HexFile
        raw_file = open(file_path, "r+b")
        self._file_handle = HexFile(raw_file)
        logger.debug(f"Opened file {file_path}, HexFile size: {self._file_handle.size}")

        hex_view = self.query_one(HexView)
        # Ensure the widget is ready before setting file
        self.call_after_refresh(lambda: hex_view.set_file(self._file_handle))
