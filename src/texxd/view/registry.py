"""Views: the ways of looking at a node, and the columns each one shows.

A level shows one view of its node at a time, chosen from the views that
suit it: a listing of its regions for each format it could be (like a tar),
the whole of it as a document (text, a JSON tree), and always its bytes, in
hex. The most likely comes first, and is what a level starts with.

Choosing a view looks at the same thing differently, in the same level.
Opening one of its regions (a file in an archive, a string in a document)
is going inside it, and that's a new level to the right.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

from ..data import EditError
from ..formats import Format
from ..formats.text import Text
from ..node import Node
from .columns import AddressColumn, Column, HexColumn, TextColumn
from .regions import RegionRoot
from .text import LineColumn, TextEditColumn
from .tree import TreeColumn


@dataclass(frozen=True)
class View:
    """A way of looking at a node.

    ``columns`` gives the columns to show. A view of the whole node as some
    format (text in an encoding, a JSON document) has that format as
    ``whole``, and shows the node the format opens from it: the same bytes,
    or for text in another encoding, what they decode to.
    """

    name: str
    columns: Callable[[Node], list[Column]] = field(compare=False)
    whole: Optional[type[Format]] = None

    def shown(self, node: Node) -> Node:
        """The node this view shows, for ``node``."""
        if self.whole is None:
            return node
        regions = node.regions(self.whole)
        if not regions:
            raise EditError(f"{node.name} can't be read as {self.whole.name}")
        return node.child(self.whole, regions[0])


def hex_columns(node: Node) -> list[Column]:
    return [AddressColumn(), HexColumn(), TextColumn()]


def text_columns(node: Node) -> list[Column]:
    text = TextEditColumn(node)
    return [LineColumn(text), text]


def tree_columns(node: Node) -> list[Column]:
    return [TreeColumn(node, lambda: node.fmt.tree(node.data))]


def listing_columns(fmt: type[Format]) -> Callable[[Node], list[Column]]:
    return lambda node: [TreeColumn(node, lambda: RegionRoot(node, fmt))]


HEX = View("hex", hex_columns)


def text_in(encoding: str) -> View:
    """Text in a particular encoding."""
    fmt = Text.using(encoding)
    return View(f"text ({fmt.encoding})", text_columns, fmt)


def views_for(node: Node) -> list[View]:
    """The ways ``node`` can be looked at, most likely first."""
    views = []
    contents = node.fmt.contents if node.fmt else "bytes"
    # data that was opened as text or a tree is that already
    if contents == "text":
        views.append(View("text", text_columns))
    elif contents == "tree":
        views.append(View(node.fmt.name, tree_columns))
    for fmt in node.formats:
        if fmt.contents == "text":
            regions = node.regions(fmt)
            if regions:
                views.append(View(regions[0].name, text_columns, fmt.for_region(regions[0])))
        elif fmt.contents == "tree":
            views.append(View(fmt.name, tree_columns, fmt))
        elif fmt.has_regions:
            views.append(View(fmt.name, listing_columns(fmt)))
    views.append(HEX)
    return views
