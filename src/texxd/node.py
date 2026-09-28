"""Nodes: a piece of data, where it came from, and what it looks like."""

from bisect import bisect_right
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Optional

from .data import Buffer, Change, Data, ResizeError, Window
from .formats import Format, Region, detect
from .log import get_logger

logger = get_logger(__name__)


class StaleError(Exception):
    """Raised for an edit to encoded bytes whose decoded data has edits that haven't been committed."""

    def __init__(self, node: "Node"):
        super().__init__(f"{node.name} has edits that aren't in {node.parent.name} yet")
        self.node = node


class Node:
    """A named piece of data in the tree, like a file or a file inside an archive.

    The root node owns a Buffer for the file on disk. A child node is a region
    of its parent opened by a format, which decides what data it gets: usually
    a window onto the region's contents, but for encoded contents (like a
    compressed stream) a buffer of its own, derived from them. Either way the
    node keeps a window onto the contents in the parent, its span, which
    follows them as the parent changes. When the span changes size through the
    node, its format fixes up the parent to match.

    Edits to derived data stay in its own buffer until they're committed:
    encoded by the format and written back over the span. Until then the
    span's bytes are stale, and editing them any other way is refused. When
    they change while the derived data has no edits, it's decoded again.
    """

    def __init__(
        self,
        name: str,
        data: Data,
        parent: Optional["Node"] = None,
        fmt: Optional[type[Format]] = None,
        region: Optional[Region] = None,
    ):
        self.name = name
        self.data = data
        self.parent = parent
        # the format and region of the parent this was opened from
        self.fmt = fmt
        self._region = region
        self._formats: Optional[list[type[Format]]] = None
        self._regions: dict[type[Format], list[Region]] = {}
        self._children: list["Node"] = []
        # counts changes to the data, for things that cache what they worked out from it
        self.version = 0
        data.subscribe(self._on_change)
        # where the contents are in the parent: the data itself if its bytes are there
        self.span: Optional[Data] = None
        if parent is not None and region is not None:
            if data.to_parent(0) is not None:
                self.span = data
            else:
                self.span = Window(parent.data, region.data_start, region.data_size)
                self.span.root.guards.append(self._guard)
            self.span.subscribe(self._on_span_change)

    @classmethod
    def open(cls, path: Path) -> "Node":
        """Make a root node for a file on disk."""
        return cls(Path(path).name, Buffer.open(path))

    @property
    def buffer(self) -> Buffer:
        """The root buffer holding the edits."""
        return self.data.root

    @property
    def derived(self) -> bool:
        """True if this node's data is decoded from its parent's, rather than being its bytes."""
        return self.span is not None and self.span is not self.data

    @property
    def stale(self) -> bool:
        """True if this is derived data with edits that haven't been written back to its span."""
        return self.derived and self.valid and self.data.modified

    @property
    def region(self) -> Optional[Region]:
        """The region of the parent this was opened from, as it is now."""
        region = self._current_region()
        if region is not None or self._region is None:
            return region
        # the parent can't be parsed right now: assume only its position changed
        stored = self._region
        start = self.span.to_parent(0)
        moved = start - stored.data_start
        return replace(stored, start=stored.start + moved, stop=stored.stop + moved, data_start=start)

    def _current_region(self) -> Optional[Region]:
        """The region as the parent's bytes describe it now, if they still do."""
        if self._region is None:
            return None
        start = self.span.to_parent(0)
        region = self.parent.region_before(start + 1, self.fmt)
        if region is not None and region.data_start == start and region.name == self._region.name:
            return region
        return None

    @property
    def resizable(self) -> bool:
        """True if edits can change the size of this node's data."""
        if not self.data.resizable:
            return False
        if self.parent is None:
            return True
        return self.fmt is not None and self.fmt.can_resize and self.parent.resizable

    @property
    def valid(self) -> bool:
        """False if the data this node pointed at has moved out from under it."""
        if self.parent is None:
            return self.data.valid
        return self.data.valid and self.span.valid and self.parent.valid

    @property
    def path(self) -> list["Node"]:
        """Nodes from the root down to this one."""
        return (self.parent.path if self.parent else []) + [self]

    @property
    def formats(self) -> list[type[Format]]:
        """What this data could be, most likely first."""
        if self._formats is None:
            formats = detect(self.data)
            if self.fmt is not None:
                formats = [fmt for fmt in formats if fmt.nests or fmt.name != self.fmt.name]
            self._formats = formats
        return self._formats

    def regions(self, fmt: Optional[type[Format]] = None) -> list[Region]:
        """Top level regions for the given format, or the most likely one."""
        if fmt is None:
            fmt = self.formats[0]
        if fmt not in self._regions:
            try:
                self._regions[fmt] = fmt.regions(self.data)
            except Exception:
                logger.exception(f"couldn't parse {self.name} as {fmt.name}")
                self._regions[fmt] = []
        return self._regions[fmt]

    def region_at(self, offset: int, fmt: Optional[type[Format]] = None) -> Optional[Region]:
        """The top level region containing ``offset``, if any."""
        region = self.region_before(offset + 1, fmt)
        if region and region.contains(offset):
            return region
        return None

    def region_before(self, offset: int, fmt: Optional[type[Format]] = None) -> Optional[Region]:
        """The last top level region that starts before ``offset``."""
        regions = self.regions(fmt)
        i = bisect_right(regions, offset - 1, key=lambda r: r.start) - 1
        return regions[i] if i >= 0 else None

    def walk(self) -> Iterator["Node"]:
        """This node and all its open descendants, parents first."""
        yield self
        for node in self._children:
            if node.valid:
                yield from node.walk()

    def child(self, fmt: type[Format], region: Region) -> "Node":
        """Open a region as a child node. The same region gives the same node."""
        fmt = fmt.for_region(region)
        self._children = [node for node in self._children if node.valid]
        for node in self._children:
            current = node.region
            if node.fmt is fmt and (current.name, current.data_start) == (region.name, region.data_start):
                return node
        node = Node(region.name, fmt.open(self.data, region), parent=self, fmt=fmt, region=region)
        self._children.append(node)
        return node

    def _on_change(self, change: Change) -> None:
        # contents changed, so the detected format and structure may have too
        self.version += 1
        self._formats = None
        self._regions.clear()

    def _on_span_change(self, change: Change) -> None:
        if change.made_through(self.span):
            # edits made through this node keep its container consistent; raw edits to
            # the container's bytes are left as they are
            if change.delta:
                # innermost first, so each container sees its contents' final size
                self.buffer.defer(("fixup", id(self)), len(self.path), self._fixup)
        elif self.derived:
            self._redecode()

    def _guard(self, change: Change) -> None:
        """Refuse edits to the span's bytes, other than through it, while they're stale."""
        if not self.stale or change.made_through(self.span):
            return
        start = self.span.to_buffer(0)
        stop = start + self.span.size
        lo, hi = change.offset, change.offset + change.removed
        if (lo < stop and hi > start) or start < lo < stop:
            raise StaleError(self)

    def _decode(self) -> Buffer:
        return self.fmt.open(self.parent.data, self.region)

    def _redecode(self) -> None:
        """The encoded bytes changed some other way: decode them again."""
        if not self.valid:
            return
        if self.data.modified:
            logger.warning(f"{self.name} changed underneath uncommitted edits")
            return
        try:
            fresh = self._decode()
        except Exception as e:
            logger.warning(f"{self.name} can't be decoded any more: {e}")
            self.data.valid = False
            return
        self.data.replace(fresh.source, record=False)

    def _fixup(self) -> None:
        if not self.valid:
            return
        # the container's header still describes the contents as they were
        region = self._current_region()
        if region is None:
            raise ResizeError(f"lost track of {self.name} in {self.parent.name}")
        if self.span.size != region.data_size:
            self._region = self.fmt.fixup(self.parent.data, region, self.span.size)

    def discard(self) -> None:
        """Throw away derived data's uncommitted edits, decoding it again. Undo brings them back."""
        if not self.stale:
            return
        fresh = self._decode()
        with self.data.transaction():
            self.data.replace(fresh.source)
            self.data.mark_saved()

    def commit(self) -> None:
        """Encode derived data's edits and write them back over its contents in the parent.

        One undo step: undoing it puts the parent back and leaves the edits
        uncommitted again.
        """
        data = self.data
        if not self.derived or not data.modified:
            return
        encoded = self.fmt.encode(self.parent.data, self.region, data)
        with data.transaction():
            data.history.record(data)
            span = self.span
            if len(encoded) < span.size:
                span.delete(len(encoded), span.size - len(encoded))
            span.write(0, encoded)
            data.mark_saved()
