"""Nodes: a piece of data, where it came from, and what it looks like."""

from bisect import bisect_right
from pathlib import Path
from typing import Optional

from .data import Buffer, Change, Data, Window
from .formats import Entry, Format, detect
from .log import get_logger

logger = get_logger(__name__)


class Node:
    """A named piece of data in the tree, like a file or a file inside an archive.

    The root node owns a Buffer for the file on disk. Child nodes are Windows
    onto their parent's data, so edits made in a child land in the root buffer.
    """

    def __init__(self, name: str, data: Data, parent: Optional["Node"] = None):
        self.name = name
        self.data = data
        self.parent = parent
        self._formats: Optional[list[type[Format]]] = None
        self._entries: dict[type[Format], list[Entry]] = {}
        self._children: dict[tuple, "Node"] = {}
        data.subscribe(self._on_change)

    @classmethod
    def open(cls, path: Path) -> "Node":
        """Make a root node for a file on disk."""
        return cls(Path(path).name, Buffer.open(path))

    @property
    def buffer(self) -> Buffer:
        """The root buffer holding the edits."""
        return self.data.root

    @property
    def valid(self) -> bool:
        """False if the data this node pointed at has moved out from under it."""
        if isinstance(self.data, Window) and not self.data.valid:
            return False
        return self.parent is None or self.parent.valid

    @property
    def path(self) -> list["Node"]:
        """Nodes from the root down to this one."""
        return (self.parent.path if self.parent else []) + [self]

    @property
    def formats(self) -> list[type[Format]]:
        """What this data could be, most likely first."""
        if self._formats is None:
            self._formats = detect(self.data)
        return self._formats

    def entries(self, fmt: Optional[type[Format]] = None) -> list[Entry]:
        """Entries for the given format, or the most likely one that has structure."""
        if fmt is None:
            fmt = self.formats[0]
        if fmt not in self._entries:
            try:
                self._entries[fmt] = fmt.entries(self.data)
            except Exception:
                logger.exception(f"couldn't parse {self.name} as {fmt.name}")
                self._entries[fmt] = []
        return self._entries[fmt]

    def entry_at(self, offset: int, fmt: Optional[type[Format]] = None) -> Optional[Entry]:
        """The entry containing ``offset``, if any."""
        entry = self.entry_before(offset + 1, fmt)
        if entry and entry.contains(offset):
            return entry
        return None

    def entry_before(self, offset: int, fmt: Optional[type[Format]] = None) -> Optional[Entry]:
        """The last entry that starts before ``offset``."""
        entries = self.entries(fmt)
        i = bisect_right(entries, offset - 1, key=lambda e: e.start) - 1
        return entries[i] if i >= 0 else None

    def child(self, entry: Entry) -> "Node":
        """Open an entry as a child node. The same entry gives the same node."""
        key = (entry.name, entry.data_start, entry.data_size)
        node = self._children.get(key)
        if node is None or not node.valid:
            node = Node(entry.name, Window(self.data, entry.data_start, entry.data_size), parent=self)
            self._children[key] = node
        return node

    def local_offset(self, root_offset: int) -> Optional[int]:
        """Convert an offset in the root buffer to one in this node, if it falls inside it."""
        offset = root_offset
        for node in self.path[1:]:
            offset -= node.data.start
        if 0 <= offset <= self.data.size:
            return offset
        return None

    def _on_change(self, change: Change) -> None:
        # contents changed, so the detected format and structure may have too
        self._formats = None
        self._entries.clear()
