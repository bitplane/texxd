"""Which columns a node's level gets.

Each view is a function giving the columns it contributes for a node, or none
if it doesn't apply. A level shows every view's columns, left to right.
"""

from collections.abc import Callable

from ..node import Node
from .columns import AddressColumn, Column, HexColumn, StructureColumn, TextColumn

View = Callable[[Node], list[Column]]


def bytes_view(node: Node) -> list[Column]:
    """The bytes themselves: address, hex and ascii."""
    return [AddressColumn(), HexColumn(), TextColumn()]


def structure_view(node: Node) -> list[Column]:
    """A structure column for each format the node could be that has any."""
    return [StructureColumn(node, fmt) for fmt in node.formats if fmt.has_regions]


VIEWS: list[View] = [bytes_view, structure_view]


def columns_for(node: Node) -> list[Column]:
    return [column for view in VIEWS for column in view(node)]
