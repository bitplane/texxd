"""Tests for column rendering logic."""

from rich.segment import Segment, Style
from typing import Optional

from texxd.columns.hex import HexColumn
from texxd.columns.ascii import AsciiColumn


class MockHexColumn(HexColumn):
    """A mock HexColumn for testing _render_hex_line_segments."""

    def _get_line_data(self, file_offset: int) -> bytes:
        # Mock data for testing
        return b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"

    def _apply_highlighting(self, data: bytes, file_offset: int) -> list[Optional[Style]]:
        # Mock highlighting - no highlighting for now
        return [None] * len(data)


def test_render_hex_line_segments():
    """Test _render_hex_line_segments method."""
    column = MockHexColumn(bytes_per_line=16)
    data = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
    file_offset = 0
    styles = [None] * len(data)

    segments = column._render_hex_line_segments(data, file_offset, styles)

    # Expected segments for 16 bytes
    expected_segments = [
        Segment("00"),
        Segment(" "),
        Segment("01"),
        Segment(" "),
        Segment("02"),
        Segment(" "),
        Segment("03"),
        Segment(" "),
        Segment("04"),
        Segment(" "),
        Segment("05"),
        Segment(" "),
        Segment("06"),
        Segment(" "),
        Segment("07"),
        Segment(" "),
        Segment(" "),  # Extra space after 8 bytes
        Segment("08"),
        Segment(" "),
        Segment("09"),
        Segment(" "),
        Segment("0a"),
        Segment(" "),
        Segment("0b"),
        Segment(" "),
        Segment("0c"),
        Segment(" "),
        Segment("0d"),
        Segment(" "),
        Segment("0e"),
        Segment(" "),
        Segment("0f"),
        Segment(" "),  # Trailing space
    ]

    # Compare text and style of segments
    assert len(segments) == len(expected_segments)
    for i in range(len(segments)):
        assert segments[i].text == expected_segments[i].text
        assert segments[i].style == expected_segments[i].style


class MockAsciiColumn(AsciiColumn):
    """A mock AsciiColumn for testing _render_ascii_line_segments."""

    def _get_line_data(self, file_offset: int) -> bytes:
        # Mock data for testing
        return b"ABCDEFGHIJKLMNO\x00"

    def _apply_highlighting(self, data: bytes, file_offset: int) -> list[Optional[Style]]:
        # Mock highlighting - no highlighting for now
        return [None] * len(data)


def test_render_ascii_line_segments():
    """Test _render_ascii_line_segments method."""
    column = MockAsciiColumn(bytes_per_line=16)
    data = b"ABCDEFGHIJKLMNO\x00"
    file_offset = 0
    styles = [None] * len(data)

    segments = column._render_ascii_line_segments(data, file_offset, styles)

    # Expected segments for 16 bytes
    expected_segments = [
        Segment("A"),
        Segment("B"),
        Segment("C"),
        Segment("D"),
        Segment("E"),
        Segment("F"),
        Segment("G"),
        Segment("H"),
        Segment("I"),
        Segment("J"),
        Segment("K"),
        Segment("L"),
        Segment("M"),
        Segment("N"),
        Segment("O"),
        Segment("."),
    ]

    # Compare text and style of segments
    assert len(segments) == len(expected_segments)
    for i in range(len(segments)):
        assert segments[i].text == expected_segments[i].text
        assert segments[i].style == expected_segments[i].style
