"""JSON, and streams of it (JSON Lines, concatenated JSON).

JSON opens as a tree. Every node knows where its bytes are, so the tree
lines up with the text and hex beside it, and edits can change just the
bytes of one value.

Parsing is tolerant: anything that isn't valid JSON becomes an error node,
and parsing carries on after it, so a half-typed document still shows
everything it can. It's also lazy: a node only parses its children when
something asks for them, and finds where a value ends by scanning for
brackets and strings, without parsing what's inside.

A document with more than one top-level value is a stream, whose children
are the values. In JSON Lines, where each value is on a line of its own,
each line is parsed on its own, so a broken line only breaks itself.
"""

import json
import re
from bisect import bisect_right
from dataclasses import replace
from typing import Optional

from ..data import Buffer, BytesSource, Data, EditError
from . import Format, Region

SAMPLE = 64 * 1024

WHITESPACE = re.compile(rb"[ \t\r\n]*")
# JSON strings can't hold raw line breaks, so a missing quote stops at the end of the line
STRING = re.compile(rb'"(?:[^"\\\n]|\\.)*"')
OPEN_STRING = re.compile(rb'"(?:[^"\\\n]|\\.)*')
NUMBER = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
LITERAL = re.compile(rb"true|false|null")
# what matters for finding the end of a container: strings (which can hold brackets) and brackets
STRUCTURE = re.compile(rb'"(?:[^"\\\n]|\\.)*"?|[\[\]{}]')
GARBAGE = re.compile(rb'[^,:\[\]{}\s"]+')
CLOSERS = {ord("{"): ord("}"), ord("["): ord("]")}
SPACES = re.compile(r"\s+")
SURROGATE = re.compile(r"[\ud800-\udfff]")


def _skip_space(buffer: bytes, pos: int, end: int) -> int:
    return WHITESPACE.match(buffer, pos, end).end() if pos < end else pos


def _value(buffer: bytes, pos: int, end: int) -> tuple[str, int, Optional[str]]:
    """Find the value at ``pos``: its kind, where it stops, and what's wrong with it, if anything."""
    first = buffer[pos]
    if first in CLOSERS:
        return _container(buffer, pos, end)
    if first == ord('"'):
        match = STRING.match(buffer, pos, end)
        if match:
            return "string", match.end(), None
        return "string", OPEN_STRING.match(buffer, pos, end).end(), "the string isn't closed"
    match = NUMBER.match(buffer, pos, end)
    if match:
        return "number", match.end(), None
    match = LITERAL.match(buffer, pos, end)
    if match:
        return ("null" if match.group() == b"null" else "bool"), match.end(), None
    match = GARBAGE.match(buffer, pos, end)
    stop = match.end() if match else pos + 1
    return "error", stop, f"unexpected {buffer[pos:stop][:20].decode('utf-8', 'replace')!r}"


def _container(buffer: bytes, pos: int, end: int) -> tuple[str, int, Optional[str]]:
    kind = "object" if buffer[pos] == ord("{") else "array"
    expecting = [CLOSERS[buffer[pos]]]
    error = None
    for match in STRUCTURE.finditer(buffer, pos + 1, end):
        token = match.group()
        if token[0] == ord('"'):
            continue
        if token[0] in CLOSERS:
            expecting.append(CLOSERS[token[0]])
            continue
        if token[0] not in expecting:
            # closes nothing that's open: skip it
            error = error or f"unexpected {chr(token[0])!r}"
            continue
        if token[0] != expecting[-1]:
            # closes something further out: whatever's open inside it was never closed
            error = error or f"missing {chr(expecting[-1])!r}"
            while expecting[-1] != token[0]:
                expecting.pop()
        expecting.pop()
        if not expecting:
            return kind, match.end(), error
    return kind, end, f"no closing {chr(expecting[0])!r}"


