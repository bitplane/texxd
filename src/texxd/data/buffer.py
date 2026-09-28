"""Editable byte buffers.

``Buffer`` is a piece table: the current contents are described by a RangeMap
of pieces, each pointing into either the original source or an append-only
buffer of added bytes. Nothing is copied until save.

``Window`` is a view onto part of another buffer. Reads and edits go straight
through to the parent, so all edits live in the root buffer and saving the
root saves everything.

Edits happen in transactions, and undo steps are kept in a History; see
history.py.
"""

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ContextManager, Optional, Tuple

from .history import History
from .rangemap import RangeMap
from .source import BytesSource, FileSource, Source

ORIGINAL = 0
ADDED = 1

CHUNK_SIZE = 1024 * 1024


class ResizeError(Exception):
    """Raised when an edit would change the size of something that can't be resized."""


@dataclass(frozen=True)
class Change:
    """Describes an edit: ``removed`` bytes at ``offset`` were replaced by ``inserted`` bytes.

    An overwrite has removed == inserted, an insert has removed == 0 and a
    delete has inserted == 0. ``origin`` is the data the edit was made through,
    so a window can tell its own inserts at its edges from its neighbours'.
    """

    offset: int
    removed: int
    inserted: int
    origin: Optional["Data"] = field(default=None, compare=False, repr=False)

    @property
    def delta(self) -> int:
        """How much the size changed by."""
        return self.inserted - self.removed

    def inverse(self) -> "Change":
        """The change that undoes this one."""
        return Change(self.offset, self.inserted, self.removed, self.origin)

    def made_through(self, data: "Data") -> bool:
        """True if the edit was made through ``data``, or something derived from it."""
        origin = self.origin
        while origin is not None:
            if origin is data:
                return True
            origin = origin.parent
        return False


Listener = Callable[[Change], None]


class Data:
    """Interface shared by everything a view can display and edit.

    Data can be derived from a parent: ``to_parent`` and ``from_parent`` map
    offsets between the two where the bytes correspond one to one, and return
    None where they don't (outside the region, or for transformed data like a
    decompressed stream).
    """

    resizable = False
    parent: Optional["Data"] = None
    # False once the bytes this was derived from have moved out from under it
    valid = True

    def __init__(self) -> None:
        self._listeners: list[Listener] = []

    @property
    def size(self) -> int:
        raise NotImplementedError()

    @property
    def root(self) -> "Buffer":
        """The buffer that actually holds the edits."""
        raise NotImplementedError()

    def read(self, offset: int, size: int) -> bytes:
        """Read up to ``size`` bytes from ``offset``."""
        raise NotImplementedError()

    def write(self, offset: int, data: bytes, origin: Optional["Data"] = None) -> None:
        """Overwrite bytes at ``offset``. ``origin`` is the data the edit is being made through."""
        raise NotImplementedError()

    def insert(self, offset: int, data: bytes, origin: Optional["Data"] = None) -> None:
        """Insert bytes at ``offset``, moving everything after it."""
        raise ResizeError("can't insert here")

    def delete(self, offset: int, size: int, origin: Optional["Data"] = None) -> None:
        """Remove ``size`` bytes at ``offset``."""
        raise ResizeError("can't delete here")

    def edits(self, start: int, stop: int) -> list[Tuple[int, int]]:
        """Ranges within [start, stop) that have unsaved changes."""
        raise NotImplementedError()

    def to_parent(self, offset: int) -> Optional[int]:
        """Where byte ``offset`` is in the parent, if it maps there directly."""
        return None

    def from_parent(self, offset: int) -> Optional[int]:
        """Where the parent's byte ``offset`` is in this data, if it maps here directly."""
        return None

    def to_root(self, offset: int) -> Optional[int]:
        """Where byte ``offset`` is in the root buffer, if it maps there directly."""
        data = self
        while data.parent is not None:
            offset = data.to_parent(offset)
            if offset is None:
                return None
            data = data.parent
        return offset

    def from_root(self, offset: int) -> Optional[int]:
        """Where the root buffer's byte ``offset`` is in this data, if it maps here directly."""
        if self.parent is None:
            return offset
        offset = self.parent.from_root(offset)
        return None if offset is None else self.from_parent(offset)

    def from_buffer(self, offset: int) -> Optional[int]:
        """Where byte ``offset`` of the buffer holding this data's edits is in this data, if it maps here."""
        if self is self.root:
            return offset
        offset = self.parent.from_buffer(offset)
        return None if offset is None else self.from_parent(offset)

    def subscribe(self, listener: Listener) -> None:
        """Call ``listener`` with a Change whenever the contents change."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        """Stop calling ``listener``."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _emit(self, change: Change) -> None:
        for listener in list(self._listeners):
            listener(change)


