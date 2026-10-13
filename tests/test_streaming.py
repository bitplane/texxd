"""Disk-backed compression, source pieces, and storage ownership."""

import bz2
import gc
import gzip
import hashlib
import lzma
import random
import tracemalloc
import weakref
import zlib
from pathlib import Path

import pytest

from texxd.data import Buffer, BytesSource, ResizeError, TemporarySource, Window
from texxd.document import Document
from texxd.formats.compression import Bzip2, Lzma, Xz, Zlib
from texxd.formats.gzip import Gzip
from texxd.formats.stream import CHUNK

CODECS = [Gzip, Bzip2, Xz, Lzma, Zlib]


@pytest.fixture(params=CODECS, ids=lambda fmt: fmt.name)
def compressed(request, tmp_path):
    fmt = request.param
    compressors = {
        Gzip: gzip.compress,
        Bzip2: bz2.compress,
        Xz: lzma.compress,
        Lzma: lambda raw: lzma.compress(raw, format=lzma.FORMAT_ALONE),
    }
    compressors[Zlib] = zlib.compress
    path = tmp_path / fmt.name
    path.write_bytes(compressors[fmt](b"hello\n"))
    doc = Document.open(path)
    child = doc.root.child(fmt, doc.root.regions(fmt)[0])
    yield fmt, doc, child
    doc.close()


def test_temporary_source_streams_and_cleans_up(tmp_path):
    source = TemporarySource((chunk for chunk in (b"abc", b"def")), directory=tmp_path)
    assert source.size == 6
    assert source.read(4, 9) == b"ef" and source.read(0, 3) == b"abc"
    file = source._file
    ref = weakref.ref(source)
    del source
    assert ref() is None and file.closed
    assert list(tmp_path.iterdir()) == []


def test_default_temporary_directory_is_on_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = TemporarySource([b"test"])
    assert (tmp_path / "tmp").is_dir()
    source.close()
    assert source._file.closed


def test_failed_stream_closes_its_file(monkeypatch, tmp_path):
    from texxd.data import source as sources

    files = []
    create = sources.tempfile.TemporaryFile

    def tracked(**kwargs):
        file = create(**kwargs)
        files.append(file)
        return file

    def chunks():
        yield b"partial"
        raise OSError("failed to produce output")

    monkeypatch.setattr(sources.tempfile, "TemporaryFile", tracked)
    with pytest.raises(OSError, match="failed to produce"):
        TemporarySource(chunks(), directory=tmp_path)
    assert len(files) == 1 and files[0].closed
    assert list(tmp_path.iterdir()) == []


def test_splice_through_nested_windows_and_undo(tmp_path):
    buffer = Buffer(BytesSource(b"0123456789"))
    outer = Window(buffer, 2, 6)
    inner = Window(outer, 1, 3)
    source = TemporarySource([b"hello"], directory=tmp_path)
    file = source._file
    inner.splice(0, 3, source)
    del source
    assert buffer.read(0, 20) == b"012hello6789"
    assert inner.read(0, 10) == b"hello" and outer.size == 8
    assert not buffer._added
    assert inner.edits(0, 5) == [(0, 5)]
    buffer.undo()
    assert buffer.read(0, 20) == b"0123456789" and inner.size == 3
    assert not file.closed  # redo still owns the source
    buffer.redo()
    assert inner.read(0, 10) == b"hello"
    buffer.undo()
    buffer.write(0, b"!")  # abandoning redo releases its source
    assert file.closed


def test_source_pieces_survive_edits_and_history():
    rng = random.Random(41)
    buffer = Buffer(BytesSource(b"original"))
    expected = bytearray(b"original")
    states = [bytes(expected)]
    for _ in range(200):
        at = rng.randrange(len(expected) + 1)
        removed = rng.randrange(len(expected) - at + 1)
        raw = rng.randbytes(rng.randrange(1, 20))
        buffer.splice(at, removed, BytesSource(raw))
        expected[at : at + removed] = raw
        assert buffer.read(0, buffer.size) == bytes(expected)
        states.append(bytes(expected))
    for state in reversed(states[:-1]):
        buffer.undo()
        assert buffer.read(0, buffer.size) == state
    for state in states[1:]:
        buffer.redo()
        assert buffer.read(0, buffer.size) == state


def test_splice_validates_bounds_and_fixed_windows():
    buffer = Buffer(BytesSource(b"original"))
    source = BytesSource(b"new")
    for at, removed in ((-1, 1), (9, 0), (0, -1), (7, 2)):
        with pytest.raises(IndexError):
            buffer.splice(at, removed, source)
    buffer.resizable = False
    window = Window(buffer, 0, 3)
    with pytest.raises(ResizeError):
        window.splice(0, 2, source)
    window.splice(0, 3, source)
    assert buffer.read(0, 8) == b"newginal"


def test_save_source_pieces_and_release_storage(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"original")
    buffer = Buffer.open(path)
    source = TemporarySource([b"new contents"], directory=tmp_path)
    file = source._file
    buffer.splice(0, buffer.size, source)
    del source
    buffer.save()
    assert path.read_bytes() == b"new contents"
    assert buffer.read(0, buffer.size) == b"new contents"
    assert not buffer.can_undo and not buffer.edits(0, buffer.size)
    assert file.closed
    buffer.close()


