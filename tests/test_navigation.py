"""Tests for navigation features."""

import pytest
from texxd.cursors.cursor import Cursor


class MockColumn:
    """Mock column for testing cursor."""

    def __init__(self, file_size):
        self.file_size = file_size


class MockHexView:
    """Mock hex view for testing cursor."""

    def __init__(self, height=10):
        self.size = type("obj", (object,), {"height": height})()


@pytest.fixture
def cursor():
    """Create a cursor for testing."""
    cursor = Cursor(bytes_per_line=16)
    cursor.parent_column = MockColumn(file_size=256)  # 16 lines of data
    cursor.hex_view = MockHexView(height=10)
    return cursor


def test_move_to_line_start(cursor):
    """Test moving to beginning of line."""
    cursor._set_position(23)  # Line 1, column 7
    cursor.move_to_line_start()
    assert cursor.position == 16  # Line 1, column 0
    assert cursor.x == 0
    assert cursor.y == 1


def test_move_to_line_end(cursor):
    """Test moving to end of line."""
    cursor._set_position(20)  # Line 1, column 4
    cursor.move_to_line_end()
    assert cursor.position == 31  # Line 1, column 15
    assert cursor.x == 15
    assert cursor.y == 1


def test_move_to_file_start(cursor):
    """Test moving to beginning of file."""
    cursor._set_position(123)
    cursor.move_to_file_start()
    assert cursor.position == 0


def test_move_to_file_end(cursor):
    """Test moving to end of file."""
    cursor._set_position(0)
    cursor.move_to_file_end()
    assert cursor.position == 255  # Last byte in 256-byte file


def test_move_word_forward(cursor):
    """Test moving forward by word."""
    cursor._set_position(10)
    cursor.move_word_forward()  # Default 4 bytes
    assert cursor.position == 14

    cursor.move_word_forward(8)  # 8 bytes
    assert cursor.position == 22


def test_move_word_backward(cursor):
    """Test moving backward by word."""
    cursor._set_position(20)
    cursor.move_word_backward()  # Default 4 bytes
    assert cursor.position == 16

    cursor.move_word_backward(8)  # 8 bytes
    assert cursor.position == 8


def test_move_word_forward_at_end(cursor):
    """Test word forward doesn't go past end."""
    cursor._set_position(254)
    cursor.move_word_forward()
    assert cursor.position == 255  # Clamped to end


def test_move_word_backward_at_start(cursor):
    """Test word backward doesn't go before start."""
    cursor._set_position(2)
    cursor.move_word_backward()
    assert cursor.position == 0  # Clamped to start


def test_move_half_page_up(cursor):
    """Test moving up by half page."""
    cursor._set_position(160)  # Line 10
    cursor.move_half_page_up()  # Half of 10 = 5 lines
    assert cursor.y == 5


def test_move_half_page_down(cursor):
    """Test moving down by half page."""
    cursor._set_position(32)  # Line 2
    cursor.move_half_page_down()  # Half of 10 = 5 lines
    assert cursor.y == 7


def test_go_to_offset(cursor):
    """Test jumping to specific offset."""
    cursor.go_to_offset(100)
    assert cursor.position == 100

    # Test clamping
    cursor.go_to_offset(1000)
    assert cursor.position == 255  # Clamped to file size - 1

    cursor.go_to_offset(-10)
    assert cursor.position == 0  # Clamped to 0


def test_set_x_bounds(cursor):
    """Test that set_x respects boundaries."""
    cursor._set_position(20)  # Line 1, column 4

    # Normal movement
    cursor.set_x(10)
    assert cursor.x == 10
    assert cursor.y == 1

    # Beyond line end
    cursor.set_x(20)
    assert cursor.x == 15  # Clamped to 15

    # Negative
    cursor.set_x(-5)
    assert cursor.x == 0  # Clamped to 0


def test_set_x_at_file_end(cursor):
    """Test set_x on last incomplete line."""
    # Create cursor with smaller file
    cursor.parent_column.file_size = 20  # 1 full line + 4 bytes
    cursor._set_position(18)  # Last line, byte 2

    cursor.set_x(10)  # Try to go to column 10
    # Should clamp to last valid position (19)
    assert cursor.position == 19


def test_home_end_keys(cursor):
    """Test that Home/End keys work as expected."""
    from textual import events

    # Start at middle of line
    cursor._set_position(20)  # Line 1, column 4

    # Test Home key
    event = events.Key(key="home", character="")
    cursor.handle_event(event)
    assert cursor.x == 0
    assert cursor.y == 1  # Same line

    # Test End key
    event = events.Key(key="end", character="")
    cursor.handle_event(event)
    assert cursor.x == 15
    assert cursor.y == 1  # Same line


def test_ctrl_home_end_keys(cursor):
    """Test that Ctrl+Home/End keys work."""
    from textual import events

    # Start at middle
    cursor._set_position(128)

    # Test Ctrl+Home
    event = events.Key(key="ctrl+home", character="")
    cursor.handle_event(event)
    assert cursor.position == 0

    # Test Ctrl+End
    event = events.Key(key="ctrl+end", character="")
    cursor.handle_event(event)
    assert cursor.position == 255


def test_ctrl_left_right_keys(cursor):
    """Test Ctrl+Left/Right for word navigation."""
    from textual import events

    cursor._set_position(20)

    # Test Ctrl+Right
    event = events.Key(key="ctrl+right", character="")
    cursor.handle_event(event)
    assert cursor.position == 24  # +4 bytes

    # Test Ctrl+Left
    event = events.Key(key="ctrl+left", character="")
    cursor.handle_event(event)
    assert cursor.position == 20  # -4 bytes


def test_ctrl_a_d_as_home_end(cursor):
    """Test that Ctrl+A/D work as Home/End alternatives."""
    from textual import events

    # Start at middle of line
    cursor._set_position(20)  # Line 1, column 4

    # Test Ctrl+A as Home
    event = events.Key(key="\x01", character="\x01")
    cursor.handle_event(event)
    assert cursor.x == 0
    assert cursor.y == 1  # Same line

    # Test Ctrl+D as End
    event = events.Key(key="\x04", character="\x04")
    cursor.handle_event(event)
    assert cursor.x == 15
    assert cursor.y == 1  # Same line
