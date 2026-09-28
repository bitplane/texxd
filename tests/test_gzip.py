"""Tests for gzip streams, alone and nested with tars."""

import gzip
import subprocess
import tarfile

from conftest import make_tar

from texxd.document import Document
from texxd.formats import Binary, detect
from texxd.formats.gzip import Gzip
from texxd.formats.tar import Tar


def member(node, name):
    fmt = node.formats[0]
    return node.child(fmt, next(r for r in node.regions(fmt) if r.name == name))


def test_regions_and_contents(tmp_path):
    path = tmp_path / "hello.gz"
    with gzip.GzipFile(path, "wb", mtime=0) as f:  # stores the name "hello"
        f.write(b"hello world\n" * 100)
    doc = Document.open(path)
    assert detect(doc.root.data) == [Gzip, Binary]
    (region,) = doc.root.regions()
    assert region.name == "hello" and region.data_start == 16 and region.stop == path.stat().st_size
    contents = member(doc.root, "hello")
    assert contents.derived and contents.data.read(0, 12) == b"hello world\n"


def test_several_members(tmp_path):
    path = tmp_path / "two.gz"
    path.write_bytes(gzip.compress(b"one", mtime=0) + gzip.compress(b"two", mtime=0))
    doc = Document.open(path)
    regions = doc.root.regions()
    assert len(regions) == 2 and regions[1].start == regions[0].stop
    second = doc.root.child(Gzip, regions[1])
    second.data.insert(3, b"!")
    doc.save()
    assert gzip.decompress(path.read_bytes()) == b"onetwo!"


def test_editing_inside_a_tar_gz(tmp_path):
    path = tmp_path / "t.tar.gz"
    path.write_bytes(gzip.compress(make_tar({"a.txt": b"hello", "b.txt": b"bee"}), mtime=0))
    doc = Document.open(path)
    tar = doc.root.child(Gzip, doc.root.regions()[0])
    assert tar.formats[0] is Tar
    a = member(tar, "a.txt")
    a.data.insert(5, b" there" + b"!" * 1000)
    assert doc.modified and not doc.buffer.modified
    doc.save()
    assert not doc.modified
    out = subprocess.run(["tar", "-xzOf", str(path), "a.txt"], capture_output=True)
    assert out.returncode == 0 and out.stdout == b"hello there" + b"!" * 1000
    assert subprocess.run(["tar", "-xzOf", str(path), "b.txt"], capture_output=True).stdout == b"bee"
    # still open and still where it was, so it can be edited and saved again
    assert a.valid and a.data.read(0, 5) == b"hello"
    a.data.write(0, b"J")
    doc.save()
    with tarfile.open(path) as archive:
        assert archive.extractfile("a.txt").read(5) == b"Jello"


def test_gzip_inside_a_tar(tmp_path):
    path = tmp_path / "t.tar"
    path.write_bytes(make_tar({"x.gz": gzip.compress(b"x" * 10, mtime=0), "z.txt": b"zed"}))
    doc = Document.open(path)
    gz = member(doc.root, "x.gz")
    contents = gz.child(Gzip, gz.regions()[0])
    contents.data.insert(0, bytes(range(256)) * 20)  # won't compress, so the member grows
    doc.save()
    with tarfile.open(path) as archive:
        assert gzip.decompress(archive.extractfile("x.gz").read()) == bytes(range(256)) * 20 + b"x" * 10
        assert archive.extractfile("z.txt").read() == b"zed"
    # undo is gone after saving, but a commit can be undone before it
    contents.data.write(0, b"\xff")
    doc.commit()
    doc.undo()
    assert contents.data.modified and doc.buffer.read(0, doc.buffer.size) == path.read_bytes()


def test_truncated_stream_gives_no_regions(tmp_path):
    data = gzip.compress(b"abc" * 1000, mtime=0)
    path = tmp_path / "cut.gz"
    path.write_bytes(data[: len(data) // 2])
    doc = Document.open(path)
    assert doc.root.regions(Gzip) == []
