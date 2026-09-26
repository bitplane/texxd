"""File-like adapter so stdlib parsers (tarfile etc) can read a Data."""

from io import RawIOBase

from .buffer import Data


class DataReader(RawIOBase):
    """A read-only, seekable file object over a Data."""

    def __init__(self, data: Data):
        super().__init__()
        self._data = data
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 1:
            offset += self._position
        elif whence == 2:
            offset += self._data.size
        self._position = max(0, offset)
        return self._position

    def readinto(self, b) -> int:
        chunk = self._data.read(self._position, len(b))
        b[: len(chunk)] = chunk
        self._position += len(chunk)
        return len(chunk)
