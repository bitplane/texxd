"""Compressed contents stay editable through nesting, commits and saves."""

import bz2
import io
import lzma
import random
import shutil
import subprocess
import tarfile
import zlib

import pytest
from conftest import make_tar

from texxd.app import TexxdApp
from texxd.data import Buffer, BytesSource
from texxd.document import Document
from texxd.formats import detect
from texxd.formats.compression import Bzip2, Lzma, Xz, Zlib
from texxd.formats.tar import Tar
from texxd.node import StaleError
from texxd.view import HexView


def alone(data):
    return lzma.compress(data, format=lzma.FORMAT_ALONE)


@pytest.fixture(
    params=[
        (Bzip2, bz2.compress, bz2.decompress),
        (Xz, lzma.compress, lzma.decompress),
        (Lzma, alone, lzma.decompress),
        (Zlib, zlib.compress, zlib.decompress),
    ],
    ids=["bzip2", "xz", "lzma", "zlib"],
)
def codec(request):
    return request.param


def contents(node, fmt):
    return node.child(fmt, node.regions(fmt)[0])


def test_detect_open_commit_and_history(codec, tmp_path):
    fmt, compress, decompress = codec
    original = compress(b"hello\n")
    path = tmp_path / fmt.name
    path.write_bytes(original)
    doc = Document.open(path)
    assert detect(doc.buffer)[0] is fmt
    (region,) = doc.root.regions(fmt)
    assert (region.start, region.stop, region.data_start, region.data_size) == (0, len(original), 0, len(original))
    child = contents(doc.root, fmt)
    assert child.derived and child.resizable
    child.data.insert(5, b" there")
    assert doc.modified and not doc.buffer.modified
    with pytest.raises(StaleError):
        doc.buffer.write(0, b"!")
    doc.commit()
    assert decompress(doc.buffer.read(0, doc.buffer.size)) == b"hello there\n"
    assert not child.stale
    doc.undo()
    assert doc.buffer.read(0, doc.buffer.size) == original and child.stale
    doc.redo()
    assert not child.stale
    doc.save()
    assert decompress(path.read_bytes()) == b"hello there\n" and not doc.modified
    assert child.valid
    child.data.delete(0, child.data.size)
    doc.save()
    assert decompress(path.read_bytes()) == b""


def test_concatenated_streams_and_trailing_bytes(codec, tmp_path):
    fmt, compress, decompress = codec
    first, second = compress(b"one"), compress(b"two")
    path = tmp_path / fmt.name
    path.write_bytes(first + second + b"trailing")
    doc = Document.open(path)
    a, b = doc.root.regions(fmt)
    left, right = doc.root.child(fmt, a), doc.root.child(fmt, b)
    assert b.start == len(first) and b.stop == len(first) + len(second)
    left.data.insert(0, random.Random(0).randbytes(2000))
    right.data.insert(3, b"!")
    doc.save()
    a, b = doc.root.regions(fmt)
    saved = path.read_bytes()
    assert decompress(saved[a.start : a.stop]).endswith(b"one")
    assert decompress(saved[b.start : b.stop]) == b"two!"
    assert saved[b.stop :] == b"trailing"
    assert left.valid and right.valid
    right.data.write(0, b"T")
    doc.save()
    b = doc.root.regions(fmt)[1]
    assert decompress(path.read_bytes()[b.start : b.stop]) == b"Two!"


def test_nested_compression_and_tar(codec, tmp_path):
    fmt, compress, decompress = codec
    path = tmp_path / "nested"
    path.write_bytes(compress(make_tar({"inner": compress(b"hello"), "other": b"untouched"})))
    doc = Document.open(path)
    archive = contents(doc.root, fmt)
    inner = archive.child(Tar, archive.regions(Tar)[0])
    payload = contents(inner, fmt)
    extra = random.Random(1).randbytes(3000)
    payload.data.insert(5, extra)
    doc.save()
    with tarfile.open(fileobj=io.BytesIO(decompress(path.read_bytes()))) as tar:
        assert decompress(tar.extractfile("inner").read()) == b"hello" + extra
        assert tar.extractfile("other").read() == b"untouched"
    payload.data.delete(5, len(extra))
    doc.save()
    with tarfile.open(fileobj=io.BytesIO(decompress(path.read_bytes()))) as tar:
        assert decompress(tar.extractfile("inner").read()) == b"hello"


def test_large_output_and_input_chunk_boundaries(codec):
    fmt, compress, _ = codec
    # Exercise both buffered output from tiny inputs and many input chunks.
    for raw in (b"x" * 200000, random.Random(2).randbytes(200000)):
        encoded = compress(raw)
        data = Buffer(BytesSource(encoded))
        (region,) = fmt.regions(data)
        decoded = fmt.open(data, region)
        assert decoded.read(0, decoded.size) == raw
        assert region.stop == len(encoded)


