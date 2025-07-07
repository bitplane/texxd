"""Main application for texxd hex editor."""

import argparse
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer

from .hex_editor import HexEditor
from .hex_view import HexView
from .log import setup_logging


class TexxdApp(App):
    """A hex editor application built with Textual."""

    TITLE = "texxd"
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+s", "save", "Save"),
    ]

    def __init__(self, file_path: Optional[Path] = None):
        super().__init__()
        self.file_path = file_path

    def compose(self) -> ComposeResult:
        """Compose the application layout."""
        yield Header()
        yield HexEditor()
        yield Footer()

    def on_mount(self) -> None:
        """Handle mount event."""
        if self.file_path:
            self.query_one(HexEditor).open(self.file_path)

        # Focus the hex view widget so navigation works immediately
        self.call_after_refresh(lambda: self.query_one(HexView).focus())

    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

    def action_save(self) -> None:
        """Save the current file."""
        try:
            hex_editor = self.query_one(HexEditor)
            if hex_editor._file_handle:
                hex_editor._file_handle.flush()
                # Refresh the hex view to clear edit highlighting
                hex_view = self.query_one(HexView)
                hex_view.refresh()
                self.notify("File saved!")
            else:
                self.notify("No file open", severity="warning")
        except Exception as e:
            self.notify(f"Save failed: {e}", severity="error")


def main() -> None:
    """Main entry point for the application."""
    parser = argparse.ArgumentParser(description="texxd - A hex editor built with Textual")
    parser.add_argument("file", nargs="?", help="File to open")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Set logging level"
    )
    parser.add_argument("--log-file", type=Path, help="Log file path")

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.log_level, args.log_file)

    # Create and run app
    file_path = Path(args.file) if args.file else None
    app = TexxdApp(file_path)
    app.run()


if __name__ == "__main__":
    main()