class JsonNode:
    """A value in a JSON document, and where it came from.

    ``start`` and ``stop`` are the value's bytes; ``row_start`` is where its
    row starts, which for an object member is its key. ``label`` is how its
    parent knows it: the key, or the index.
    """

    __slots__ = (
        "buffer",
        "kind",
        "start",
        "stop",
        "error",
        "label",
        "key_start",
        "parent",
        "index",
        "depth",
        "_children",
        "_keys",
        "_lines",
    )

    def __init__(self, buffer, kind, start, stop, error=None, label=None, key_start=None, parent=None, index=0):
        self.buffer = buffer
        self.kind = kind
        self.start = start
        self.stop = stop
        self.error = error
        self.label = label
        self.key_start = key_start
        self.parent = parent
        self.index = index
        self.depth = parent.depth + 1 if parent else 0
        self._children: Optional[list[JsonNode]] = None
        self._keys: Optional[dict] = None
        # for JSON Lines: where each line's value starts and its line ends, parsed when it's needed
        self._lines: Optional[tuple[list[int], list[int]]] = None

    def __repr__(self) -> str:
        return f"JsonNode({self.kind} {self.label!r} {self.start}:{self.stop})"

    @property
    def hidden(self) -> bool:
        """A stream has no row of its own: its documents are the top rows."""
        return self.kind == "stream"

    @property
    def row_start(self) -> int:
        return self.start if self.key_start is None else self.key_start

    @property
    def container(self) -> bool:
        return self.kind in ("object", "array", "stream")

    @property
    def count(self) -> int:
        if self._lines is not None:
            return len(self._lines[0])
        return len(self.children)

    @property
    def children(self) -> list["JsonNode"]:
        if self._children is None:
            if self._lines is not None:
                self._children = [self.child(i) for i in range(self.count)]
            else:
                self._children = self._parse_children() if self.kind in ("object", "array") else []
        return self._children

    def child(self, index: int) -> "JsonNode":
        if self._lines is None:
            return self.children[index]
        if self._children is None:
            self._children = [None] * self.count
        if self._children[index] is None:
            self._children[index] = self._line(index)
        return self._children[index]

    def find(self, label) -> Optional[int]:
        """The index of the child known as ``label``."""
        if isinstance(label, int):
            return label if 0 <= label < self.count else None
        if self._keys is None:
            self._keys = {}
            for child in self.children:
                self._keys.setdefault(child.label, child.index)
        return self._keys.get(label)

    def index_at(self, position: int) -> Optional[int]:
        """The last child whose row starts at or before ``position``."""
        if self._lines is not None:
            index = bisect_right(self._lines[0], position) - 1
        else:
            index = bisect_right(self.children, position, key=lambda child: child.row_start) - 1
        return index if index >= 0 else None

    @property
    def path(self) -> str:
        """Where this is in the document, like ``$.users[3].name``."""
        if self.parent is None:
            return "$"
        if isinstance(self.label, int):
            return f"{self.parent.path}[{self.label}]"
        return f"{self.parent.path}.{self.label}"

    def source(self, size: int) -> str:
        """The start of this value's text, on one line."""
        raw = self.buffer[self.start : min(self.stop, self.start + size * 4)]
        return SPACES.sub(" ", raw.decode("utf-8", "replace"))

    @property
    def value(self):
        """The value, for strings, numbers, booleans and null."""
        try:
            return json.loads(self.buffer[self.start : self.stop])
        except ValueError:
            return None

    # editing

    @property
    def editable(self) -> bool:
        return not self.container and self.kind != "error" and self.stop > self.start

    def edit_text(self) -> str:
        """The value as it's edited: a string's text, or anything else's JSON."""
        if self.kind == "string" and self.error is None:
            return self.value
        return self.buffer[self.start : self.stop].decode("utf-8", "replace")

    def encode_edit(self, text: str) -> bytes:
        """The bytes to replace this value with, for edited ``text``. Raises ValueError if it isn't valid."""
        if self.kind == "string" and self.error is None:
            return encode_string(text)
        json.loads(text)
        return text.strip().encode("utf-8")

    def opening(self) -> Optional[tuple[type[Format], Region]]:
        """What this value can be opened as, in a level of its own: strings open as their text."""
        if self.kind != "string" or self.error is not None:
            return None
        return JsonString, Region(
            self.path, self.start, self.stop, self.start, self.stop - self.start, kind="string", openable=True
        )

    # parsing

    def _parse_children(self) -> list["JsonNode"]:
        buffer = self.buffer
        closed = self.error is None or not self.error.startswith("no closing")
        end = self.stop - 1 if closed else self.stop
        keyed = self.kind == "object"
        children: list[JsonNode] = []
        pos = self.start + 1
        while True:
            pos = _skip_space(buffer, pos, end)
            if pos >= end:
                break
            if buffer[pos] == ord(","):
                pos += 1
                continue
            label, key_start, problem = len(children), None, None
            if keyed:
                key = STRING.match(buffer, pos, end)
                bare = None if key else GARBAGE.match(buffer, pos, end)
                colon = _skip_space(buffer, (key or bare).end(), end) if key or bare else pos
                if key:
                    key_start = pos
                    label = _decode_key(key.group())
                elif bare and colon < end and buffer[colon] == ord(":"):
                    # a key without quotes
                    key_start = pos
                    label = bare.group().decode("utf-8", "replace")
                    problem = "keys need quotes"
                else:
                    problem = "expected a key"
                if key_start is not None:
                    if colon < end and buffer[colon] == ord(":"):
                        pos = _skip_space(buffer, colon + 1, end)
                    else:
                        pos = colon
                        problem = problem or "expected ':'"
            if pos >= end or buffer[pos] == ord(","):
                # a key with no value
                children.append(self._node("error", pos, pos, problem or "missing value", label, key_start, children))
                continue
            kind, stop, error = _value(buffer, pos, end)
            child = self._node(kind, pos, stop, problem or error, label, key_start, children)
            children.append(child)
            pos = _skip_space(buffer, stop, end)
            if pos < end and buffer[pos] != ord(","):
                child.error = child.error or f"expected ',' or {'}' if keyed else ']'!r}"
        return children

    def _node(self, kind, start, stop, error, label, key_start, siblings) -> "JsonNode":
        return JsonNode(self.buffer, kind, start, stop, error, label, key_start, self, len(siblings))

    def _line(self, index: int) -> "JsonNode":
        start, end = self._lines[0][index], self._lines[1][index]
        kind, stop, error = _value(self.buffer, start, end)
        if error is None and _skip_space(self.buffer, stop, end) < end:
            error = "more after the value on this line"
        return JsonNode(self.buffer, kind, start, stop, error, index, None, self, index)


