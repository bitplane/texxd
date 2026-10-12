"""Compressed streams whose entire container is replaced on write-back.

Each concatenated stream opens separately. Listing counts decoded bytes without
keeping them; opening keeps the decoded contents in an editable buffer.
"""

import bz2
import lzma
import zlib
from dataclasses import replace

from ..data import Buffer, BytesSource, Data, EditError
from ..log import get_logger
from . import Format, Region

logger = get_logger(__name__)
CHUNK = 64 * 1024
LZMA_MEMORY_LIMIT = 128 * 1024 * 1024
DECODE_ERRORS = (ValueError, OSError, EOFError, lzma.LZMAError, zlib.error)


class Compressed(Format):
    """Shared listing, decoding and write-back for self-contained streams."""

    has_regions = True
    can_resize = True
    weak_magic = False
    minimum_size = 0

    @classmethod
    def matches(cls, data: Data, start: int) -> bool:
        raise NotImplementedError

    @classmethod
    def decompressor(cls):
        raise NotImplementedError

    @classmethod
    def compressor(cls, data: Data, region: Region):
        raise NotImplementedError

    @classmethod
    def _decode(cls, data: Data, start: int, collect: bool = False) -> tuple[bytes, int, int]:
        decoder = cls.decompressor()
        out = []
        size = 0
        at = start
        while not decoder.eof:
            if hasattr(decoder, "unconsumed_tail"):
                pending = decoder.unconsumed_tail
                needs_input = not pending
            else:
                pending = b""
                needs_input = decoder.needs_input
            if needs_input:
                pending = data.read(at, CHUNK)
                if not pending:
                    raise ValueError("stream is truncated")
                at += len(pending)
            chunk = decoder.decompress(pending, CHUNK)
            size += len(chunk)
            if collect:
                out.append(chunk)
        stop = at - len(decoder.unused_data)
        if stop - start < cls.minimum_size:
            raise ValueError("stream has no compressed payload")
        return b"".join(out), size, stop

    @classmethod
    def sniff(cls, data: Data) -> float:
        if not cls.matches(data, 0):
            return 0.0
        # LZMA-alone and zlib have weak headers: validate before offering them.
        if cls.weak_magic:
            try:
                cls._decode(data, 0)
            except DECODE_ERRORS:
                return 0.0
        return 0.9

    @classmethod
    def next_start(cls, data: Data, stop: int) -> int:
        return stop

    @classmethod
    def regions(cls, data: Data) -> list[Region]:
        out = []
        start = 0
        while start < data.size and cls.matches(data, start):
            try:
                _, size, stop = cls._decode(data, start)
            except DECODE_ERRORS as e:
                logger.warning(f"{cls.name} parse stopped early: {e}")
                break
            out.append(Region("(contents)", start, stop, start, stop - start, openable=True, info=(f"{size} bytes",)))
            start = cls.next_start(data, stop)
        return out

    @classmethod
    def open(cls, data: Data, region: Region) -> Data:
        try:
            contents, _, _ = cls._decode(data, region.data_start, collect=True)
        except DECODE_ERRORS as e:
            raise EditError(f"couldn't decode {cls.name}: {e}") from e
        return Buffer(BytesSource(contents), parent=data)

    @classmethod
    def encode(cls, data: Data, region: Region, contents: Data) -> bytes:
        try:
            encoder = cls.compressor(data, region)
            out = [encoder.compress(contents.read(offset, CHUNK)) for offset in range(0, contents.size, CHUNK)]
            out.append(encoder.flush())
            return b"".join(out)
        except DECODE_ERRORS as e:
            raise EditError(f"couldn't encode {cls.name}: {e}") from e

    @classmethod
    def fixup(cls, data: Data, region: Region, size: int) -> Region:
        return replace(region, stop=region.data_start + size, data_size=size)


class Bzip2(Compressed):
    """Bzip2 streams; write-back keeps the original block size."""

    name = "bzip2"

    @classmethod
    def matches(cls, data: Data, start: int) -> bool:
        head = data.read(start, 4)
        return len(head) == 4 and head[:3] == b"BZh" and b"1" <= head[3:] <= b"9"

    @classmethod
    def decompressor(cls):
        return bz2.BZ2Decompressor()

    @classmethod
    def compressor(cls, data: Data, region: Region):
        return bz2.BZ2Compressor(data.read(region.start + 3, 1)[0] - ord("0"))


class Xz(Compressed):
    """XZ streams; write-back keeps the original integrity check."""

    name = "xz"

    @classmethod
    def matches(cls, data: Data, start: int) -> bool:
        return data.read(start, 6) == b"\xfd7zXZ\x00"

    @classmethod
    def decompressor(cls):
        return lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=LZMA_MEMORY_LIMIT)

    @classmethod
    def compressor(cls, data: Data, region: Region):
        check = data.read(region.start + 7, 1)[0] & 0x0F
        return lzma.LZMACompressor(format=lzma.FORMAT_XZ, check=check)

    @classmethod
    def next_start(cls, data: Data, stop: int) -> int:
        # XZ permits stream padding in multiples of four bytes. Keep it outside
        # the editable span, so replacing a stream preserves its padding.
        while data.read(stop, 4) == b"\x00" * 4:
            stop += 4
        return stop


class Lzma(Compressed):
    """Legacy .lzma containers; write-back uses the standard encoder defaults."""

    name = "lzma"
    weak_magic = True
    # The 13-byte header must be followed by at least five range-coder bytes.
    # liblzma can accept a zero-size header alone as an empty stream.
    minimum_size = 18

    @classmethod
    def matches(cls, data: Data, start: int) -> bool:
        head = data.read(start, 14)
        # There is no magic number. Check the properties byte and dictionary
        # size first, then sniff() validates the complete stream.
        dictionary = int.from_bytes(head[1:5], "little")
        common_dictionary = dictionary in {size << bits for size in (2, 3) for bits in range(11, 26)}
        return (
            len(head) == 14
            and head[0] < 225
            and head[13] == 0
            and common_dictionary
            and 4096 <= dictionary <= 64 * 1024 * 1024
        )

    @classmethod
    def decompressor(cls):
        return lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=LZMA_MEMORY_LIMIT)

    @classmethod
    def compressor(cls, data: Data, region: Region):
        return lzma.LZMACompressor(format=lzma.FORMAT_ALONE)


class Zlib(Compressed):
    """Zlib-wrapped deflate; preset dictionary streams are not supported."""

    name = "zlib"
    weak_magic = True

    @classmethod
    def matches(cls, data: Data, start: int) -> bool:
        head = data.read(start, 2)
        return (
            len(head) == 2
            and head[0] & 0x0F == 8
            and head[0] >> 4 <= 7
            and not head[1] & 0x20
            and int.from_bytes(head, "big") % 31 == 0
        )

    @classmethod
    def decompressor(cls):
        return zlib.decompressobj()

    @classmethod
    def compressor(cls, data: Data, region: Region):
        bits = (data.read(region.start, 1)[0] >> 4) + 8
        return zlib.compressobj(wbits=max(9, bits))
