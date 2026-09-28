"""Trees: structured documents (like JSON) shown as a tree that folds.

A tree level's rows are the tree's visible nodes, one per row, in document
order. Every node has a byte range, so levels beside it show each node's
bytes on its row, and the tree shows the node for each row of theirs.

Formats with ``contents = "tree"`` give a tree with ``fmt.tree(data)``. The
tree only needs its nodes to have: ``kind``, ``label``, ``start``, ``stop``,
``row_start``, ``error``, ``container``, ``count``, ``child(i)``,
``find(label)``, ``index_at(position)``, ``parent``, ``index``, ``depth``,
``path`` and ``source(size)``.

What's folded is kept by path (keys and indexes), so it survives the tree
being parsed again after an edit.
"""

from bisect import bisect_left, bisect_right
from typing import TYPE_CHECKING, Any, List, Optional

from rich.segment import Segment
from rich.style import Style

from .columns import Column, Ranges
from .highlight import ACTIVE_STYLE, INACTIVE_STYLE, combine
from .rows import Rows

if TYPE_CHECKING:
    from ..node import Node
    from .view import HexView

# the most of a row's bytes other levels need to show it
ROW_BYTES = 4096

# the paths of unfolded nodes: {label: {label: ...}}
Unfolded = dict[Any, "Unfolded"]


class _Open:
    """An unfolded node's children: which of them are unfolded too, and which rows they cover.

    Rows here count from its first child. ``firsts[k]`` is the row of unfolded
    child ``indices[k]``, and its rows end before ``ends[k]``.
    """

    __slots__ = ("indices", "firsts", "ends")

    def __init__(self) -> None:
        self.indices: list[int] = []
        self.firsts: list[int] = []
        self.ends: list[int] = []

    def extra_through(self, k: int) -> int:
        """Rows added by unfolded children up to and including the ``k``th of them."""
        return self.ends[k] - 1 - self.indices[k] if k >= 0 else 0


class TreeRows(Rows):
    """The visible nodes of a tree, a row each.

    A stream (several documents in one) has no row of its own: its documents
    are the top rows.
    """

    def __init__(self, root, unfolded: Unfolded, limit: int):
        self.root = root
        self.unfolded = unfolded
        self.limit = limit
        self.show_root = root.kind != "stream"
        self._open: dict[int, _Open] = {}
        # the root is always open: there'd be nothing to see otherwise
        self._count = self._layout(root, unfolded) + (1 if self.show_root else 0)

    def _layout(self, node, unfolded: Unfolded) -> int:
        """Lay out an unfolded node's children. Returns how many rows they take."""
        spec = _Open()
        opened = []
        for label, below in unfolded.items():
            index = node.find(label)
            if index is not None and node.child(index).container:
                opened.append((index, below))
        extra = 0
        for index, below in sorted(opened, key=lambda item: item[0]):
            first = index + extra
            rows = self._layout(node.child(index), below)
            spec.indices.append(index)
            spec.firsts.append(first)
            spec.ends.append(first + 1 + rows)
            extra += rows
        self._open[id(node)] = spec
        return node.count + extra

    def is_open(self, node) -> bool:
        return id(node) in self._open

    def node(self, row: int):
        """The node on ``row``."""
        node = self.root
        if self.show_root:
            if row <= 0:
                return node
            row -= 1
        while True:
            spec = self._open[id(node)]
            k = bisect_right(spec.firsts, row) - 1
            if k >= 0 and row < spec.ends[k]:
                child = node.child(spec.indices[k])
                if row == spec.firsts[k]:
                    return child
                row -= spec.firsts[k] + 1
                node = child
                continue
            index = row - spec.extra_through(k)
            return node.child(max(0, min(index, node.count - 1)))

    def row_of_node(self, node) -> int:
        if node.parent is None:
            return 0 if self.show_root else -1
        base = self.row_of_node(node.parent) + 1
        spec = self._open[id(node.parent)]
        k = bisect_left(spec.indices, node.index)
        if k < len(spec.indices) and spec.indices[k] == node.index:
            return base + spec.firsts[k]
        return base + node.index + spec.extra_through(k - 1)

    def node_at(self, position: int):
        """The deepest visible node whose row starts at or before ``position``."""
        node = self.root
        while self.is_open(node) and node.count:
            index = node.index_at(position)
            if index is None:
                break
            node = node.child(index)
        if node is self.root and not self.show_root:
            node = self.root.child(0)
        return node

    def depth(self, node) -> int:
        """How far in to draw ``node``."""
        return node.depth if self.show_root else node.depth - 1

    @property
    def count(self) -> int:
        return self._count

    def start(self, row: int) -> int:
        return self.node(row).row_start

    def stop(self, row: int) -> int:
        node = self.node(row)
        stop = node.stop
        if self.is_open(node) and node.count:
            # an unfolded node's row is just its start: its children have rows of their own
            stop = node.child(0).row_start
        return max(node.row_start, min(stop, node.row_start + ROW_BYTES))

    def row_of(self, position: int) -> int:
        return max(0, self.row_of_node(self.node_at(position)))

    def x_of(self, position: int) -> int:
        return 0

    def position_at(self, row: int, x: int) -> int:
        return self.start(max(0, min(row, self._count - 1)))

    def end_of(self, row: int) -> int:
        return self.start(row)

    def step(self, position: int, delta: int) -> int:
        return self.position_at(self.row_of(position) + delta, 0)


def path_of(node) -> list:
    """The labels from the root down to ``node``."""
    labels = []
    while node.parent is not None:
        labels.append(node.label)
        node = node.parent
    return labels[::-1]


