"""Tests for format detection, tar parsing and nodes."""

from conftest import make_tar

from texxd.data import Buffer, BytesSource
from texxd.formats import Binary, detect
from texxd.formats.tar import Tar
from texxd.node import Node


def node_for(data: bytes) -> Node:
    return Node("test", Buffer(BytesSource(data)))


def test_binary_is_always_last():
    assert detect(Buffer(BytesSource(b"hello"))) == [Binary]
    assert detect(Buffer()) == [Binary]


def test_detects_tar():
    tar = make_tar({"a.txt": b"hi"})
    assert detect(Buffer(BytesSource(tar))) == [Tar, Binary]


def test_tar_entries():
    node = node_for(make_tar({"dir": None, "a.txt": b"hello"}))
    entries = node.entries()
    assert [(e.name, e.kind, e.openable) for e in entries] == [("dir", "dir", False), ("a.txt", "file", True)]
    a = entries[1]
    assert a.start == 512
    assert a.data_start == 1024
    assert a.data_size == 5
    assert a.stop == 1536
    assert a.info[0] == "-rw-r--r--"
    assert entries[0].info[0] == "drwxr-xr-x"
    assert node.entry_at(700) is a
    assert node.entry_at(10) is entries[0]


def test_truncated_tar_gives_partial_entries():
    tar = make_tar({"a.txt": b"x" * 600, "b.txt": b"y"})
    node = node_for(tar[:1100])
    names = [e.name for e in node.entries()]
    assert names in (["a.txt"], [])


def test_nested_child_nodes(nested_tar):
    root = Node.open(nested_tar)
    inner_entry = next(e for e in root.entries() if e.name == "inner.tar")
    inner = root.child(inner_entry)
    assert inner.parent is root
    assert root.child(inner_entry) is inner
    assert inner.formats[0] is Tar
    data_entry = next(e for e in inner.entries() if e.name == "data.json")
    data = inner.child(data_entry)
    assert data.data.read(0, 100) == b'{"a": 1}\n'
    assert [n.name for n in data.path] == ["outer.tar", "inner.tar", "data.json"]

    # writes land in the root buffer, at the right place
    data.data.write(1, b"'")
    offset = inner_entry.data_start + data_entry.data_start + 1
    assert root.buffer.read(offset, 1) == b"'"
    assert data.local_offset(offset) == 1
    assert inner.local_offset(0) is None


def test_structure_reparsed_after_edit():
    node = node_for(make_tar({"a.txt": b"hello"}))
    assert node.entries()[0].name == "a.txt"
    # renaming in the header breaks its checksum, so it stops being a readable tar
    node.data.write(0, b"b")
    assert node.entries() == []
    node.buffer.undo()
    assert node.entries()[0].name == "a.txt"
