"""Main application for texxd hex editor."""

import argparse
import sys
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from . import __version__
from .data import Buffer
from .dialogs import ConfirmModal
from .log import setup_logging
from .node import Node
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
        self.file_path = file_path
        if file_path and file_path.exists():
            self.node = Node.open(file_path)
        else:
            # new file: nothing on disk until it's saved
            self.node = Node(file_path.name if file_path else "untitled", Buffer())

    def compose(self) -> ComposeResult:
        yield HexView(self.node)
        yield StatusBar()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.node.name
        self.query_one(HexView).focus()

    def on_location_changed(self, message: LocationChanged) -> None:
        self.query_one(StatusBar).update(self.query_one(HexView).status)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # leave dialogs' own keys alone
        if action in ("save", "undo", "redo") and isinstance(self.screen, ModalScreen):
            return False
        return True

    @property
    def buffer(self) -> Buffer:
        return self.node.buffer

    def action_quit(self) -> None:
        if self.buffer.modified:

            def answer(quit: bool | None) -> None:
                if quit:
                    self.exit()

            self.push_screen(ConfirmModal("There are unsaved changes. Quit anyway?", yes="Quit"), answer)
        else:
            self.exit()

    def action_save(self) -> None:
        if self.file_path is None:
            self.notify("No file name to save to (start texxd with one)", severity="warning")
            return
        try:
            self.buffer.save(self.file_path)
        except OSError as e:
            self.notify(f"Save failed: {e}", severity="error")
            return
        self.notify(f"Saved {self.file_path}")

    def _after_history(self, change) -> None:
        """Move the cursor to where an undo or redo happened."""
        if change is None:
            self.notify("Nothing to do", severity="information")
            return
        view = self.query_one(HexView)
        offset = view.node.local_offset(change.offset)
        if offset is not None:
            view.go_to(offset)

    def action_undo(self) -> None:
        self._after_history(self.buffer.undo())

    def action_redo(self) -> None:
        self._after_history(self.buffer.redo())


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
