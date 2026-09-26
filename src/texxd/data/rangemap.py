"""Sorted range-to-value map.

Same semantics as ``arranges.Dict`` (``m[10:20] = value``, adjacent ranges with
equal values merge) but backed by sorted lists so lookups are O(log n), plus a
``shift`` operation for inserting and removing space. If arranges grows a fast
backend and ``shift``, this can be swapped out for it.
"""

from bisect import bisect_right
from typing import Any, Callable, Iterator, Optional, Tuple

Entry = Tuple[int, int, Any]


def _same(value: Any, moved: int) -> Any:
    """Default value adjustment for shifted ranges: leave the value alone."""
    return value


class RangeMap:
    """Maps non-overlapping half-open integer ranges to values.

    Args:
        adjust: Called as ``adjust(value, n)`` when part of a range is moved
            by ``n`` during a shift, and must return the value for the moved
            part. Values that encode positions (like piece table deltas) use
            this to stay correct.
    """

    def __init__(self, adjust: Callable[[Any, int], Any] = _same):
        self._starts: list[int] = []
        self._stops: list[int] = []
        self._values: list[Any] = []
        self._adjust = adjust

    def __len__(self) -> int:
        return len(self._starts)

    def __bool__(self) -> bool:
        return bool(self._starts)

    def __iter__(self) -> Iterator[Entry]:
        return iter(zip(self._starts, self._stops, self._values))

    def __repr__(self) -> str:
        items = ", ".join(f"{s}:{e}={v!r}" for s, e, v in self)
        return f"RangeMap({items})"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, RangeMap):
            return NotImplemented
        return list(self) == list(other)

    def copy(self) -> "RangeMap":
        """Return a shallow copy."""
        new = RangeMap(self._adjust)
        new._starts = self._starts.copy()
        new._stops = self._stops.copy()
        new._values = self._values.copy()
        return new

    def clear(self) -> None:
        """Remove everything."""
        self._starts.clear()
        self._stops.clear()
        self._values.clear()

    @staticmethod
    def _bounds(key: slice) -> Tuple[int, int]:
        if not isinstance(key, slice) or key.step not in (None, 1):
            raise TypeError("RangeMap keys must be slices like m[start:stop]")
        start = 0 if key.start is None else key.start
        if key.stop is None:
            raise ValueError("RangeMap slices need a stop")
        return start, key.stop

    def _first_touching(self, pos: int) -> int:
        """Index of the first range whose stop is > pos."""
        i = bisect_right(self._starts, pos) - 1
        if i >= 0 and self._stops[i] > pos:
            return i
        return i + 1

    def get(self, pos: int, default: Any = None) -> Any:
        """Get the value covering a single position."""
        i = bisect_right(self._starts, pos) - 1
        if i >= 0 and pos < self._stops[i]:
            return self._values[i]
        return default

    def __getitem__(self, pos: int) -> Any:
        i = bisect_right(self._starts, pos) - 1
        if i >= 0 and pos < self._stops[i]:
            return self._values[i]
        raise KeyError(pos)

    def __contains__(self, pos: int) -> bool:
        i = bisect_right(self._starts, pos) - 1
        return i >= 0 and pos < self._stops[i]

    def overlapping(self, start: int, stop: int) -> Iterator[Entry]:
        """Yield (start, stop, value) for every range that overlaps [start, stop).

        The ranges are yielded whole, not clipped.
        """
        i = self._first_touching(start)
        while i < len(self._starts) and self._starts[i] < stop:
            yield self._starts[i], self._stops[i], self._values[i]
            i += 1

    def _cut(self, start: int, stop: int) -> int:
        """Remove coverage of [start, stop), splitting ranges at the edges.

        Returns the index where a range starting at ``start`` should go.
        """
        i = self._first_touching(start)
        j = i
        while j < len(self._starts) and self._starts[j] < stop:
            j += 1

        keep_starts, keep_stops, keep_values = [], [], []
        if i < j:
            if self._starts[i] < start:
                keep_starts.append(self._starts[i])
                keep_stops.append(start)
                keep_values.append(self._values[i])
            if self._stops[j - 1] > stop:
                keep_starts.append(stop)
                keep_stops.append(self._stops[j - 1])
                keep_values.append(self._values[j - 1])

        self._starts[i:j] = keep_starts
        self._stops[i:j] = keep_stops
        self._values[i:j] = keep_values

        if keep_starts and keep_starts[0] < start:
            return i + 1
        return i

    def _merge_around(self, i: int) -> None:
        """Merge the range at index i with equal-valued neighbours that touch it."""
        starts, stops, values = self._starts, self._stops, self._values
        if i + 1 < len(starts) and stops[i] == starts[i + 1] and values[i] == values[i + 1]:
            self._stops[i] = self._stops[i + 1]
            del self._starts[i + 1], self._stops[i + 1], self._values[i + 1]
        if i > 0 and self._stops[i - 1] == self._starts[i] and self._values[i - 1] == self._values[i]:
            self._stops[i - 1] = self._stops[i]
            del self._starts[i], self._stops[i], self._values[i]

    def __setitem__(self, key: slice, value: Any) -> None:
        start, stop = self._bounds(key)
        if stop <= start:
            return
        i = self._cut(start, stop)
        self._starts.insert(i, start)
        self._stops.insert(i, stop)
        self._values.insert(i, value)
        self._merge_around(i)

    def __delitem__(self, key: slice) -> None:
        start, stop = self._bounds(key)
        if stop > start:
            self._cut(start, stop)

    def shift(self, pos: int, n: int) -> None:
        """Move everything at or after ``pos`` by ``n``.

        With n > 0 this opens an empty gap [pos, pos + n), splitting any range
        that spans ``pos``. With n < 0 it removes [pos, pos - n) and closes the
        gap, joining the ranges either side if their values are equal.
        """
        if n == 0:
            return
        if n < 0:
            self._cut(pos, pos - n)
            pos = pos - n
        else:
            # split a range that spans pos so the part after it can move
            i = self._first_touching(pos)
            if i < len(self._starts) and self._starts[i] < pos:
                self._starts.insert(i + 1, pos)
                self._stops.insert(i + 1, self._stops[i])
                self._values.insert(i + 1, self._values[i])
                self._stops[i] = pos

        i = self._first_touching(pos)
        for k in range(i, len(self._starts)):
            self._starts[k] += n
            self._stops[k] += n
            self._values[k] = self._adjust(self._values[k], n)

        if n < 0 and 0 < i < len(self._starts):
            self._merge_around(i)

    def ranges(self, value_filter: Optional[Callable[[Any], bool]] = None) -> list[Tuple[int, int]]:
        """Return the covered ranges as (start, stop) tuples, merging touching ones.

        Args:
            value_filter: If given, only include ranges whose value passes it.
        """
        out: list[Tuple[int, int]] = []
        for start, stop, value in self:
            if value_filter and not value_filter(value):
                continue
            if out and out[-1][1] == start:
                out[-1] = (out[-1][0], stop)
            else:
                out.append((start, stop))
        return out
