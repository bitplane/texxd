"""Tests for text: encodings, the text format, and laying text out in rows."""

import random
import tarfile
import time

import pytest

from texxd.data import Buffer, BytesSource, EditError, Window
from texxd.document import Document
from texxd.formats import detect
from texxd.formats.tar import Tar
from texxd.formats.text import Text
from texxd.formats.text import detect as detect_encoding
from texxd.view.text import TextRows, tokens


def data(raw: bytes) -> Buffer:
    return Buffer(BytesSource(raw))


@pytest.mark.parametrize(
    "raw, encoding",
    [
        (b"plain ascii\n", "utf-8"),
        ("naïve café\n".encode(), "utf-8"),
        (b"\xef\xbb\xbfwith a bom", "utf-8"),
        ("hi".encode("utf-16"), "utf-16-le"),
        (b"\xfe\xff\x00h\x00i", "utf-16-be"),
        ("Grüße aus München, schöne Straße. ".encode("latin-1") * 20, "cp1252"),
    ],
)
def test_detects_encodings(raw, encoding):
    assert detect_encoding(data(raw))[0] == encoding
    assert Text in detect(data(raw))


@pytest.mark.parametrize("raw", [b"", b"\x00\x01\x02\x03", bytes(range(256)) * 4, b"text\x00with a null"])
def test_binary_isnt_text(raw):
    assert detect_encoding(data(raw)) is None
    assert Text not in detect(data(raw))


def test_regions_skip_the_bom():
    (region,) = Text.regions(data(b"\xef\xbb\xbfhello"))
    assert (region.name, region.data_start, region.data_size, region.value) == ("text (utf-8)", 3, 5, "utf-8")
    # read as something else, the BOM is just bytes
    (region,) = Text.using("latin-1").regions(data(b"\xef\xbb\xbfhello"))
    assert region.data_start == 0 and region.name == "text (iso8859-1)"


def test_utf8_opens_in_place_and_other_encodings_are_decoded(tmp_path):
    path = tmp_path / "t.txt"
    path.write_bytes("café\n".encode())
    doc = Document.open(path)
    text = doc.root.child(Text, doc.root.regions(Text)[0])
    assert text.fmt is Text.using("utf-8") and isinstance(text.data, Window) and not text.derived
    # text isn't offered as text again
    assert Text not in text.formats

    path.write_bytes("café\n".encode("latin-1"))
    doc = Document.open(path)
    text = doc.root.child(Text.using("latin-1"), Text.using("latin-1").regions(doc.root.data)[0])
    assert text.derived and text.data.read(0, 10) == "café\n".encode()


def test_bytes_that_dont_decode_survive_editing(tmp_path):
    path = tmp_path / "t.txt"
    path.write_bytes(b"a\x81b\n")  # 0x81 isn't a cp1252 character
    doc = Document.open(path)
    fmt = Text.using("cp1252")
    text = doc.root.child(fmt, fmt.regions(doc.root.data)[0])
    text.data.insert(0, "€".encode())
    doc.save()
    assert path.read_bytes() == b"\x80a\x81b\n"


def test_characters_the_encoding_cant_hold(tmp_path):
    path = tmp_path / "t.txt"
    path.write_bytes(b"abc")
    doc = Document.open(path)
    fmt = Text.using("latin-1")
    text = doc.root.child(fmt, fmt.regions(doc.root.data)[0])
    text.data.insert(0, "日".encode())
    with pytest.raises(EditError):
        doc.save()
    assert path.read_bytes() == b"abc" and text.stale


def test_text_in_a_tar_resizes_the_member(nested_tar):
    doc = Document.open(nested_tar)
    readme = doc.root.child(Tar, next(r for r in doc.root.regions() if r.name == "README"))
    text = readme.child(Text, readme.regions(Text)[0])
    text.data.insert(0, "Please ".encode())
    doc.save()

    with tarfile.open(nested_tar) as archive:
        assert archive.extractfile("README").read() == b"Please read me\n"


def test_tokens():
    found = [(t.start, t.stop, t.text, t.width) for t in tokens("a\té日\r\n\xff".encode("utf-8") + b"\xff")]
    assert found[:5] == [(0, 1, "a", 1), (1, 2, "   ", 3), (2, 4, "é", 1), (4, 7, "日", 2), (7, 9, "\n", 0)]
    # "\xff" as a str is ÿ, two bytes in UTF-8; the lone 0xff byte on the end isn't UTF-8 at all
    assert found[5][2] == "ÿ" and found[6][2] == "·"


