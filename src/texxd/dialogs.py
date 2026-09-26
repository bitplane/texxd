"""Modal dialogs."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

DIALOG_CSS = """
{name} {{
    align: center middle;
}}
{name} > Container {{
    width: 50;
    height: auto;
    border: solid $primary;
    background: $surface;
    padding: 1 2;
}}
{name} Label, {name} Input {{
    margin-bottom: 1;
}}
{name} Horizontal {{
    height: auto;
    align-horizontal: right;
}}
{name} Button {{
    margin-left: 1;
}}
"""


def parse_offset(text: str) -> int:
    """Parse ``0x1f``, ``1fh``, ``$1f`` as hex and anything else as decimal."""
    text = text.strip().replace("_", "")
    lower = text.lower()
    if lower.startswith("0x"):
        return int(lower[2:], 16)
    if lower.startswith("$"):
        return int(lower[1:], 16)
    if lower.endswith("h"):
        return int(lower[:-1], 16)
    return int(lower)


class GoToOffsetModal(ModalScreen[int | None]):
    """Ask for an offset to jump to."""

    DEFAULT_CSS = DIALOG_CSS.format(name="GoToOffsetModal")
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, max_offset: int):
        super().__init__()
        self.max_offset = max_offset

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("Go to offset (0x1f, 1fh or 31):")
            yield Input(placeholder=f"0 to 0x{self.max_offset:x}", id="offset")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Go", variant="primary", id="go")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "go":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        field = self.query_one(Input)
        if not field.value.strip():
            self.dismiss(None)
            return
        try:
            offset = parse_offset(field.value)
        except ValueError:
            field.value = ""
            field.placeholder = "not a number"
            return
        self.dismiss(max(0, min(offset, self.max_offset)))


class ConfirmModal(ModalScreen[bool]):
    """Ask a yes/no question."""

    DEFAULT_CSS = DIALOG_CSS.format(name="ConfirmModal")
    BINDINGS = [Binding("escape", "no", "No"), Binding("y", "yes", "Yes"), Binding("n", "no", "No")]

    def __init__(self, question: str, yes: str = "Yes", no: str = "No"):
        super().__init__()
        self.question = question
        self.yes_label = yes
        self.no_label = no

    def compose(self) -> ComposeResult:
        with Container():
            yield Label(self.question)
            with Horizontal():
                yield Button(self.no_label, id="no")
                yield Button(self.yes_label, variant="error", id="yes")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)
