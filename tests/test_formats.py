"""Tests for format detection, tar parsing and nodes."""

import subprocess
import tarfile

import pytest
from conftest import make_tar

from texxd.data import Buffer, BytesSource, ResizeError
from texxd.formats import Binary, Format, detect
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
    entries = node.regions()
    assert [(e.name, e.kind, e.openable) for e in entries] == [("dir", "dir", False), ("a.txt", "file", True)]
    a = entries[1]
    assert a.start == 512
    assert a.data_start == 1024
    assert a.data_size == 5
    assert a.stop == 1536
    assert a.info[0] == "-rw-r--r--"
    assert entries[0].info[0] == "drwxr-xr-x"
    assert node.region_at(700) is a
    assert node.region_at(10) is entries[0]


def test_truncated_tar_gives_partial_entries():
    tar = make_tar({"a.txt": b"x" * 600, "b.txt": b"y"})
    node = node_for(tar[:1100])
    names = [e.name for e in node.regions()]
    assert names in (["a.txt"], [])


def test_nested_child_nodes(nested_tar):
    root = Node.open(nested_tar)
    inner_entry = next(e for e in root.regions() if e.name == "inner.tar")
    inner = root.child(Tar, inner_entry)
    assert inner.parent is root
    assert root.child(Tar, inner_entry) is inner
    assert inner.formats[0] is Tar
    data_entry = next(e for e in inner.regions() if e.name == "data.json")
    data = inner.child(Tar, data_entry)
    assert data.data.read(0, 100) == b'{"a": 1}\n'
    assert [n.name for n in data.path] == ["outer.tar", "inner.tar", "data.json"]

    # writes land in the root buffer, at the right place
    data.data.write(1, b"'")
    offset = inner_entry.data_start + data_entry.data_start + 1
    assert root.buffer.read(offset, 1) == b"'"
    assert data.data.from_root(offset) == 1
    assert data.data.to_root(1) == offset
    assert inner.data.from_root(0) is None
    assert data.fmt is Tar and data.region == data_entry


def test_structure_reparsed_after_edit():
    node = node_for(make_tar({"a.txt": b"hello"}))
    assert node.regions()[0].name == "a.txt"
    # renaming in the header breaks its checksum, so it stops being a readable tar
    node.data.write(0, b"b")
    assert node.regions() == []
    node.buffer.undo()
    assert node.regions()[0].name == "a.txt"


def read_nested(path, inner_name="inner.tar", member="data.json"):
    """A member of a tar inside a tar, read with the standard library."""
    with tarfile.open(path) as outer:
        inner = tarfile.open(fileobj=outer.extractfile(inner_name))
        return inner.extractfile(member).read()


def read_member(path, name):
    with tarfile.open(path) as archive:
        return archive.extractfile(name).read()


def open_nested(path):
    root = Node.open(path)
    inner = root.child(Tar, next(r for r in root.regions() if r.name == "inner.tar"))
    data = inner.child(Tar, next(r for r in inner.regions() if r.name == "data.json"))
    return root, inner, data


@pytest.mark.parametrize("extra", [b"more\n", b"x" * 600, b"y" * 20000], ids=["small", "new-block", "outer-grows"])
def test_resizing_a_file_in_a_nested_tar(nested_tar, extra):
    root, inner, data = open_nested(nested_tar)
    data.data.insert(9, extra)
    assert data.data.read(0, 100) == (b'{"a": 1}\n' + extra)[:100]
    assert data.valid and inner.valid
    assert data.region.data_size == 9 + len(extra)
    # headers and padding at both levels were fixed up, as one undoable step
    root.buffer.save()
    assert read_nested(nested_tar) == b'{"a": 1}\n' + extra
    out = subprocess.run(
        ["tar", "-xOf", "-", "data.json"], input=read_member(nested_tar, "inner.tar"), capture_output=True
    )
    assert out.returncode == 0 and out.stdout == b'{"a": 1}\n' + extra
    with tarfile.open(nested_tar) as outer:
        assert outer.extractfile("README").read() == b"read me\n"


def test_shrinking_and_undo(nested_tar):
    root, inner, data = open_nested(nested_tar)
    original = root.data.read(0, root.data.size)
    data.data.delete(0, 5)
    assert data.data.read(0, 100) == b" 1}\n"
    assert root.buffer.undo() is not None
    assert root.data.read(0, root.data.size) == original
    assert not root.buffer.can_undo
    assert data.data.read(0, 100) == b'{"a": 1}\n'
    root.buffer.redo()
    root.buffer.save()
    assert read_nested(nested_tar) == b" 1}\n"


def test_formats_that_cant_resize_roll_back(nested_tar, monkeypatch):
    monkeypatch.setattr(Tar, "can_resize", False)
    monkeypatch.setattr(Tar, "fixup", classmethod(Format.fixup.__func__))
    root, inner, data = open_nested(nested_tar)
    assert not data.resizable
    original = root.data.read(0, root.data.size)
    with pytest.raises(ResizeError):
        data.data.insert(0, b"x")
    assert root.data.read(0, root.data.size) == original
    assert not root.buffer.modified


def test_raw_edits_in_the_container_are_left_alone(nested_tar):
    root, inner, data = open_nested(nested_tar)
    header = inner.data.read(0, 512)
    # inserting into data.json's bytes from inner.tar's level is a raw edit: no fixup
    inner.data.insert(data.region.data_start, b"x")
    assert inner.data.read(0, 512) == header
    assert data.data.read(0, 3) == b'{"a'  # an insert at its start, not through it, isn't its


def test_resizing_again_after_undo(tmp_path):
    # the header is what says how big a member is, so undo can't leave a stale idea of it
    path = tmp_path / "t.tar"
    path.write_bytes(make_tar({"a.txt": b"hello", "b.txt": b"bee"}))
    root = Node.open(path)
    a = root.child(Tar, root.regions()[0])
    a.data.insert(5, b"x" * 600)
    root.buffer.undo()
    a.data.insert(5, b"!")
    root.buffer.save()
    assert read_member(path, "a.txt") == b"hello!"
    assert read_member(path, "b.txt") == b"bee"
