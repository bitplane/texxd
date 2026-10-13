"""Read-only byte sources that buffers are built on."""

import os
import tempfile
import weakref
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO, Optional


class Source:
    """Something with a size that bytes can be read from by offset."""

    @property
    def size(self) -> int:
        raise NotImplementedError()

    def read(self, offset: int, size: int) -> bytes:
        """Read up to ``size`` bytes starting at ``offset``."""
        raise NotImplementedError()

    def close(self) -> None:
        """Release any resources."""


class TemporarySource(Source):
    """Immutable streamed output, kept on disk until its last owner releases it.

    Buffers and history snapshots share references to this source. The finalizer
    closes its temporary file when all those references are gone. Large output
    belongs in ~/tmp rather than the system temporary directory (often tmpfs).
    """

    def __init__(self, chunks: Iterable[bytes], directory: Path | None = None):
        directory = directory if directory is not None else Path.home() / "tmp"
        directory.mkdir(parents=True, exist_ok=True)
        file = tempfile.TemporaryFile(mode="w+b", dir=directory)  # noqa: SIM115 - owned by the source
        try:
            for chunk in chunks:
                file.write(chunk)
            self._size = file.tell()
            file.flush()
        except BaseException:
            file.close()
            raise
        self._file = file
        self._cleanup = weakref.finalize(self, file.close)

    @property
    def size(self) -> int:
        return self._size

    def read(self, offset: int, size: int) -> bytes:
        self._file.seek(offset)
        return self._file.read(size)

    def close(self) -> None:
        """Close explicitly only when the source has no other owners."""
        self._cleanup()


class BytesSource(Source):
    """A source backed by bytes in memory."""

    def __init__(self, data: bytes = b""):
        self._data = bytes(data)

    @property
    def size(self) -> int:
        return len(self._data)

    def read(self, offset: int, size: int) -> bytes:
        return self._data[offset : offset + size]


class FileSource(Source):
    """A source backed by a file on disk, read lazily so large files are fine."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.writable = os.access(self.path, os.W_OK)
        # unbuffered, so reads see changes written through other handles
        self._file: Optional[BinaryIO] = open(self.path, "rb", buffering=0)
        self._size = os.fstat(self._file.fileno()).st_size

    @property
    def size(self) -> int:
        return self._size

    def read(self, offset: int, size: int) -> bytes:
        if self._file is None:
            raise ValueError("read from closed FileSource")
        self._file.seek(offset)
        return self._file.read(size)

    def refresh(self) -> None:
        """Re-read the size after the file was changed in place."""
        self._size = os.fstat(self._file.fileno()).st_size

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
