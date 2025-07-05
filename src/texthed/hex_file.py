"""File-like object with memory view and write buffer overlay."""

from io import RawIOBase
from typing import Dict, Tuple
from .log import get_logger

logger = get_logger(__name__)


class HexFile(RawIOBase):
    """A file-like object that wraps a file with memory view and write buffer."""

    def __init__(self, file: RawIOBase):
        self._file = file
        self._write_buffer: Dict[int, bytes] = {}
        self._position = 0
        self._file_size = self._get_file_size()

    def _get_file_size(self) -> int:
        """Get the size of the underlying file."""
        current_pos = self._file.tell()
        self._file.seek(0, 2)  # Seek to end
        size = self._file.tell()
        self._file.seek(current_pos)  # Restore position
        return size

    @property
    def size(self) -> int:
        """Get the current size of the file including unsaved changes."""
        return self._file_size

    def tell(self) -> int:
        """Get current position."""
        return self._position

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to position."""
        if whence == 0:  # SEEK_SET
            self._position = offset
        elif whence == 1:  # SEEK_CUR
            self._position += offset
        elif whence == 2:  # SEEK_END
            self._position = self._file_size + offset

        # Clamp position to valid range (allow seeking beyond end for writes)
        self._position = max(0, self._position)
        return self._position

    def readable(self) -> bool:
        """Return True if the stream can be read from."""
        return True

    def readinto(self, b: bytearray) -> int:
        """Read bytes into a pre-allocated bytearray."""
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def read(self, size: int = -1) -> bytes:
        """Read bytes from current position."""
        if size == -1:
            size = self._file_size - self._position

        if size <= 0:
            return b""

        # Calculate how much data we can actually read
        max_read_size = self._file_size - self._position
        actual_read_size = min(size, max_read_size)

        if actual_read_size <= 0:
            return b""

        # Read from original file up to its actual size
        self._file.seek(self._position)
        original_file_size = self._get_file_size()
        bytes_to_read_from_file = min(actual_read_size, max(0, original_file_size - self._position))
        original_data = self._file.read(bytes_to_read_from_file)

        # Create result buffer, extending with zeros if we're reading beyond original file
        result = bytearray(original_data)
        if len(result) < actual_read_size:
            result.extend(b"\x00" * (actual_read_size - len(result)))

        # Apply write buffer overlays
        for buf_offset, buf_data in self._write_buffer.items():
            # Check if this buffer entry overlaps with our read range
            read_start = self._position
            read_end = self._position + len(result)
            buf_end = buf_offset + len(buf_data)

            # Skip if no overlap
            if buf_end <= read_start or buf_offset >= read_end:
                continue

            # Calculate overlap region
            overlap_start = max(read_start, buf_offset)
            overlap_end = min(read_end, buf_end)

            # Apply overlay
            result_start = overlap_start - read_start
            result_end = overlap_end - read_start
            buf_start = overlap_start - buf_offset
            buf_end_slice = buf_start + (overlap_end - overlap_start)

            result[result_start:result_end] = buf_data[buf_start:buf_end_slice]

        self._position += len(result)
        return bytes(result)

    def writable(self) -> bool:
        """Return True if the stream can be written to."""
        return True

    def write(self, data: bytes) -> int:
        """Write bytes to buffer at current position."""
        if not data:
            return 0

        # Store in write buffer
        self._write_buffer[self._position] = data
        bytes_written = len(data)
        self._position += bytes_written

        # Extend file size if we wrote past end
        if self._position > self._file_size:
            self._file_size = self._position

        return bytes_written

    def has_unsaved_changes(self) -> bool:
        """Check if there are unsaved changes."""
        return len(self._write_buffer) > 0

    def get_unsaved_ranges(self) -> list[Tuple[int, int]]:
        """Get list of (start, end) tuples for unsaved byte ranges."""
        ranges = []
        for offset, data in self._write_buffer.items():
            ranges.append((offset, offset + len(data)))
        return sorted(ranges)

    def save(self) -> None:
        """Save all changes to the underlying file."""
        if not self._write_buffer:
            return

        # Apply all writes to file
        original_pos = self._file.tell()

        for offset, data in sorted(self._write_buffer.items()):
            self._file.seek(offset)
            self._file.write(data)

        # Restore position and clear buffer
        self._file.seek(original_pos)
        self._write_buffer.clear()

        # Update file size
        self._file_size = self._get_file_size()

    def revert(self) -> None:
        """Discard all unsaved changes."""
        self._write_buffer.clear()
        self._file_size = self._get_file_size()

    def close(self) -> None:
        """Close the underlying file."""
        self._file.close()