def test_empty_stream(codec):
    fmt, compress, _ = codec
    data = Buffer(BytesSource(compress(b"")))
    assert detect(data)[0] is fmt
    assert fmt.open(data, fmt.regions(data)[0]).size == 0


def test_bad_streams(codec):
    fmt, compress, _ = codec
    encoded = compress(b"hello" * 100)
    for raw in (encoded[: len(encoded) // 2], encoded[:-1], b"not compressed"):
        data = Buffer(BytesSource(raw))
        assert fmt.regions(data) == []
        if fmt.weak_magic:
            assert fmt not in detect(data)
    data = Buffer(BytesSource(encoded + encoded[:-1]))
    assert len(fmt.regions(data)) == 1


def test_checksums_are_validated():
    for fmt, compress in ((Bzip2, bz2.compress), (Xz, lzma.compress), (Zlib, zlib.compress)):
        raw = bytearray(compress(b"hello" * 100))
        raw[-5] ^= 0xFF
        assert fmt.regions(Buffer(BytesSource(raw))) == []


@pytest.mark.parametrize("check", [lzma.CHECK_NONE, lzma.CHECK_CRC32, lzma.CHECK_CRC64, lzma.CHECK_SHA256])
def test_xz_padding_and_integrity_check(tmp_path, check):
    if not lzma.is_check_supported(check):
        pytest.skip("integrity check unavailable")
    first = lzma.compress(b"first", check=check)
    second = lzma.compress(b"second")
    path = tmp_path / "padded.xz"
    path.write_bytes(first + b"\x00" * 8 + second + b"\x00" * 4)
    doc = Document.open(path)
    a, b = doc.root.regions(Xz)
    assert b.start == a.stop + 8
    child = doc.root.child(Xz, a)
    child.data.insert(5, b"!")
    doc.save()
    a, b = doc.root.regions(Xz)
    raw = path.read_bytes()
    assert raw[a.stop : b.start] == b"\x00" * 8
    assert raw[b.stop :] == b"\x00" * 4
    decoder = lzma.LZMADecompressor()
    assert decoder.decompress(raw[: a.stop]) == b"first!" and decoder.check == check
    assert lzma.decompress(raw[b.start : b.stop]) == b"second"
    # Python's convenience decoder stops at XZ stream padding. The xz tool
    # understands padded concatenated streams and provides an independent check.
    if shutil.which("xz"):
        result = subprocess.run(["xz", "-dc", str(path)], capture_output=True, check=False)
        assert result.returncode == 0 and result.stdout == b"first!second"


def test_bzip2_keeps_block_size(tmp_path):
    path = tmp_path / "small.bz2"
    path.write_bytes(bz2.compress(b"hello", compresslevel=1))
    doc = Document.open(path)
    contents(doc.root, Bzip2).data.insert(0, b"!")
    doc.save()
    assert path.read_bytes().startswith(b"BZh1")


def test_zlib_dictionary_stream_is_not_offered():
    encoder = zlib.compressobj(zdict=b"hello")
    raw = encoder.compress(b"hello world") + encoder.flush()
    assert Zlib not in detect(Buffer(BytesSource(raw)))


def test_lzma_does_not_mistake_tar_headers_for_empty_streams():
    raw = make_tar({"dir": None, "inner.tar": make_tar({"a.txt": b"hello"})})
    data = Buffer(BytesSource(raw))
    assert detect(data)[0] is Tar
    assert Lzma not in detect(data)
    assert Lzma.regions(data) == []
    header_only = b"\x5d" + (4096).to_bytes(4, "little") + b"\x00" * 8
    data = Buffer(BytesSource(header_only))
    assert Lzma not in detect(data)
    assert Lzma.regions(data) == []


@pytest.mark.asyncio
async def test_app_opens_edits_and_saves_compression(codec, tmp_path):
    fmt, compress, decompress = codec
    path = tmp_path / fmt.name
    path.write_bytes(compress(b"hello\n"))
    app = TexxdApp(path)
    async with app.run_test(size=(130, 30)) as pilot:
        await pilot.pause()
        view = app.query_one(HexView)
        assert view.level.view.name == fmt.name
        await pilot.press("enter")
        await pilot.pause()
        assert view.data.read(0, 6) == b"hello\n"
        await pilot.press("J", "ctrl+z")
        await pilot.pause()
        assert view.data.read(0, 6) == b"hello\n"
        await pilot.press("ctrl+y", "ctrl+s")
        await pilot.pause()
        assert decompress(path.read_bytes()) == b"Jhello\n"
        assert not app.document.modified
