"""Editable byte buffers.

``Buffer`` is a piece table: the current contents are described by a RangeMap
of pieces, each pointing into either the original source or an append-only
buffer of added bytes. Nothing is copied until save.

``Window`` is a fixed-size view onto part of another buffer. Reads and writes
go straight through to the parent, so all edits live in the root buffer and
saving the root saves everything.
"""

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

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
    delete has inserted == 0.
    """

    offset: int
    removed: int
    inserted: int

    @property
    def delta(self) -> int:
        """How much the size changed by."""
        return self.inserted - self.removed

    def inverse(self) -> "Change":
        """The change that undoes this one."""
        return Change(self.offset, self.inserted, self.removed)


Listener = Callable[[Change], None]


class Data:
    """Interface shared by everything a view can display and edit."""

    resizable = False

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

    def write(self, offset: int, data: bytes) -> None:
        """Overwrite bytes at ``offset``."""
        raise NotImplementedError()

    def insert(self, offset: int, data: bytes) -> None:
        """Insert bytes at ``offset``, moving everything after it."""
        raise ResizeError("can't insert here")

    def delete(self, offset: int, size: int) -> None:
        """Remove ``size`` bytes at ``offset``."""
        raise ResizeError("can't delete here")

    def edits(self, start: int, stop: int) -> list[Tuple[int, int]]:
        """Ranges within [start, stop) that have unsaved changes."""
        raise NotImplementedError()

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


@dataclass
class _Step:
    """One undoable edit: the state before it, and what it changed."""

    pieces: RangeMap
    size: int
    version: int
    change: Change


class Buffer(Data):
    """A resizable piece table over a Source, with undo."""

    resizable = True

    def __init__(self, source: Optional[Source] = None):
        super().__init__()
        self._source = source if source is not None else BytesSource()
        self._added = bytearray()
        self._pieces = RangeMap(_adjust_piece)
        self._size = self._source.size
        if self._size:
            self._pieces[0 : self._size] = (ORIGINAL, 0)
        self._undo: list[_Step] = []
        self._redo: list[_Step] = []
        self._version = 0
        self._next_version = 1
        self._saved_version = 0

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

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

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

    def _record(self, change: Change) -> None:
        """Snapshot the current state so the edit about to happen can be undone."""
        self._undo.append(_Step(self._pieces.copy(), self._size, self._version, change))
        self._redo.clear()
        self._version = self._next_version
        self._next_version += 1

    def _add(self, offset: int, data: bytes) -> None:
        """Append data to the add buffer and map [offset, offset + len) onto it."""
        where = len(self._added)
        self._added += data
        self._pieces[offset : offset + len(data)] = (ADDED, where - offset)

    def write(self, offset: int, data: bytes) -> None:
        """Overwrite bytes at ``offset``. Writing past the end extends the buffer."""
        if not data:
            return
        if offset < 0 or offset > self._size:
            raise IndexError(f"write at {offset} outside buffer of size {self._size}")
        replaced = min(len(data), self._size - offset)
        change = Change(offset, replaced, len(data))
        self._record(change)
        self._add(offset, data)
        self._size = max(self._size, offset + len(data))
        self._emit(change)

    def insert(self, offset: int, data: bytes) -> None:
        if not data:
            return
        if offset < 0 or offset > self._size:
            raise IndexError(f"insert at {offset} outside buffer of size {self._size}")
        change = Change(offset, 0, len(data))
        self._record(change)
        self._pieces.shift(offset, len(data))
        self._add(offset, data)
        self._size += len(data)
        self._emit(change)

    def delete(self, offset: int, size: int) -> None:
        size = min(size, self._size - offset)
        if size <= 0 or offset < 0:
            return
        change = Change(offset, size, 0)
        self._record(change)
        self._pieces.shift(offset, -size)
        self._size -= size
        self._emit(change)

    def undo(self) -> Optional[Change]:
        """Undo the last edit. Returns the change it made, or None if there was nothing to undo."""
        if not self._undo:
            return None
        step = self._undo.pop()
        self._redo.append(_Step(self._pieces, self._size, self._version, step.change))
        self._pieces, self._size, self._version = step.pieces, step.size, step.version
        change = step.change.inverse()
        self._emit(change)
        return change

    def redo(self) -> Optional[Change]:
        """Redo the last undone edit. Returns the change it made, or None."""
        if not self._redo:
            return None
        step = self._redo.pop()
        self._undo.append(_Step(self._pieces, self._size, self._version, step.change))
        self._pieces, self._size, self._version = step.pieces, step.size, step.version
        self._emit(step.change)
        return step.change

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
        # history points into the old source, which is gone now
        self._undo.clear()
        self._redo.clear()
        self._saved_version = self._version
        self._emit(Change(0, self._size, self._size))

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
    """A fixed-size view of part of another buffer.

    Overwrites pass straight through to the parent. If the parent is resized
    in a way that moves the window's bytes around, the window follows; if the
    resize happens inside the window it can't know where its bytes went, so it
    becomes invalid.
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
    def root(self) -> Buffer:
        return self.parent.root

    def read(self, offset: int, size: int) -> bytes:
        size = min(size, self._size - offset)
        if offset < 0 or size <= 0:
            return b""
        return self.parent.read(self.start + offset, size)

    def write(self, offset: int, data: bytes) -> None:
        if offset < 0 or offset + len(data) > self._size:
            raise ResizeError("can't write past the end of a fixed-size region")
        self.parent.write(self.start + offset, data)

    def edits(self, start: int, stop: int) -> list[Tuple[int, int]]:
        stop = min(stop, self._size)
        edits = self.parent.edits(self.start + start, self.start + stop)
        return [(lo - self.start, hi - self.start) for lo, hi in edits]

    def _on_parent_change(self, change: Change) -> None:
        end = self.start + self._size
        if change.delta == 0:
            lo = max(change.offset, self.start)
            hi = min(change.offset + change.removed, end)
            if lo < hi:
                self._emit(Change(lo - self.start, hi - lo, hi - lo))
        elif change.offset + change.removed <= self.start:
            self.start += change.delta
        elif change.offset >= end:
            pass
        else:
            self.valid = False
            self._emit(Change(0, self._size, self._size))
