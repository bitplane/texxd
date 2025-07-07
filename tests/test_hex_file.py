"""Tests for HexFile class."""

import pytest

from texxd.hex_file import HexFile


@pytest.fixture
def tmpdir(tmp_path):
    """Create a temporary directory for test files."""
    return tmp_path


def test_hex_file_basic_read(tmpdir):
    """Test basic reading from HexFile."""
    test_file = tmpdir / "test.bin"
    test_data = b"Hello, World!"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Test reading all data
        assert hex_file.read() == test_data

        # Test position after read
        assert hex_file.tell() == len(test_data)

        # Test size property
        assert hex_file.size == len(test_data)


def test_hex_file_seek_and_tell(tmpdir):
    """Test seeking and position tracking."""
    test_file = tmpdir / "test.bin"
    test_data = b"0123456789"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Test initial position
        assert hex_file.tell() == 0

        # Test SEEK_SET
        hex_file.seek(5)
        assert hex_file.tell() == 5
        assert hex_file.read(2) == b"56"

        # Test SEEK_CUR
        hex_file.seek(-3, 1)
        assert hex_file.tell() == 4
        assert hex_file.read(1) == b"4"

        # Test SEEK_END
        hex_file.seek(-2, 2)
        assert hex_file.tell() == 8
        assert hex_file.read(2) == b"89"


def test_hex_file_partial_read(tmpdir):
    """Test reading specific amounts of data."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDEFGHIJ"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Test reading specific size
        assert hex_file.read(3) == b"ABC"
        assert hex_file.tell() == 3

        # Test reading from middle
        hex_file.seek(5)
        assert hex_file.read(2) == b"FG"

        # Test reading beyond end
        hex_file.seek(8)
        assert hex_file.read(10) == b"IJ"


def test_hex_file_write_buffer(tmpdir):
    """Test write buffer functionality."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDEFGHIJ"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Test writing to buffer
        hex_file.seek(2)
        written = hex_file.write(b"XY")
        assert written == 2
        assert hex_file.tell() == 4

        # Test that changes are reflected in reads
        hex_file.seek(0)
        assert hex_file.read() == b"ABXYEFGHIJ"

        # Test unsaved changes detection
        assert hex_file.has_unsaved_changes()
        ranges = hex_file.get_unsaved_ranges()
        assert ranges == [(2, 4)]


def test_hex_file_overlapping_writes(tmpdir):
    """Test overlapping write operations."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDEFGHIJ"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Write first change
        hex_file.seek(2)
        hex_file.write(b"XYZ")

        # Write overlapping change
        hex_file.seek(4)
        hex_file.write(b"123")

        # Read and verify overlay behavior
        hex_file.seek(0)
        data = hex_file.read()
        assert data == b"ABXY123HIJ"


def test_hex_file_write_at_end(tmpdir):
    """Test writing past end of file."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDE"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Write past end
        hex_file.seek(5)
        hex_file.write(b"XYZ")

        # Check that size is extended
        assert hex_file.size == 8

        # Read all data
        hex_file.seek(0)
        assert hex_file.read() == b"ABCDEXYZ"


def test_hex_file_flush_changes(tmpdir):
    """Test flushing changes to underlying file."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDEFGHIJ"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Make changes
        hex_file.seek(2)
        hex_file.write(b"XY")
        hex_file.seek(7)
        hex_file.write(b"Z")

        # Flush changes
        hex_file.flush()

        # Verify no unsaved changes
        assert not hex_file.has_unsaved_changes()
        assert hex_file.get_unsaved_ranges() == []

    # Verify file was actually modified
    assert test_file.read_bytes() == b"ABXYEFGZIJ"


def test_hex_file_revert_changes(tmpdir):
    """Test reverting unsaved changes."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDEFGHIJ"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Make changes
        hex_file.seek(2)
        hex_file.write(b"XY")

        # Verify changes exist
        assert hex_file.has_unsaved_changes()
        hex_file.seek(0)
        assert hex_file.read() == b"ABXYEFGHIJ"

        # Revert changes
        hex_file.revert()

        # Verify changes are gone
        assert not hex_file.has_unsaved_changes()
        hex_file.seek(0)
        assert hex_file.read() == b"ABCDEFGHIJ"