def _adjust_piece(piece: Tuple[int, int], moved: int) -> Tuple[int, int]:
    """Keep a piece pointing at the same source bytes when it moves."""
    source, delta = piece
    return source, delta - moved


class Buffer(Data):
    """A resizable piece table over a Source, with undo."""

    resizable = True

    def __init__(self, source: Optional[Source] = None, parent: Optional[Data] = None):
        super().__init__()
        # set for a buffer holding data derived from another, like a decompressed
        # stream: its offsets don't map to the parent's, and it keeps its own edits
        self.parent = parent
        self._source = source if source is not None else BytesSource()
        self._added = bytearray()
        self._pieces = RangeMap(_adjust_piece)
        self._size = self._source.size
        if self._size:
            self._pieces[0 : self._size] = (ORIGINAL, 0)
        self._version = 0
        self._next_version = 1
        self._saved_version = 0
        # derived data shares its history with the data it came from
        self.history: History = parent.root.history if parent is not None else History()

    @classmethod
    def open(cls, path: Path) -> "Buffer":
        """Make a buffer for a file on disk."""
        return cls(FileSource(path))

    @property
    def source(self) -> Source:
        return self._source

    @property
    def size(self) -> int:
        return self._size

    @property
    def root(self) -> "Buffer":
        return self

    @property
    def modified(self) -> bool:
        """True if there are unsaved changes."""
        return self._version != self._saved_version

    def mark_saved(self) -> None:
        """Count the contents as they are now as saved, like once derived data is written back."""
        self._saved_version = self._version

    @property
    def can_undo(self) -> bool:
        return self.history.can_undo

    @property
    def can_redo(self) -> bool:
        return self.history.can_redo

    def read(self, offset: int, size: int) -> bytes:
        stop = min(offset + size, self._size)
        if offset < 0 or offset >= stop:
            return b""
        out = bytearray()
        for start, end, (source, delta) in self._pieces.overlapping(offset, stop):
            lo = max(start, offset) + delta
            hi = min(end, stop) + delta
            if source == ORIGINAL:
                out += self._source.read(lo, hi - lo)
            else:
                out += self._added[lo:hi]
        return bytes(out)

    def transaction(self) -> ContextManager[None]:
        """Group edits into one undo step; see History.transaction."""
        return self.history.transaction()

    def defer(self, key: Any, priority: int, work: Callable[[], None]) -> None:
        """Run ``work`` before the current transaction finishes; see History.defer."""
        self.history.defer(key, priority, work)

    def _state(self) -> tuple:
        return self._pieces, self._size, self._version, self._saved_version

    def _restore(self, state: tuple) -> None:
        self._pieces, self._size, self._version, self._saved_version = state

    def _begin_step(self) -> tuple:
        """A step is about to edit this buffer: returns the state to go back to, and bumps the version."""
        state = (self._pieces.copy(), self._size, self._version, self._saved_version)
        self._version = self._next_version
        self._next_version += 1
        return state

    def _record(self, change: Change) -> None:
        self.history.record(self, change)

    def _add(self, offset: int, data: bytes) -> None:
        """Append data to the add buffer and map [offset, offset + len) onto it."""
        where = len(self._added)
        self._added += data
        self._pieces[offset : offset + len(data)] = (ADDED, where - offset)

    def write(self, offset: int, data: bytes, origin: Optional[Data] = None) -> None:
        """Overwrite bytes at ``offset``. Writing past the end extends the buffer."""
        if not data:
            return
        if offset < 0 or offset > self._size:
            raise IndexError(f"write at {offset} outside buffer of size {self._size}")
        replaced = min(len(data), self._size - offset)
        change = Change(offset, replaced, len(data), origin)
        with self.transaction():
            self._record(change)
            self._add(offset, data)
            self._size = max(self._size, offset + len(data))
            self._emit(change)

    def insert(self, offset: int, data: bytes, origin: Optional[Data] = None) -> None:
        if not data:
            return
        if offset < 0 or offset > self._size:
            raise IndexError(f"insert at {offset} outside buffer of size {self._size}")
        change = Change(offset, 0, len(data), origin)
        with self.transaction():
            self._record(change)
            self._pieces.shift(offset, len(data))
            self._add(offset, data)
            self._size += len(data)
            self._emit(change)

    def delete(self, offset: int, size: int, origin: Optional[Data] = None) -> None:
        size = min(size, self._size - offset)
        if size <= 0 or offset < 0:
            return
        change = Change(offset, size, 0, origin)
        with self.transaction():
            self._record(change)
            self._pieces.shift(offset, -size)
            self._size -= size
            self._emit(change)

    def undo(self) -> Optional[Change]:
        """Undo the last step. Returns the change it started with, undone, or None if there was nothing to undo."""
        where = self.history.undo()
        return where and where[1]

    def redo(self) -> Optional[Change]:
        """Redo the last undone step. Returns the change it started with, or None."""
        where = self.history.redo()
        return where and where[1]

    def edits(self, start: int, stop: int) -> list[Tuple[int, int]]:
        out = []
        for lo, hi, (source, _) in self._pieces.overlapping(start, stop):
            if source != ADDED:
                continue
            lo, hi = max(lo, start), min(hi, stop)
            if out and out[-1][1] == lo:
                out[-1] = (out[-1][0], hi)
            else:
                out.append((lo, hi))
        return out

    def _in_place_ok(self, path: Path) -> bool:
        """True if saving to ``path`` can patch the original file rather than rewrite it.

        That works when no original bytes have moved, so only added bytes need writing.
        """
        source = self._source
        if not isinstance(source, FileSource) or not source.writable:
            return False
        if os.path.realpath(source.path) != os.path.realpath(path):
            return False
        return all(delta == 0 for _, _, (src, delta) in self._pieces if src == ORIGINAL)

    def save(self, path: Optional[Path] = None) -> None:
        """Write the contents to ``path``, or back to the file it came from."""
        if path is None:
            if not isinstance(self._source, FileSource):
                raise ValueError("no path to save to")
            path = self._source.path
        path = Path(path)

        if self._in_place_ok(path):
            with open(path, "r+b") as f:
                for start, stop, (source, delta) in self._pieces:
                    if source == ADDED:
                        f.seek(start)
                        f.write(self._added[start + delta : stop + delta])
                f.truncate(self._size)
            self._source.refresh()
        else:
            self._rewrite(path)

        self._added = bytearray()
        self._pieces.clear()
        if self._size:
            self._pieces[0 : self._size] = (ORIGINAL, 0)
        self.mark_saved()
        self._emit(Change(0, self._size, self._size))
        # history points into the old source, which is gone now
        self.history.clear()

    def _rewrite(self, path: Path) -> None:
        """Write a whole new file next to ``path`` then move it into place."""
        real = Path(os.path.realpath(path))
        if real.exists() and not os.access(real, os.W_OK):
            raise PermissionError(f"{path} is read-only")
        fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}.", suffix=".texxd")
        try:
            with os.fdopen(fd, "wb") as out:
                for offset in range(0, self._size, CHUNK_SIZE):
                    out.write(self.read(offset, CHUNK_SIZE))
                out.flush()
                os.fsync(out.fileno())
            if real.exists():
                shutil.copymode(real, tmp)
            os.replace(tmp, real)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        self._source.close()
        self._source = FileSource(path)

    def close(self) -> None:
        """Release the source."""
        self._source.close()


