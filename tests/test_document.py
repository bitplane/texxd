"""Tests for documents: shared history, derived data and saving."""

import pytest
from conftest import Reversed

from texxd.document import Document


@pytest.fixture
def rev(tmp_path):
    path = tmp_path / "rev.bin"
    path.write_bytes(b"REV" + b"olleh")
    return path


def open_payload(path):
    doc = Document.open(path)
    return doc, doc.root.child(Reversed, doc.root.regions(Reversed)[0])


def test_derived_edits_are_the_documents(rev):
    doc, payload = open_payload(rev)
    assert payload.derived and payload.data.read(0, 5) == b"hello"
    payload.data.write(0, b"J")
    assert doc.modified and not doc.buffer.modified
    # one history for the whole document
    buffer, change = doc.undo()
    assert buffer is payload.data and change.offset == 0
    assert not doc.modified and payload.data.read(0, 5) == b"hello"
    doc.redo()
    assert payload.data.read(0, 5) == b"Jello"


def test_save_commits_derived_edits(rev):
    doc, payload = open_payload(rev)
    payload.data.insert(5, b" world")
    doc.save()
    assert rev.read_bytes() == b"REV" + b"dlrow olleh"
    assert not doc.modified and not doc.history.can_undo
    assert payload.valid and payload.region.data_size == 11


def test_undoing_a_commit_leaves_the_edits_uncommitted(rev):
    doc, payload = open_payload(rev)
    payload.data.write(0, b"J")
    doc.commit()
    assert doc.buffer.read(0, 8) == b"REVolleJ"
    assert not payload.data.modified and doc.buffer.modified
    doc.undo()
    assert doc.buffer.read(0, 8) == b"REVolleh"
    assert payload.data.modified and payload.data.read(0, 5) == b"Jello"
    doc.redo()
    assert doc.buffer.read(0, 8) == b"REVolleJ" and not payload.data.modified


def test_nested_derived_data_commits_innermost_first(tmp_path):
    path = tmp_path / "rev.bin"
    path.write_bytes(b"REV" + (b"REV" + b"abc")[::-1])
    doc = Document.open(path)
    middle = doc.root.child(Reversed, doc.root.regions(Reversed)[0])
    assert middle.data.read(0, 6) == b"REVabc"
    inner = middle.child(Reversed, middle.regions(Reversed)[0])
    assert inner.data.read(0, 3) == b"cba"
    inner.data.insert(0, b"z")
    doc.save()
    assert middle.data.read(0, 10) == b"REVabcz"
    assert path.read_bytes() == b"REV" + b"zcbaVER"
    assert not doc.modified


def test_new_document(tmp_path):
    path = tmp_path / "new.bin"
    doc = Document.open(path)
    assert doc.name == "new.bin" and doc.buffer.size == 0
    doc.buffer.insert(0, b"hi")
    doc.save()
    assert path.read_bytes() == b"hi"
    with pytest.raises(ValueError):
        Document.open(None).save()