def test_compression_uses_disk_for_decoding_and_writeback(compressed):
    _, doc, child = compressed
    assert isinstance(child.data.source, TemporarySource)
    child.data.insert(0, b"!")
    doc.commit()
    source_pieces = [src for _, _, (src, _) in doc.buffer._pieces if isinstance(src, TemporarySource)]
    assert len(source_pieces) == 1
    assert not doc.buffer._added  # encoded output wasn't copied into the edit store
    file = source_pieces[0]._file
    del source_pieces
    doc.undo()
    assert child.stale and not file.closed
    doc.redo()
    assert child.data.read(0, 7) == b"!hello\n"
    doc.save()
    assert file.closed and child.valid
    assert isinstance(child.data.source, TemporarySource)


def test_discard_and_document_close_keep_history_sources_alive(compressed):
    _, doc, child = compressed
    original_file = child.data.source._file
    child.data.write(0, b"J")
    child.discard()
    new_file = child.data.source._file
    assert not original_file.closed and not new_file.closed
    doc.undo()
    assert child.data.read(0, 6) == b"Jello\n"
    doc.redo()
    assert child.data.read(0, 6) == b"hello\n"
    doc.close()
    assert original_file.closed and new_file.closed


def test_close_releases_invalidated_buffers(compressed):
    _, doc, child = compressed
    file = child.data.source._file
    # A source replacement straddling the span invalidates it for every codec.
    doc.buffer.splice(0, doc.buffer.size, BytesSource(b"bad"))
    assert not child.valid
    doc.root._children.clear()  # simulate pruning an invalid node
    doc.close()
    assert file.closed


def test_failed_commit_rolls_back_and_releases_output(compressed, monkeypatch):
    fmt, doc, child = compressed
    original = doc.buffer.read(0, doc.buffer.size)
    captured = []
    encode = fmt.encode

    def tracked(data, region, contents):
        source = encode(data, region, contents)
        captured.append(source._file)
        return source

    monkeypatch.setattr(fmt, "encode", tracked)
    monkeypatch.setattr(doc.buffer, "resizable", False)
    child.data.insert(0, random.Random(9).randbytes(10000))
    with pytest.raises(ResizeError):
        child.commit()
    assert doc.buffer.read(0, doc.buffer.size) == original
    assert child.stale and captured[0].closed
    doc.undo()
    assert child.data.read(0, 6) == b"hello\n"


@pytest.mark.parametrize("fmt", CODECS, ids=lambda fmt: fmt.name)
def test_high_expansion_decode_has_bounded_memory(fmt):
    # Allocate the fixture before tracing. The decoder should never allocate
    # the whole 32 MiB result, even when the encoded stream fits in one chunk.
    raw_size = 32 * 1024 * 1024
    compressors = {
        Gzip: gzip.compress,
        Bzip2: bz2.compress,
        Xz: lzma.compress,
        Lzma: lambda raw: lzma.compress(raw, format=lzma.FORMAT_ALONE),
    }
    compressors[Zlib] = zlib.compress
    data = Buffer(BytesSource(compressors[fmt](b"x" * raw_size)))
    (region,) = fmt.regions(data)
    gc.collect()
    tracemalloc.start()
    try:
        decoded = fmt.open(data, region)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    try:
        assert isinstance(decoded.source, TemporarySource)
        assert decoded.size == raw_size
        assert decoded.read(raw_size - CHUNK, CHUNK) == b"x" * CHUNK
        assert peak < raw_size // 2
    finally:
        data.history.close()


def test_saving_a_shared_temporary_source_does_not_close_other_owners(tmp_path):
    source = TemporarySource([b"shared"], directory=tmp_path)
    file = source._file
    left, right = Buffer(source), Buffer(source)
    del source
    left.save(tmp_path / "saved")
    assert not file.closed and right.read(0, 6) == b"shared"
    right.close()
    assert file.closed
    left.close()


def test_incompressible_writeback_and_save_have_bounded_memory(tmp_path):
    path = tmp_path / "random.gz"
    path.write_bytes(gzip.compress(b"seed"))
    doc = Document.open(path)
    child = doc.root.child(Gzip, doc.root.regions(Gzip)[0])
    raw_size = 16 * 1024 * 1024
    expected = hashlib.sha256()
    rng = random.Random(73)

    def chunks():
        for _ in range(raw_size // CHUNK):
            chunk = rng.randbytes(CHUNK)
            expected.update(chunk)
            yield chunk

    child.data.replace(TemporarySource(chunks()))
    gc.collect()
    tracemalloc.start()
    try:
        doc.commit()
        assert not doc.buffer._added
        doc.save()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    try:
        assert peak < 4 * 1024 * 1024
        actual = hashlib.sha256()
        # Independent validation through the standard library, also in chunks.
        with gzip.open(path, "rb") as file:
            while chunk := file.read(CHUNK):
                actual.update(chunk)
        assert actual.digest() == expected.digest()
    finally:
        doc.close()


@pytest.mark.parametrize("cut", [1, 4, 8])
def test_gzip_missing_trailer_is_not_openable(cut):
    data = Buffer(BytesSource(gzip.compress(b"hello")[:-cut]))
    assert Gzip.regions(data) == []


def test_gzip_checksum_failure_releases_decoded_output(monkeypatch):
    from texxd.data import source as sources

    raw = gzip.compress(b"hello")
    original = Buffer(BytesSource(raw))
    region = Gzip.regions(original)[0]
    damaged = bytearray(raw)
    damaged[-8] ^= 1
    data = Buffer(BytesSource(damaged))
    assert Gzip.regions(data) == []
    files = []
    create = sources.tempfile.TemporaryFile

    def tracked(**kwargs):
        file = create(**kwargs)
        files.append(file)
        return file

    monkeypatch.setattr(sources.tempfile, "TemporaryFile", tracked)
    from texxd.data import EditError

    with pytest.raises(EditError, match="checksum"):
        Gzip.open(data, region)
    assert len(files) == 1 and files[0].closed
