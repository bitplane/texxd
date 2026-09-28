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
    # writing past the end extends the window, moving what's after it
    win.write(3, b"ab")
    assert contents(buf) == b"0123x5ab789"
    assert win.size == 5
    buf.undo()
    assert contents(buf) == b"0123x56789" and win.size == 4


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
    buf.insert(6, b"!")  # inside the window: it grows
    assert win.read(0, 10) == b"5!67"
    buf.delete(4, 2)  # across its start: it can't tell where its bytes went
    assert not win.valid


def test_window_edges():
    buf = make()
    win = Window(buf, 3, 4)  # "3456"
    buf.insert(3, b"<")  # at its start, not through it: it moves
    buf.insert(8, b">")  # at its end, not through it: it doesn't grow
    assert win.read(0, 10) == b"3456" and win.start == 4
    win.insert(0, b"[")
    win.insert(5, b"]")
    assert win.read(0, 10) == b"[3456]"
    assert contents(buf) == b"012<[3456]>789"
    # undoing a delete at the end puts the bytes back in the window, not after it
    win.delete(5, 1)
    assert win.read(0, 10) == b"[3456"
    buf.undo()
    assert win.read(0, 10) == b"[3456]"


def test_nested_windows_resize_together():
    buf = make()
    outer = Window(buf, 2, 6)  # "234567"
    inner = Window(outer, 4, 2)  # "67", at the end of outer
    inner.insert(2, b"xy")
    assert inner.read(0, 10) == b"67xy"
    assert outer.read(0, 10) == b"234567xy"
    assert contents(buf) == b"01234567xy89"
    inner.delete(0, 3)
    assert contents(buf) == b"012345y89"
    assert outer.read(0, 10) == b"2345y" and inner.read(0, 10) == b"y"


def test_fixed_parent_refuses_resizes():
    class Fixed(Buffer):
        resizable = False

    win = Window(Fixed(BytesSource(b"0123")), 1, 2)
    with pytest.raises(ResizeError):
        win.insert(0, b"a")
    with pytest.raises(ResizeError):
        win.delete(0, 1)


def test_transaction_is_one_undo_step():
    buf = make()
    with buf.transaction():
        buf.write(0, b"a")
        buf.insert(1, b"b")
        buf.delete(5, 2)
    assert contents(buf) == b"ab1236789"
    change = buf.undo()
    assert contents(buf) == b"0123456789"
    assert change == Change(0, 1, 1)
    assert not buf.can_undo
    buf.redo()
    assert contents(buf) == b"ab1236789"


def test_transaction_rolls_back_on_error():
    buf = make()
    buf.write(0, b"a")
    buf.undo()
    win = Window(buf, 4, 2)
    with pytest.raises(ValueError):
        with buf.transaction():
            buf.insert(0, b"xx")
            buf.write(9, b"!")
            raise ValueError()
    assert contents(buf) == b"0123456789"
    assert win.start == 4 and win.valid
    assert not buf.can_undo and buf.can_redo  # the failed edit didn't lose the redo
    assert not buf.modified


def test_deferred_work_runs_highest_priority_first_and_can_edit():
    buf = make()
    ran = []

    def outer():
        ran.append("outer")
        buf.write(0, b"O")

    def inner():
        ran.append("inner")
        buf.write(1, b"I")
        buf.defer("outer", 1, outer)  # queued again: still only runs once, after this

    with buf.transaction():
        buf.write(9, b"!")
        buf.defer("outer", 1, outer)
        buf.defer("inner", 2, inner)
    assert ran == ["inner", "outer"]
    assert contents(buf) == b"OI2345678!"
    buf.undo()
    assert contents(buf) == b"0123456789"
    buf.defer("ignored", 1, outer)  # outside a transaction
    assert ran == ["inner", "outer"]


def test_failing_deferred_work_rolls_back():
    buf = make()

    def fail():
        raise ResizeError("no")

    with pytest.raises(ResizeError):
        with buf.transaction():
            buf.insert(0, b"x")
            buf.defer("fail", 1, fail)
    assert contents(buf) == b"0123456789"
    buf.write(0, b"y")  # the next edit starts afresh
    buf.undo()
    assert contents(buf) == b"0123456789"