def test_rows_wrap_lines():
    rows = TextRows(data(b"short\n" + b"x" * 25 + b"\n\nend"), width=10, limit=100)
    assert rows.starts == [0, 6, 16, 26, 32, 33]
    assert rows.lines == [0, 1, 1, 1, 2, 3]
    assert rows.stop(0) == 6 and rows.stop(5) == 36
    # the end of a line is its line break; a wrapped row ends on its last character
    assert rows.end_of(0) == 5 and rows.end_of(1) == 15 and rows.end_of(3) == 31
    assert rows.end_of(5) == 36


def test_rows_end_with_an_empty_line_after_a_final_break():
    rows = TextRows(data(b"a\nb\n"), width=10, limit=4)
    assert rows.starts == [0, 2, 4]
    assert TextRows(data(b""), width=10, limit=0).starts == [0]


def test_wide_characters_wrap_by_cells():
    rows = TextRows(data("日本語のテキスト".encode()), width=5, limit=100)
    # two cells each: two to a row
    assert rows.starts == [0, 6, 12, 18]


def test_moving_by_character():
    raw = "aé日\r\nz".encode()
    rows = TextRows(data(raw), width=80, limit=len(raw))
    positions = [0]
    while positions[-1] < len(raw):
        positions.append(rows.step(positions[-1], 1))
    assert positions == [0, 1, 3, 6, 8, 9]
    assert [rows.step(p, -1) for p in positions[1:]] == positions[:-1]
    assert rows.x_of(6) == 4 and rows.position_at(0, 2) == 3 and rows.position_at(0, 99) == 6
    assert rows.newline == b"\r\n"


def layout(rows):
    return list(zip(rows.starts, rows.lines))


@pytest.mark.parametrize("seed", range(20))
def test_following_edits_matches_laying_out_again(seed):
    rng = random.Random(seed)
    pieces = [b"\n", b"\r\n", b"\t", b" ", b"word", b"x" * 30, "日本".encode(), b"\xff", "é".encode()]
    buffer = data(b"".join(rng.choice(pieces) for _ in range(200)))
    rows = TextRows(buffer, width=rng.choice([5, 12, 40]), limit=0)
    buffer.subscribe(rows.update)
    for _ in range(150):
        offset = rng.randint(0, buffer.size)
        roll = rng.random()
        if roll < 0.4:
            buffer.insert(offset, b"".join(rng.choice(pieces) for _ in range(rng.randint(1, 4))))
        elif roll < 0.8:
            buffer.delete(offset, rng.randint(1, 12))
        elif roll < 0.9:
            buffer.write(offset, rng.choice(pieces))
        else:
            buffer.undo()
        fresh = TextRows(buffer, rows.width, 0)
        assert layout(rows) == layout(fresh)
        assert rows.count == fresh.count and rows.stop(rows.count - 1) == buffer.size


def test_edits_in_a_big_file_are_fast():
    line = b"The quick brown fox jumps over the lazy dog, again and again.\n"
    buffer = data(line * 200_000)  # 12MB, 200k lines
    rows = TextRows(buffer, width=40, limit=buffer.size)
    buffer.subscribe(rows.update)
    started = time.perf_counter()
    for i in range(100):
        buffer.insert(5_000_000 + i, b"x")
    buffer.insert(6_000_000, b"\n\n")
    assert time.perf_counter() - started < 1
    assert rows.count == TextRows(buffer, 40, buffer.size).count


@pytest.mark.parametrize("seed", range(10))
def test_fast_wrapping_matches_wrapping_a_character_at_a_time(seed):
    rng = random.Random(seed)
    pieces = ["a", "word ", "x" * 17, "\t", "日本", "é", "\x01", " " * 5, "😀"]
    rows = TextRows(data(b""), width=rng.choice([1, 2, 7, 13, 80]), limit=0)
    for _ in range(50):
        body = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 40))).encode()
        assert list(rows._wrap(100, body)) == list(rows._wrap_tokens(100, body))


def test_short_text_isnt_read_as_an_old_mac_encoding():
    # chardet guesses MacIceland for this, with next to no confidence
    assert detect_encoding(data("Grüße\n".encode("latin-1")))[0] == "cp1252"
    assert detect_encoding(data("Привет, мир\n".encode("cp1251") * 5))[0] == "cp1251"