class Window(Data):
    """A view of part of another buffer.

    Edits pass straight through to the parent. The window follows its bytes
    when the parent changes: edits before it move it, edits inside it grow or
    shrink it. An insert right at one of its edges belongs to it only if it was
    made through it. If an edit straddles an edge the window can't tell where
    its bytes went, so it becomes invalid.
    """

    def __init__(self, parent: Data, start: int, size: int):
        super().__init__()
        self.parent = parent
        self.start = start
        self._size = size
        self.valid = True
        parent.subscribe(self._on_parent_change)

    @property
    def size(self) -> int:
        return self._size

    @property
    def resizable(self) -> bool:
        return self.parent.resizable

    @property
    def root(self) -> Buffer:
        return self.parent.root

    def read(self, offset: int, size: int) -> bytes:
        size = min(size, self._size - offset)
        if offset < 0 or size <= 0:
            return b""
        return self.parent.read(self.start + offset, size)

    def write(self, offset: int, data: bytes, origin: Optional[Data] = None) -> None:
        """Overwrite bytes at ``offset``. Writing past the end extends the window."""
        if offset < 0 or offset > self._size:
            raise IndexError(f"write at {offset} outside window of size {self._size}")
        inside, rest = data[: self._size - offset], data[self._size - offset :]
        with self.root.transaction():
            if inside:
                self.parent.write(self.start + offset, inside, origin or self)
            if rest:
                self.insert(offset + len(inside), rest, origin)

    def insert(self, offset: int, data: bytes, origin: Optional[Data] = None) -> None:
        if not 0 <= offset <= self._size:
            raise IndexError(f"insert at {offset} outside window of size {self._size}")
        if not self.resizable:
            raise ResizeError("can't insert here")
        self.parent.insert(self.start + offset, data, origin or self)

    def delete(self, offset: int, size: int, origin: Optional[Data] = None) -> None:
        size = min(size, self._size - offset)
        if size <= 0 or offset < 0:
            return
        if not self.resizable:
            raise ResizeError("can't delete here")
        self.parent.delete(self.start + offset, size, origin or self)

    def to_parent(self, offset: int) -> Optional[int]:
        return self.start + offset if 0 <= offset <= self._size else None

    def from_parent(self, offset: int) -> Optional[int]:
        offset -= self.start
        return offset if 0 <= offset <= self._size else None

    def edits(self, start: int, stop: int) -> list[Tuple[int, int]]:
        stop = min(stop, self._size)
        edits = self.parent.edits(self.start + start, self.start + stop)
        return [(lo - self.start, hi - self.start) for lo, hi in edits]

    def _on_parent_change(self, change: Change) -> None:
        if not self.valid:
            return
        start, end = self.start, self.start + self._size
        lo, hi = change.offset, change.offset + change.removed
        if change.delta == 0:
            lo, hi = max(lo, start), min(hi, end)
            if lo < hi:
                self._emit(Change(lo - start, hi - lo, hi - lo, change.origin))
            return
        ours = change.made_through(self)
        if hi < start or (hi == start and (lo < start or not ours)):
            self.start += change.delta
        elif lo > end or (lo == end and (hi > end or not ours)):
            pass
        elif start <= lo and hi <= end:
            self._size += change.delta
            self._emit(Change(lo - start, change.removed, change.inserted, change.origin))
        else:
            self.valid = False
            self._emit(Change(0, self._size, self._size, change.origin))
