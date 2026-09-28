"""Tests for JSON: the tolerant parser, and the tree layout."""

import json
import random

import pytest

from texxd.data import Buffer, BytesSource
from texxd.formats import detect
from texxd.formats.json import Json, parse
from texxd.view.tree import TreeRows


def shape(node, depth=8):
    """The tree as nested (label, kind, source) tuples, with errors."""
    out = (node.label, node.kind, node.buffer[node.start : node.stop].decode(), node.error)
    if node.container and depth:
        return out + ([shape(node.child(i), depth - 1) for i in range(node.count)],)
    return out


def test_valid_json_keeps_where_everything_is():
    raw = b'{"a": 1, "b": [true, null, "x"], "c": {"d": -2.5e3}}'
    root = parse(raw)
    assert root.kind == "object" and root.error is None and (root.start, root.stop) == (0, len(raw))
    a, b, c = root.children
    assert (a.label, a.kind, a.value, a.key_start, a.row_start) == ("a", "number", 1, 1, 1)
    assert [child.kind for child in b.children] == ["bool", "null", "string"]
    assert b.children[2].value == "x" and b.children[2].label == 2
    assert c.children[0].path == "$.c.d" and c.children[0].value == -2500.0


@pytest.mark.parametrize(
    "raw, errors",
    [
        (b'{"a": 1', ["no closing '}'"]),
        (b'{"a": }', ["missing value"]),
        (b'{"a" 1}', ["expected ':'"]),
        (b"{1: 2}", ["keys need quotes"]),
        (b'[1 2, "x]', ["no closing ']'", "expected ',' or ']'", "the string isn't closed"]),
        (b"[tru, 1]", ["unexpected 'tru'"]),
        (b'{"a": [1}', ["missing ']'", "no closing ']'"]),
        (b"[1, }]", ["unexpected '}'", "unexpected '}'"]),
    ],
)
def test_broken_json_shows_what_it_can(raw, errors):
    root = parse(raw)
    found = []

    def walk(node):
        if node.error:
            found.append(node.error)
        if node.container:
            for child in node.children:
                walk(child)

    walk(root)
    assert found == errors


def test_json_lines():
    root = parse(b'{"n": 1}\n{"n": 2, oops}\n\n  [3]\n')
    assert root.kind == "stream" and root.count == 3
    assert [child.kind for child in root.children] == ["object", "object", "array"]
    assert root.child(1).error is None and root.child(1).children[1].error == "expected a key"
    assert root.child(2).start == 27 and root.child(2).path == "$[2]"
    # a line that breaks only breaks itself
    root = parse(b'{"n": [1}\n{"n": 2}\n')
    assert root.child(0).error and root.child(1).error is None


def test_concatenated_json_and_scalars():
    root = parse(b'{"x":\n 1}{"y": 2} 7')
    assert root.kind == "stream" and [child.kind for child in root.children] == ["object", "object", "number"]
    assert parse(b" 42 ").kind == "number"
    assert parse(b"").error == "empty"


def test_garbage_never_hangs():
    rng = random.Random(0)
    alphabet = b'{}[]:,"\\ \n1a-tn'
    for _ in range(300):
        raw = bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        root = parse(raw)

        def walk(node, lo, hi):
            assert lo <= node.start <= node.stop <= hi
            if node.container:
                for child in node.children:
                    walk(child, node.start, node.stop)

        walk(root, 0, len(raw))


def test_sniff():
    assert detect(Buffer(BytesSource(b'{"a": [1, 2]}')))[0] is Json
    assert detect(Buffer(BytesSource(b'{"a": 1}\n{"a": 2}\n')))[0] is Json
    assert Json not in detect(Buffer(BytesSource(b"{not json}")))
    assert Json not in detect(Buffer(BytesSource(b"42")))


def visible(rows):
    """Every visible node, walking the tree the slow way."""
    out = []

    def walk(node):
        out.append(node)
        if rows.is_open(node):
            for i in range(node.count):
                walk(node.child(i))

    if rows.show_root:
        walk(rows.root)
    else:
        for i in range(rows.root.count):
            walk(rows.root.child(i))
    return out


@pytest.mark.parametrize("seed", range(10))
def test_tree_rows_match_walking_the_tree(seed):
    rng = random.Random(seed)

    def value(depth):
        if depth > 3 or rng.random() < 0.4:
            return rng.choice([1, "s", None, True, 2.5])
        if rng.random() < 0.5:
            return [value(depth + 1) for _ in range(rng.randint(0, 4))]
        return {f"k{i}": value(depth + 1) for i in range(rng.randint(0, 4))}

    if seed % 2:
        raw = "\n".join(json.dumps(value(1)) for _ in range(6)).encode()
    else:
        raw = json.dumps(value(0), indent=rng.choice([None, 2])).encode()
    root = parse(raw)
    # unfold some containers, by path
    unfolded: dict = {}

    def pick(node, level):
        if node.container:
            for i in range(node.count):
                child = node.child(i)
                if child.container and rng.random() < 0.6:
                    pick(child, level.setdefault(child.label, {}))

    pick(root, unfolded)
    rows = TreeRows(root, unfolded, len(raw))
    nodes = visible(rows)
    assert rows.count == len(nodes)
    for row, node in enumerate(nodes):
        assert rows.node(row) is node
        assert rows.row_of_node(node) == row
        assert rows.row_of(node.row_start) == row
        assert rows.start(row) == node.row_start


def test_big_json_lines_are_lazy():
    raw = b"".join(json.dumps({"i": i, "tags": ["a"] * (i % 3)}).encode() + b"\n" for i in range(200_000))
    root = parse(raw)
    rows = TreeRows(root, {150_000: {"tags": {}}}, len(raw))
    assert rows.count == 200_000 + 2 + (150_000 % 3)
    assert root._children.count(None) > 199_000  # only the records looked at were parsed
    assert rows.node(150_000 + 2).path == "$[150000].tags"
