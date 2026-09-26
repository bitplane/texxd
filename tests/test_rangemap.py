"""Tests for RangeMap."""

import random

import pytest

from texxd.data.rangemap import RangeMap


def as_list(m):
    return list(m)


def test_set_and_get():
    m = RangeMap()
    m[10:20] = "a"
    assert m.get(9) is None
    assert m[10] == "a"
    assert m[19] == "a"
    assert 20 not in m
    with pytest.raises(KeyError):
        m[20]


def test_overwrite_splits():
    m = RangeMap()
    m[0:10] = "a"
    m[3:5] = "b"
    assert as_list(m) == [(0, 3, "a"), (3, 5, "b"), (5, 10, "a")]


def test_adjacent_equal_values_merge():
    m = RangeMap()
    m[0:5] = "a"
    m[5:10] = "a"
    assert as_list(m) == [(0, 10, "a")]
    m[10:12] = "b"
    m[12:14] = "a"
    m[10:12] = "a"
    assert as_list(m) == [(0, 14, "a")]


def test_delete_leaves_hole():
    m = RangeMap()
    m[0:10] = "a"
    del m[2:4]
    assert as_list(m) == [(0, 2, "a"), (4, 10, "a")]


def test_overlapping():
    m = RangeMap()
    m[0:5] = "a"
    m[5:10] = "b"
    m[20:30] = "c"
    assert list(m.overlapping(4, 21)) == [(0, 5, "a"), (5, 10, "b"), (20, 30, "c")]
    assert list(m.overlapping(10, 20)) == []


def test_shift_insert_splits_and_adjusts():
    m = RangeMap(adjust=lambda v, n: v + n)
    m[0:10] = 0
    m.shift(4, 3)
    assert as_list(m) == [(0, 4, 0), (7, 13, 3)]


def test_shift_delete_closes_gap_and_merges():
    m = RangeMap()
    m[0:4] = "a"
    m[4:6] = "b"
    m[6:10] = "a"
    m.shift(4, -2)
    assert as_list(m) == [(0, 8, "a")]


def test_ranges_filter():
    m = RangeMap()
    m[0:2] = "x"
    m[2:4] = "y"
    m[4:6] = "x"
    assert m.ranges() == [(0, 6)]
    assert m.ranges(lambda v: v == "x") == [(0, 2), (4, 6)]


def test_matches_reference_model():
    """Random sets, deletes and shifts agree with a per-position list."""
    rng = random.Random(1234)
    size = 60
    m = RangeMap()
    ref = [None] * size
    for _ in range(2000):
        op = rng.random()
        a = rng.randrange(size)
        b = rng.randrange(a, size + 1)
        if op < 0.5:
            v = rng.choice("abc")
            m[a:b] = v
            ref[a:b] = [v] * (b - a)
        elif op < 0.7:
            del m[a:b]
            ref[a:b] = [None] * (b - a)
        elif op < 0.85:
            n = rng.randrange(1, 5)
            m.shift(a, n)
            ref[a:a] = [None] * n
            ref = ref[:size]
            del m[size : size + 10]
        else:
            n = b - a
            if n:
                m.shift(a, -n)
                del ref[a:b]
                ref += [None] * n
        got = [m.get(i) for i in range(size)]
        assert got == ref
        # invariant: sorted, non-overlapping, touching neighbours differ
        entries = as_list(m)
        for (s1, e1, v1), (s2, e2, v2) in zip(entries, entries[1:]):
            assert s1 < e1 <= s2 < e2
            assert not (e1 == s2 and v1 == v2)