def _decode_key(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return raw[1:-1].decode("utf-8", "replace")


def parse(buffer: bytes) -> JsonNode:
    """The tree for a JSON document or stream."""
    size = len(buffer)
    pos = _skip_space(buffer, 0, size)
    if pos >= size:
        return JsonNode(buffer, "error", 0, 0, "empty")
    kind, stop, error = _value(buffer, pos, size)
    after = _skip_space(buffer, stop, size)
    if after >= size:
        return JsonNode(buffer, kind, pos, stop, error)
    stream = JsonNode(buffer, "stream", 0, size)
    rest = buffer.find(b"\n", stop)
    one_line = buffer.find(b"\n", pos, stop) < 0
    if one_line and rest >= 0 and not buffer[stop:rest].strip():
        stream._lines = _lines(buffer)
        return stream
    # values one after another, not one to a line
    children = []
    while pos < size:
        kind, stop, error = _value(buffer, pos, size)
        children.append(JsonNode(buffer, kind, pos, stop, error, len(children), None, stream, len(children)))
        pos = _skip_space(buffer, stop, size)
    stream._children = children
    return stream


def _lines(buffer: bytes) -> tuple[list[int], list[int]]:
    """Where each non-blank line's text starts and the line ends."""
    starts, ends = [], []
    pos = 0
    size = len(buffer)
    while pos < size:
        end = buffer.find(b"\n", pos)
        end = size if end < 0 else end
        start = _skip_space(buffer, pos, end)
        if start < end:
            starts.append(start)
            ends.append(end)
        pos = end + 1
    return starts, ends


def encode_string(text: str) -> bytes:
    """``text`` as a JSON string, with characters kept as they are (bar lone surrogates, which UTF-8 can't hold)."""
    literal = json.dumps(text, ensure_ascii=False)
    literal = SURROGATE.sub(lambda match: f"\\u{ord(match.group()):04x}", literal)
    return literal.encode("utf-8")


class JsonString(Format):
    """A JSON string's text: open one to edit it as text, and it's escaped again when it's committed."""

    name = "json string"
    contents = "text"
    can_resize = True
    # strings aren't listed: there can be millions of them
    listed = False

    @classmethod
    def find_region(cls, data: Data, data_start: int, name: str) -> Optional[Region]:
        size = 4096
        while True:
            raw = data.read(data_start, size)
            match = STRING.match(raw)
            if match:
                stop = data_start + match.end()
                return Region(name, data_start, stop, data_start, stop - data_start, kind="string", openable=True)
            # strings can't hold line breaks: if there's one, it isn't a string any more
            if len(raw) < size or b"\n" in raw:
                return None
            size *= 4

    @classmethod
    def open(cls, data: Data, region: Region) -> Data:
        try:
            text = json.loads(data.read(region.data_start, region.data_size))
        except ValueError as e:
            raise EditError(f"not a valid string: {e}") from e
        return Buffer(BytesSource(text.encode("utf-8", "surrogatepass")), parent=data)

    @classmethod
    def encode(cls, data: Data, region: Region, contents: Data) -> bytes:
        return encode_string(contents.read(0, contents.size).decode("utf-8", "surrogatepass"))

    @classmethod
    def fixup(cls, data: Data, region: Region, size: int) -> Region:
        # nothing in JSON records how long a string is
        return replace(region, stop=region.data_start + size, data_size=size)


class Json(Format):
    """JSON, as a tree."""

    name = "json"
    contents = "tree"
    has_regions = True
    can_resize = True
    nests = False

    @classmethod
    def sniff(cls, data: Data) -> float:
        head = data.read(0, SAMPLE)
        pos = _skip_space(head, 0, len(head))
        if pos >= len(head) or head[pos] not in b"{[":
            return 0.0
        kind, stop, error = _value(head, pos, len(head))
        # the sample may cut it off, but it mustn't be wrong before that
        if error is not None and not (error.startswith("no closing") and len(head) == SAMPLE):
            return 0.0
        children = JsonNode(head, kind, pos, stop, error).children
        if any(child.error for child in children[:-1]) or (children and children[-1].error and stop < len(head)):
            return 0.0
        return 0.7

    @classmethod
    def regions(cls, data: Data) -> list[Region]:
        return [Region("json", 0, data.size, 0, data.size, kind="json", openable=True)]

    @classmethod
    def tree(cls, data: Data) -> JsonNode:
        return parse(data.read(0, data.size))

    @classmethod
    def fixup(cls, data: Data, region: Region, size: int) -> Region:
        return replace(region, stop=region.data_start + size, data_size=size)
