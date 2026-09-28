"""Which columns a node's level gets.

Each view is a function giving the columns it contributes for a node, or none
if it doesn't apply. A level shows the first main view that applies (how the
contents themselves are shown), then every extra view's columns, left to right.
"""

from collections.abc import Callable

from ..node import Node
from .columns import AddressColumn, Column, HexColumn, StructureColumn, TextColumn
from .text import LineColumn, TextEditColumn
from .tree import TreeColumn

View = Callable[[Node], list[Column]]


def tree_view(node: Node) -> list[Column]:
    """A tree, for data opened as one (like JSON)."""
    if node.fmt is None or node.fmt.contents != "tree":
        return []
    return [TreeColumn(node)]


def text_view(node: Node) -> list[Column]:
    """Text, for data opened as text: line numbers and the text itself."""
    if node.fmt is None or node.fmt.contents != "text":
        return []
    text = TextEditColumn(node)
    return [LineColumn(text), text]


def bytes_view(node: Node) -> list[Column]:
    """The bytes themselves: address, hex and ascii."""
    return [AddressColumn(), HexColumn(), TextColumn()]


def structure_view(node: Node) -> list[Column]:
    """A structure column for each format the node could be that has any."""
    if node.fmt is not None and node.fmt.contents == "tree":
        # the tree is its structure
        return []
    return [StructureColumn(node, fmt) for fmt in node.formats if fmt.has_regions]


MAIN_VIEWS: list[View] = [tree_view, text_view, bytes_view]
EXTRA_VIEWS: list[View] = [structure_view]


def columns_for(node: Node) -> list[Column]:
    main = next(columns for view in MAIN_VIEWS if (columns := view(node)))
    return main + [column for view in EXTRA_VIEWS for column in view(node)]
