"""Text in some character encoding.

Text opens as UTF-8, whatever it's stored as. Text that's already UTF-8 (or
ASCII) opens in place, so its bytes line up with the hex; anything else is
decoded into a buffer of its own and encoded again when it's committed.
Bytes that aren't valid in the encoding survive the trip unchanged.
"""

import codecs
from dataclasses import replace
from typing import Optional

import chardet

from ..data import Buffer, BytesSource, Data, EditError, Window
from . import Format, Region

SAMPLE = 16 * 1024

# longest first, so UTF-32's BOM isn't taken for UTF-16's
BOMS = [
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
]

# encodings that are already UTF-8, so the text is the bytes themselves
IN_PLACE = {"utf-8", "ascii"}

# bytes that turn up in text files, though they're control characters
TEXT_CONTROLS = set(b"\t\n\r\f\b\x1b")

# offered when choosing an encoding, after whatever was detected
COMMON = [
    "utf-8",
    "latin-1",
    "cp1252",
    "utf-16-le",
    "utf-16-be",
    "utf-32-le",
    "shift_jis",
    "euc-jp",
    "gb18030",
    "big5",
    "euc-kr",
    "koi8-r",
    "cp1251",
    "cp437",
    "mac-roman",
    "ascii",
]


def normalize(encoding: str) -> str:
    """The standard name for an encoding, with byte order made explicit."""
    name = codecs.lookup(encoding).name
    return {"utf-16": "utf-16-le", "utf-32": "utf-32-le"}.get(name, name)


def bom(data: Data) -> tuple[Optional[str], int]:
    """The encoding a byte order mark at the start says, and how long the mark is."""
    head = data.read(0, 4)
    for mark, encoding in BOMS:
        if head.startswith(mark):
            return encoding, len(mark)
    return None, 0


def detect(data: Data) -> Optional[tuple[str, float]]:
    """Guess the encoding of ``data`` if it looks like text: (encoding, confidence), or None."""
    encoding, _ = bom(data)
    if encoding:
        return encoding, 0.8
    sample = data.read(0, SAMPLE)
    if not sample or b"\0" in sample:
        return None
    controls = sum(1 for byte in sample if byte < 0x20 and byte not in TEXT_CONTROLS)
    if controls > len(sample) // 100:
        return None
    try:
        sample.decode("utf-8")
        return "utf-8", 0.5
    except UnicodeDecodeError as e:
        # a character cut off by the end of the sample is fine
        if e.start >= len(sample) - 3 and len(sample) == SAMPLE:
            return "utf-8", 0.5
    guess = chardet.detect(sample)
    if not guess.get("encoding"):
        return None
    try:
        return normalize(guess["encoding"]), 0.3
    except LookupError:
        return None


class Text(Format):
    """Text. ``Text`` detects the encoding; ``Text.using(encoding)`` is text in a particular one."""

    name = "text"
    contents = "text"
    has_regions = True
    can_resize = True
    # text is text: don't offer to open it as text again
    nests = False
    encoding: Optional[str] = None
    _variants: dict[str, type["Text"]] = {}

    @classmethod
    def using(cls, encoding: str) -> type["Text"]:
        """Text in ``encoding``."""
        encoding = normalize(encoding)
        if encoding not in Text._variants:
            Text._variants[encoding] = type(f"Text[{encoding}]", (Text,), {"encoding": encoding})
        return Text._variants[encoding]

    @classmethod
    def suggestions(cls, data: Data) -> list[str]:
        """Encodings worth offering for ``data``: what it looks like first, then common ones."""
        guess = detect(data)
        first = [guess[0]] if guess else []
        return first + [normalize(name) for name in COMMON if normalize(name) not in first]

    @classmethod
    def sniff(cls, data: Data) -> float:
        guess = detect(data)
        return guess[1] if guess else 0.0

    @classmethod
    def regions(cls, data: Data) -> list[Region]:
        encoding = cls.encoding
        if encoding is None:
            guess = detect(data)
            if guess is None:
                return []
            encoding = guess[0]
        marked, header = bom(data)
        if marked != encoding:
            header = 0
        return [
            Region(
                f"text ({encoding})",
                0,
                data.size,
                header,
                data.size - header,
                kind="text",
                openable=True,
                value=encoding,
            )
        ]

    @classmethod
    def for_region(cls, region: Region) -> type[Format]:
        return cls if cls.encoding else cls.using(region.value)

    @classmethod
    def open(cls, data: Data, region: Region) -> Data:
        if cls.encoding in IN_PLACE:
            return Window(data, region.data_start, region.data_size)
        stored = data.read(region.data_start, region.data_size)
        text = stored.decode(cls.encoding, "surrogateescape")
        return Buffer(BytesSource(text.encode("utf-8", "surrogatepass")), parent=data)

    @classmethod
    def encode(cls, data: Data, region: Region, contents: Data) -> bytes:
        text = contents.read(0, contents.size).decode("utf-8", "surrogatepass")
        try:
            return text.encode(cls.encoding, "surrogateescape")
        except UnicodeEncodeError as e:
            raise EditError(f"{text[e.start : e.end]!r} can't be written in {cls.encoding}") from e

    @classmethod
    def fixup(cls, data: Data, region: Region, size: int) -> Region:
        # the text runs to the end, and nothing records its size
        return replace(region, stop=region.data_start + size, data_size=size)