def test_hex_file_empty_file(tmpdir):
    """Test HexFile with empty file."""
    test_file = tmpdir / "empty.bin"
    test_file.write_bytes(b"")

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        assert hex_file.size == 0
        assert hex_file.tell() == 0
        assert hex_file.read() == b""

        # Test writing to empty file
        hex_file.write(b"Hello")
        assert hex_file.size == 5
        hex_file.seek(0)
        assert hex_file.read() == b"Hello"


def test_hex_file_seek_bounds(tmpdir):
    """Test seeking beyond file bounds."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDE"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Test seeking beyond end (now allowed)
        hex_file.seek(100)
        assert hex_file.tell() == 100  # Not clamped anymore

        # Test seeking before start
        hex_file.seek(-10)
        assert hex_file.tell() == 0  # Clamped to 0


def test_hex_file_read_zero_size(tmpdir):
    """Test reading zero bytes."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDE"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Test reading zero bytes
        assert hex_file.read(0) == b""
        assert hex_file.tell() == 0  # Position unchanged


def test_hex_file_write_empty_data(tmpdir):
    """Test writing empty data."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDE"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Test writing empty bytes
        hex_file.seek(2)
        written = hex_file.write(b"")
        assert written == 0
        assert hex_file.tell() == 2  # Position unchanged
        assert not hex_file.has_unsaved_changes()


def test_hex_file_multiple_ranges(tmpdir):
    """Test multiple non-overlapping write ranges."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDEFGHIJ"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)

        # Write to multiple ranges
        hex_file.seek(1)
        hex_file.write(b"X")
        hex_file.seek(5)
        hex_file.write(b"Y")
        hex_file.seek(8)
        hex_file.write(b"Z")

        # Check unsaved ranges
        ranges = hex_file.get_unsaved_ranges()
        assert ranges == [(1, 2), (5, 6), (8, 9)]

        # Verify data
        hex_file.seek(0)
        assert hex_file.read() == b"AXCDEYGHZJ"


def test_hex_file_close(tmpdir):
    """Test closing HexFile."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDE"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)
        hex_file.close()

        # File should be closed
        with pytest.raises(ValueError):
            f.read()


def test_hex_file_readable_writable(tmpdir):
    """Test readable() and writable() methods."""
    test_file = tmpdir / "test.bin"
    test_file.write_bytes(b"ABC")

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)
        assert hex_file.readable() is True
        assert hex_file.writable() is True


def test_hex_file_readinto(tmpdir):
    """Test readinto() method."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDEFGHIJ"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)
        buffer = bytearray(5)
        bytes_read = hex_file.readinto(buffer)
        assert bytes_read == 5
        assert buffer == bytearray(b"ABCDE")
        assert hex_file.tell() == 5

        buffer = bytearray(10)
        hex_file.seek(0)
        bytes_read = hex_file.readinto(buffer)
        assert bytes_read == 10
        assert buffer == bytearray(b"ABCDEFGHIJ")


def test_hex_file_read_actual_read_size_zero(tmpdir):
    """Test read() method when actual_read_size is zero."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABC"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)
        hex_file.seek(len(test_data))
        assert hex_file.read(1) == b""
        assert hex_file.read(-1) == b""


def test_hex_file_read_no_overlap_write_buffer(tmpdir):
    """Test read() method when there's no overlap with the write buffer."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDEFGHIJ"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)
        hex_file.seek(0)
        hex_file.write(b"XX")  # Write at offset 0

        hex_file.seek(5)
        assert hex_file.read(2) == b"FG"  # Read at offset 5, no overlap

        hex_file.seek(0)
        assert hex_file.read(2) == b"XX"  # Read the written data


def test_hex_file_flush_no_changes(tmpdir):
    """Test flush() method when there are no unsaved changes."""
    test_file = tmpdir / "test.bin"
    test_data = b"ABCDEFGHIJ"
    test_file.write_bytes(test_data)

    with open(test_file, "r+b") as f:
        hex_file = HexFile(f)
        assert not hex_file.has_unsaved_changes()
        hex_file.flush()  # Should do nothing, no error
        assert not hex_file.has_unsaved_changes()
        assert test_file.read_bytes() == test_data
