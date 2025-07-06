"""File-like object with memory view and write buffer overlay."""

from io import RawIOBase
from typing import Dict, Tuple, List, Optional
from rich.style import Style
from .highlighters.highlighter import Highlighter
from .log import get_logger

logger = get_logger(__name__)


class HexFile(RawIOBase, Highlighter):
    """A file-like object that wraps a file with memory view and write buffer."""

    def __init__(self, file: RawIOBase):
        RawIOBase.__init__(self)
        Highlighter.__init__(self)
        self._file = file
        self._position = 0
        self._file_size = self._get_file_size()
        self.unsaved: Dict[int, int] = {}  # pos -> byte value

        # Highlight style
        self.changed_style = Style(color="bright_red")

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

        # Apply unsaved byte overlays
        read_start = self._position
        for i in range(len(result)):
            pos = read_start + i
            if pos in self.unsaved:
                result[i] = self.unsaved[pos]

        self._position += len(result)
        return bytes(result)

    def writable(self) -> bool:
        """Return True if the stream can be written to."""
        return True

    def write(self, data: bytes) -> int:
        """Write bytes to buffer at current position."""
        if not data:
            return 0

        # Store each byte in unsaved map
        for i, byte in enumerate(data):
            self.unsaved[self._position + i] = byte

        bytes_written = len(data)
        self._position += bytes_written

        # Extend file size if we wrote past end
        if self._position > self._file_size:
            self._file_size = self._position

        return bytes_written

    def has_unsaved_changes(self) -> bool:
        """Check if there are unsaved changes."""
        return len(self.unsaved) > 0

    def get_unsaved_ranges(self) -> list[Tuple[int, int]]:
        """Get list of (start, end) tuples for unsaved byte ranges."""
        if not self.unsaved:
            return []

        # Group consecutive positions into ranges
        sorted_positions = sorted(self.unsaved.keys())
        ranges = []
        start = sorted_positions[0]
        end = start + 1

        for pos in sorted_positions[1:]:
            if pos == end:
                end = pos + 1
            else:
                ranges.append((start, end))
                start = pos
                end = pos + 1

        ranges.append((start, end))
        return ranges

    def highlight(self, data: bytes, file_offset: int, styles: List[Optional[Style]]) -> None:
        """Apply edit highlighting to the styles array."""
        if not data:
            return

        # Simple highlighting - just check if position is in unsaved
        for i in range(len(data)):
            if file_offset + i in self.unsaved:
                # Combine with existing style instead of replacing
                if styles[i] is None:
                    styles[i] = self.changed_style
                else:
                    styles[i] = styles[i] + self.changed_style

    def flush(self) -> None:
        """Flush all changes to the underlying file."""
        if not self.unsaved:
            return

        # Save current position
        original_pos = self._file.tell()

        # Write each byte
        for pos in sorted(self.unsaved.keys()):
            self._file.seek(pos)
            self._file.write(bytes([self.unsaved[pos]]))

        # Restore position and clear unsaved
        self._file.seek(original_pos)
        self.unsaved.clear()

        # Update file size
        self._file_size = self._get_file_size()

        # Flush the underlying file
        self._file.flush()

    def revert(self) -> None:
        """Discard all unsaved changes."""
        self.unsaved.clear()
        self._file_size = self._get_file_size()

    def close(self) -> None:
        """Close the underlying file."""
        self._file.close()
