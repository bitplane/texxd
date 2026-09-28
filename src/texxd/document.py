"""Documents: an open file, the tree of nodes over it, and its history."""

from pathlib import Path
from typing import Optional

from .data import Buffer, Change, History
from .node import Node


class Document:
    """An open file.

    Owns the root node, and through it every node opened inside the file. All
    their buffers share one history, so undo and redo work across them. Edits
    to derived data (like a decompressed stream) are kept in its own buffer
    until saving, which writes them back into their parents innermost first,
    then saves the file.
    """

    def __init__(self, root: Node, path: Optional[Path] = None):
        self.root = root
        self.path = path

    @classmethod
    def open(cls, path: Optional[Path]) -> "Document":
        """Open ``path``, or start a new document that will be saved there."""
        if path is not None and path.exists():
            return cls(Node.open(path), path)
        # a new file: nothing on disk until it's saved
        return cls(Node(path.name if path else "untitled", Buffer()), path)

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def buffer(self) -> Buffer:
        """The buffer for the file itself."""
        return self.root.buffer

    @property
    def history(self) -> History:
        return self.buffer.history

    @property
    def modified(self) -> bool:
        """True if anything in the document has unsaved changes."""
        return any(node.data.modified for node in self._buffered())

    def stale(self) -> list[Node]:
        """Derived data with edits that haven't been written back into its parent."""
        return [node for node in self.root.walk() if node.stale]

    def _buffered(self) -> list[Node]:
        """Nodes holding their own buffer: the root, and any derived data."""
        return [node for node in self.root.walk() if node.data is node.buffer]

    def undo(self) -> Optional[tuple[Buffer, Optional[Change]]]:
        """Undo the last step. Returns where, as for History.undo, or None if there was nothing to undo."""
        return self.history.undo()

    def redo(self) -> Optional[tuple[Buffer, Optional[Change]]]:
        """Redo the last undone step. Returns where, or None."""
        return self.history.redo()

    def commit(self) -> None:
        """Write edits to derived data back into their parents, innermost first."""
        for node in sorted(self._buffered(), key=lambda node: -len(node.path)):
            node.commit()

    def save(self, path: Optional[Path] = None) -> None:
        """Commit everything and write the file to ``path``, or where it was opened from."""
        path = path or self.path
        if path is None:
            raise ValueError("no path to save to")
        self.commit()
        self.buffer.save(path)
        self.path = path
