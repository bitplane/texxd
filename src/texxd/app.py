"""Main application for texxd hex editor."""

import argparse
import sys
from pathlib import Path
from typing import Optional

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from . import __version__
from .data import Buffer, Change, EditError
from .dialogs import ConfirmModal
from .document import Document
from .log import setup_logging
from .view import HexView, LocationChanged


class StatusBar(Static):
    """Where the cursor is."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $panel;
        padding: 0 1;
    }
    """


class TexxdApp(App):
    """A hex editor application built with Textual."""

    TITLE = "texxd"
    # the hex view handles save/undo/redo keys itself too, so they stay in order with typing
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+z", "undo", "Undo"),
        Binding("ctrl+y", "redo", "Redo"),
    ]

    def __init__(self, file_path: Optional[Path] = None):
        super().__init__()
        self.document = Document.open(file_path)

    def compose(self) -> ComposeResult:
        yield HexView(self.document)
        yield StatusBar()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.document.name
        self.query_one(HexView).focus()

    def on_location_changed(self, message: LocationChanged) -> None:
        # names like "[json]" aren't markup
        self.query_one(StatusBar).update(escape(self.query_one(HexView).status))

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # leave dialogs' own keys alone
        if action in ("save", "undo", "redo") and isinstance(self.screen, ModalScreen):
            return False
        return True

    @property
    def buffer(self) -> Buffer:
        return self.document.buffer

    def action_quit(self) -> None:
        if self.document.modified:

            def answer(quit: bool | None) -> None:
                if quit:
                    self.exit()

            self.push_screen(ConfirmModal("There are unsaved changes. Quit anyway?", yes="Quit"), answer)
        else:
            self.exit()

    def action_save(self) -> None:
        if self.document.path is None:
            self.notify("No file name to save to (start texxd with one)", severity="warning")
            return
        try:
            self.document.save()
        except (OSError, EditError, NotImplementedError) as e:
            self.notify(f"Save failed: {e}", severity="error")
            return
        self.notify(f"Saved {self.document.path}")

    def _after_history(self, where: Optional[tuple[Buffer, Optional[Change]]]) -> None:
        """Move the cursor to where an undo or redo happened."""
        if where is None:
            self.notify("Nothing to do", severity="information")
            return
        buffer, change = where
        if change is not None:
            self.query_one(HexView).reveal(buffer, change.offset)

    def action_undo(self) -> None:
        self._after_history(self.document.undo())

    def action_redo(self) -> None:
        self._after_history(self.document.redo())


def main() -> None:
    """Main entry point for the application."""
    parser = argparse.ArgumentParser(description="texxd - A hex editor built with Textual")
    parser.add_argument("file", nargs="?", help="File to open (created on save if it doesn't exist)")
    parser.add_argument(
        "--version", "-v", action="version", version=f"texxd {__version__}", help="Show version and exit"
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Set logging level"
    )
    parser.add_argument("--log-file", type=Path, help="Log file path")

    args = parser.parse_args()
    setup_logging(args.log_level, args.log_file)

    file_path = Path(args.file) if args.file else None
    if file_path and file_path.exists():
        if file_path.is_dir():
            sys.exit(f"texxd: {file_path} is a directory")
        try:
            file_path.open("rb").close()
        except OSError as e:
            sys.exit(f"texxd: {e}")

    TexxdApp(file_path).run()


if __name__ == "__main__":
    main()