STYLES = {
    "key": Style(color="bright_cyan"),
    "string": Style(color="green"),
    "number": Style(color="bright_magenta"),
    "bool": Style(color="yellow"),
    "null": Style(color="yellow", italic=True),
    "error": Style(color="bright_red"),
    "summary": Style(color="bright_black"),
    "marker": Style(color="bright_black"),
}


class TreeColumn(Column):
    """A document as a tree: a node per row, unfolded with right or space, folded with left."""

    focusable = True
    flexible = True
    title = "tree"
    min_width = 30
    max_width = 160
    indent = 2
    selection_style = Style(bgcolor="dark_green")

    def __init__(self, node: "Node"):
        self.node = node
        self.wrap = 60
        self.unfolded: Unfolded = {}
        self._rows: Optional[TreeRows] = None
        self._version: Optional[int] = None
        self._root = None
        self.current = None
        self.focused = False

    @property
    def tree(self):
        if self._root is None or self._version != self.node.version:
            self._root = self.node.fmt.tree(self.node.data)
            self._version = self.node.version
            self._rows = None
        return self._root

    def width(self, bytes_per_line: int, size: int) -> int:
        return self.wrap

    def fit(self, width: int) -> None:
        self.wrap = max(self.min_width, min(self.max_width, width))

    def rows(self, bytes_per_line: int, limit: int) -> TreeRows:
        root = self.tree
        if self._rows is None:
            self._rows = TreeRows(root, self.unfolded, limit)
        self._rows.limit = limit
        return self._rows

    def render(self, offset, data, styles, bytes_per_line, size, cursor, first=0) -> List[Segment]:
        rows = self.rows(bytes_per_line, size)
        blank = [Segment(" " * self.wrap)]
        if offset < 0 or not data:
            return blank
        # the node whose row starts in this row's bytes
        node = rows.node_at(offset + len(data) - 1)
        if node.row_start < offset + first:
            return blank
        segments = self._line(rows, node)
        if node is self.current:
            style = ACTIVE_STYLE if self.focused else INACTIVE_STYLE
            segments = [Segment(segment.text, combine(segment.style, style)) for segment in segments]
        return segments

    def _line(self, rows: TreeRows, node) -> List[Segment]:
        parts: list[tuple[str, Optional[Style]]] = [(" " * (self.indent * rows.depth(node)), None)]
        if node.container:
            parts.append(("▾ " if rows.is_open(node) else "▸ ", STYLES["marker"]))
        else:
            parts.append(("  ", None))
        if node.label is not None:
            label = f"[{node.label}]" if isinstance(node.label, int) else node.label
            parts.append((f"{label}: ", STYLES["key"]))
        room = max(0, self.wrap - sum(len(text) for text, _ in parts))
        if node.error:
            parts.append((f"{node.source(room)} ⚠ {node.error}", STYLES["error"]))
        elif node.container and rows.is_open(node):
            opener = {"object": "{", "array": "[", "stream": ""}[node.kind]
            noun = "keys" if node.kind == "object" else "items"
            parts.append((f"{opener} {node.count} {noun}", STYLES["summary"]))
        else:
            parts.append((node.source(room), STYLES.get(node.kind)))
        segments = []
        used = 0
        for text, style in parts:
            text = text[: max(0, self.wrap - used)]
            if text:
                segments.append(Segment(text, style))
                used += len(text)
        if len(parts[-1][0]) > room and used == self.wrap and segments:
            last = segments[-1]
            segments[-1] = Segment(last.text[:-1] + "…", last.style)
        segments.append(Segment(" " * (self.wrap - used)))
        return segments

    def sync(self, cursor: Optional[int], opened, focused: bool) -> Ranges:
        self.focused = focused
        if cursor is None:
            self.current = None
            return []
        rows = self._rows or self.rows(0, cursor)
        self.current = rows.node(rows.row_of(cursor))
        return [(self.current.row_start, self.current.stop, self.selection_style)]

    def on_key(self, view: "HexView", key: str, char: Optional[str]) -> bool:
        rows = view.cursor.layout
        if not isinstance(rows, TreeRows):
            return False
        node = rows.node(rows.row_of(view.cursor.position))
        foldable = node.container and node.count and node.parent is not None
        if key == "right":
            if foldable and not rows.is_open(node):
                self.set_open(node, True)
                view.relayout(node.row_start)
            elif node.container and node.count:
                view.go_to(node.child(0).row_start)
            return True
        if key in ("space", "enter") and foldable:
            self.set_open(node, not rows.is_open(node))
            view.relayout(node.row_start)
            return True
        if key == "left":
            if foldable and rows.is_open(node):
                self.set_open(node, False)
                view.relayout(node.row_start)
            elif node.parent is not None and (node.parent.parent is not None or rows.show_root):
                view.go_to(node.parent.row_start)
            return True
        return False

    def set_open(self, node, open: bool) -> None:
        """Unfold or fold ``node``."""
        path = path_of(node)
        level = self.unfolded
        for label in path[:-1]:
            level = level.setdefault(label, {})
        if open:
            level.setdefault(path[-1], {})
        else:
            level.pop(path[-1], None)
        self._rows = None

    def describe(self, offset: int) -> Optional[str]:
        node = self.current
        if node is None:
            return None
        kind = node.kind
        if node.container:
            kind += f", {node.count} {'keys' if node.kind == 'object' else 'items'}"
        return f"{node.path} ({kind})"
