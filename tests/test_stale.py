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
