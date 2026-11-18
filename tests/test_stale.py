"""Tests for stale encoded bytes: locking, decoding again, discarding."""

import gzip

import pytest
from conftest import Reversed

from texxd.document import Document
from texxd.formats.gzip import Gzip
from texxd.node import StaleError


@pytest.fixture
def rev(tmp_path):
    path = tmp_path / "rev.bin"
    path.write_bytes(b"REV" + b"olleh")
    return path


def open_payload(path):
    doc = Document.open(path)
    return doc, doc.root.child(Reversed, doc.root.regions(Reversed)[0])


def test_editing_stale_bytes_is_refused(rev):
    doc, payload = open_payload(rev)
    payload.data.write(0, b"J")
    assert doc.stale() == [payload]
    with pytest.raises(StaleError):
        doc.buffer.write(4, b"x")
    with pytest.raises(StaleError):
        doc.buffer.insert(5, b"x")
    assert doc.buffer.read(0, 8) == b"REVolleh" and payload.data.read(0, 5) == b"Jello"
    # the edges and the header are fine
    doc.buffer.write(0, b"R")
    doc.buffer.insert(8, b"!")
    doc.commit()
    assert doc.stale() == [] and doc.buffer.read(0, 9) == b"REVolleJ!"


def test_raw_edits_decode_again(rev):
    doc, payload = open_payload(rev)
    doc.buffer.write(3, b"y")
    assert payload.data.read(0, 5) == b"helly" and not payload.data.modified
    doc.undo()
    assert payload.data.read(0, 5) == b"hello"
    doc.redo()
    assert payload.data.read(0, 5) == b"helly"
    # an edit after that is still undone properly, back through the decoding
    payload.data.write(0, b"J")
    doc.commit()
    assert doc.buffer.read(0, 8) == b"REVylleJ"
    doc.undo()
    doc.undo()
    doc.undo()
    assert payload.data.read(0, 5) == b"hello" and doc.buffer.read(0, 8) == b"REVolleh"
    assert not doc.modified


def test_discard(rev):
    doc, payload = open_payload(rev)
    payload.data.insert(5, b" there")
    payload.discard()
    assert payload.data.read(0, 20) == b"hello" and not doc.modified
    doc.undo()
    assert payload.data.read(0, 20) == b"hello there" and doc.stale() == [payload]


def test_undecodable_bytes_close_derived_data(tmp_path):
    path = tmp_path / "x.gz"
    path.write_bytes(gzip.compress(b"hello" * 100, mtime=0))
    doc = Document.open(path)
    contents = doc.root.child(Gzip, doc.root.regions()[0])
    doc.buffer.write(10, b"\xff")  # a reserved block type
    assert not contents.valid


@pytest.mark.parametrize("explicit_commit", [False, True])
def test_nested_edit_and_alternative_decode(tmp_path, monkeypatch, explicit_commit):
    class Alternative(Reversed):
        pass

    path = tmp_path / "nested"
    raw = b"REV" + b"REVolleh"[::-1]
    path.write_bytes(raw)
    doc = Document.open(path)

    def opened(parent, fmt):
        return parent.child(fmt, parent.regions(fmt)[0])

    outer = opened(doc.root, Reversed)
    inner = opened(outer, Reversed)
    calls = []

    def encode(data, region, contents):
        calls.append(contents.read(0, contents.size))
        return calls[-1][::-1]

    monkeypatch.setattr(Reversed, "encode", staticmethod(encode))
    inner.data.write(0, b"J")
    other = opened(doc.root, Alternative)
    assert other.data.read(0, 8) == b"REVolleh"
    assert list(doc.root.stale_ranges(0, len(raw))) == [(3, len(raw), inner, outer)]
    assert list(other.stale_ranges(0, 8)) == [(0, 8, inner, outer)]
    assert not list(inner.stale_ranges(0, 5))
    for data, at in [(doc.buffer, 4), (outer.data, 4), (other.data, other.data.size)]:
        with pytest.raises(StaleError) as caught:
            data.insert(at, b"x")
        assert caught.value.node is inner
    doc.undo()
    assert not doc.modified and not list(other.stale_ranges(0, 8))
    doc.redo()
    assert inner.data.read(0, 5) == b"Jello"
    assert path.read_bytes() == raw and not calls
    if explicit_commit:
        with pytest.raises(StaleError) as conflict:
            other.data.write(0, b"X")
        conflict.value.commit()
        assert not doc.stale() and path.read_bytes() == raw
        assert other.data.read(0, 8) == b"REVolleJ"
    doc.save()
    assert path.read_bytes() == b"REV" + b"REVolleJ"[::-1]
    assert calls == [b"Jello", b"REVolleJ"]
    assert other.data.read(0, 8) == b"REVolleJ"
    doc.close()


def test_pending_edits_to_an_empty_span_are_reported(tmp_path):
    from texxd.formats.text import Text

    path = tmp_path / "empty"
    path.write_bytes(b"")
    doc = Document.open(path)
    latin = Text.using("latin-1")
    text = doc.root.child(latin, latin.regions(doc.root.data)[0])
    text.data.insert(0, b"hi")
    assert list(doc.root.stale_ranges(0, 0)) == [(0, 0, text, text)]
    doc.close()


def test_disjoint_gzip_members_can_both_be_edited(tmp_path):
    path = tmp_path / "pair.gz"
    path.write_bytes(gzip.compress(b"one") + gzip.compress(b"two"))
    doc = Document.open(path)
    left, right = [doc.root.child(Gzip, region) for region in doc.root.regions(Gzip)]
    left.data.insert(0, b"first ")
    assert not list(right.stale_ranges(0, 3))
    right.data.insert(0, b"second ")
    doc.save()
    assert gzip.decompress(path.read_bytes()) == b"first onesecond two"
    doc.close()


def test_cutting_a_window_cannot_orphan_pending_edits(tmp_path):
    from texxd.formats import Format, Region

    path = tmp_path / "window"
    path.write_bytes(b"..REVolleh..")
    doc = Document.open(path)
    window = doc.root.child(Format, Region("member", 2, 10, 2, 8, openable=True))
    child = window.child(Reversed, window.regions(Reversed)[0])
    child.data.write(0, b"J")
    # These bytes precede the payload, but removing them cuts the member's edge.
    with pytest.raises(StaleError):
        doc.buffer.delete(1, 2)
    assert child.valid and doc.modified
    doc.undo()
    doc.buffer.delete(1, 2)
    assert not window.valid
    doc.close()
