"""A format's regions, as a tree: a listing of what's in an archive or container.

Each region is a node, so listings use the same tree view as documents like
JSON. A region that can be opened opens as a level to the right.
"""

from bisect import bisect_right
from typing import TYPE_CHECKING, Optional

from ..formats import Format, Region

if TYPE_CHECKING:
    from ..node import Node


def human_size(size: int) -> str:
    """Format a byte count compactly."""
    value = float(size)
    for unit in ("", "K", "M", "G"):
        if value < 1024:
            return f"{size}" if not unit else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}T"


class RegionNode:
    """A region of some data, as a node in a tree."""

    separator = "  "
    hidden = False
    error = None
    editable = False

    def __init__(self, fmt: type[Format], region: Region, parent: Optional["RegionNode"], index: int):
        self.fmt = fmt
        self.region = region
        self.parent = parent
        self.index = index
        self.depth = parent.depth + 1 if parent else 0
        self._children: Optional[list[RegionNode]] = None

    def __repr__(self) -> str:
        return f"RegionNode({self.region.name!r} {self.start}:{self.stop})"

    def _regions(self) -> list[Region]:
        return list(self.region.children)

    @property
    def children(self) -> list["RegionNode"]:
        if self._children is None:
            self._children = [RegionNode(self.fmt, region, self, i) for i, region in enumerate(self._regions())]
        return self._children

    @property
    def kind(self) -> str:
        return self.region.kind

    @property
    def label(self) -> str:
        return self.region.name

    @property
    def display_label(self) -> str:
        """Its name, padded to line up with the others beside it."""
        return self.label.ljust(self.parent.name_width if self.parent else 0)

    @property
    def name_width(self) -> int:
        """How wide its children's names are, so they line up."""
        return min(30, max((len(child.label) for child in self.children), default=0))

    @property
    def start(self) -> int:
        return self.region.start

    @property
    def stop(self) -> int:
        return self.region.stop

    @property
    def row_start(self) -> int:
        return self.region.start

    @property
    def container(self) -> bool:
        return bool(self.count)

    @property
    def count(self) -> int:
        return len(self.children)

    def child(self, index: int) -> "RegionNode":
        return self.children[index]

    def find(self, label) -> Optional[int]:
        return next((child.index for child in self.children if child.label == label), None)

    def index_at(self, position: int) -> Optional[int]:
        index = bisect_right(self.children, position, key=lambda child: child.start) - 1
        return index if index >= 0 else None

    @property
    def path(self) -> str:
        if self.parent is None or self.parent.hidden:
            return self.label
        return f"{self.parent.path}/{self.label}"

    def source(self, size: int) -> str:
        """What to show beside its name: how big it is, and whatever else the format says."""
        region = self.region
        amount = human_size(region.data_size) if region.kind in ("file", "text", "json") else region.kind
        return "  ".join([f"{amount:>6}", *region.info])[:size]

    def opening(self):
        return (self.fmt, self.region) if self.region.openable else None


class RegionRoot(RegionNode):
    """All of a node's regions for a format. It has no row: its regions are the top rows."""

    hidden = True

    def __init__(self, node: "Node", fmt: type[Format]):
        self.node = node
        size = node.data.size
        super().__init__(fmt, Region(node.name, 0, size, 0, size, kind=fmt.name), None, 0)

    def _regions(self) -> list[Region]:
        return self.node.regions(self.fmt)

    @property
    def container(self) -> bool:
        return True
