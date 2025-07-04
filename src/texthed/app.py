"""Main application for TextHed hex editor."""

import sys
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer

from .hex_editor import HexEditor


class TexthedApp(App):
    """A hex editor application built with Textual."""

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        """Compose the application layout."""
        yield Header()
        yield HexEditor()
        yield Footer()

    def on_mount(self) -> None:
        """Handle mount event."""
        if len(sys.argv) > 1:
            file_path = Path(sys.argv[1])
            self.query_one(HexEditor).open(file_path)

    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()


def main() -> None:
    """Main entry point for the application."""
    app = TexthedApp()
    app.run()


if __name__ == "__main__":
    main()
