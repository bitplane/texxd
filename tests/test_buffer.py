"""Tests for Buffer and Window."""

import os
import random

import pytest

from texxd.data import Buffer, BytesSource, Change, ResizeError, Window


def make(data=b"0123456789"):
    return Buffer(BytesSource(data))


def contents(buf):
    return buf.read(0, buf.size)


def test_read():
    buf = make()
    assert buf.size == 10
    assert buf.read(2, 3) == b"234"
    assert buf.read(8, 10) == b"89"
    assert buf.read(10, 1) == b""
    assert not buf.modified


def test_empty():
    buf = Buffer()
    assert buf.size == 0
    assert buf.read(0, 10) == b""
    buf.write(0, b"ab")
    assert contents(buf) == b"ab"


def test_write_overwrites():
    buf = make()
    buf.write(3, b"xy")
    assert contents(buf) == b"012xy56789"
    assert buf.modified
    assert buf.edits(0, 10) == [(3, 5)]


def test_write_past_end_extends():
    buf = make()
    buf.write(9, b"abc")
    assert contents(buf) == b"012345678abc"
    with pytest.raises(IndexError):
        buf.write(20, b"x")


def test_insert_and_delete():
    buf = make()
    buf.insert(5, b"--")
    assert contents(buf) == b"01234--56789"
    assert buf.edits(0, buf.size) == [(5, 7)]
    buf.delete(0, 3)
    assert contents(buf) == b"34--56789"
    buf.delete(7, 100)
    assert contents(buf) == b"34--567"


def test_typing_merges_pieces():
    buf = make()
    for i, c in enumerate(b"abc"):
        buf.write(2 + i, bytes([c]))
    assert len(buf._pieces) == 3  # orig, added, orig


def test_undo_redo():
    buf = make()
    buf.write(0, b"X")
    buf.insert(5, b"Y")
    buf.delete(9, 2)
    assert contents(buf) == b"X1234Y567"
    assert buf.undo() == Change(9, 0, 2)
    assert contents(buf) == b"X1234Y56789"
    buf.undo()
    buf.undo()
    assert contents(buf) == b"0123456789"
    assert not buf.modified
    assert buf.undo() is None
    buf.redo()
    assert contents(buf) == b"X123456789"
    assert buf.modified
    buf.write(1, b"Z")
    assert not buf.can_redo


def test_listeners():
    buf = make()
    seen = []
    buf.subscribe(seen.append)
    buf.write(1, b"ab")
    buf.insert(0, b"c")
    buf.delete(0, 1)
    buf.undo()
    buf.unsubscribe(seen.append)
    buf.write(0, b"z")
    assert seen == [Change(1, 2, 2), Change(0, 0, 1), Change(0, 1, 0), Change(0, 0, 1)]


def test_random_edits_match_bytearray():
    rng = random.Random(42)
    original = bytes(rng.randrange(256) for _ in range(200))
    buf = make(original)
    ref = bytearray(original)
    history = [bytes(ref)]
    for _ in range(1500):
        op = rng.random()
        pos = rng.randrange(len(ref) + 1)
        if op < 0.35:
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 6)))
            buf.write(pos, data)
            ref[pos : pos + len(data)] = data
        elif op < 0.6:
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 6)))
            buf.insert(pos, data)
            ref[pos:pos] = data
        elif op < 0.8:
            n = rng.randrange(1, 6)
            if pos >= len(ref):
                continue
            buf.delete(pos, n)
            del ref[pos : pos + n]
        elif op < 0.9 and len(history) > 1:
            buf.undo()
            history.pop()
            ref = bytearray(history[-1])
            continue
        else:
            continue
        history.append(bytes(ref))
        assert contents(buf) == bytes(ref)
        start = rng.randrange(len(ref) + 1)
        assert buf.read(start, 7) == bytes(ref[start : start + 7])


def test_save_in_place(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"0123456789")
    buf = Buffer.open(path)
    inode = os.stat(path).st_ino
    buf.write(2, b"ab")
    buf.write(10, b"!")
    buf.save()
    assert path.read_bytes() == b"01ab456789!"
    assert os.stat(path).st_ino == inode  # patched, not replaced
    assert not buf.modified
    assert buf.edits(0, buf.size) == []
    assert contents(buf) == b"01ab456789!"
    buf.write(0, b"Z")
    buf.save()
    assert path.read_bytes() == b"Z1ab456789!"


def test_save_truncates_in_place(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"0123456789")
    buf = Buffer.open(path)
    buf.delete(7, 3)
    buf.save()
    assert path.read_bytes() == b"0123456"


def test_save_rewrites_when_bytes_move(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"0123456789")
    path.chmod(0o640)
    buf = Buffer.open(path)
    buf.insert(0, b">>")
    buf.save()
    assert path.read_bytes() == b">>0123456789"
    assert oct(path.stat().st_mode & 0o777) == "0o640"
    assert list(tmp_path.iterdir()) == [path]
    # the buffer now reads the new file
    buf.write(0, b"<")
    buf.save()
    assert path.read_bytes() == b"<>0123456789"


def test_save_read_only_fails(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"0123")
    path.chmod(0o444)
    buf = Buffer.open(path)
    buf.write(0, b"x")
    with pytest.raises(PermissionError):
        buf.save()
    assert path.read_bytes() == b"0123"
    assert buf.modified


def test_window_reads_and_writes_through():
    buf = make()
    win = Window(buf, 3, 4)
    assert win.size == 4
    assert win.read(0, 10) == b"3456"
    win.write(1, b"x")
    assert contents(buf) == b"0123x56789"
    assert win.edits(0, 4) == [(1, 2)]
    assert win.root is buf
    with pytest.raises(ResizeError):
        win.write(3, b"ab")
    with pytest.raises(ResizeError):
        win.insert(0, b"a")


def test_window_follows_parent_resizes():
    buf = make()
    win = Window(buf, 5, 3)
    seen = []
    win.subscribe(seen.append)
    buf.insert(0, b"ab")
    assert win.read(0, 3) == b"567"
    buf.delete(10, 2)  # after the window
    assert win.read(0, 3) == b"567"
    buf.write(8, b"Z")
    assert win.read(0, 3) == b"5Z7"
    assert seen == [Change(1, 1, 1)]
    buf.undo()
    buf.undo()
    buf.undo()
    assert win.valid and win.start == 5
    buf.insert(6, b"!")  # inside the window
    assert not win.valid
