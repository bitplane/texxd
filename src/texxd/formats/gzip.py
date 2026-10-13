"""Gzip streams.

A gzip file is one or more members, each a header, a raw deflate stream,
then the CRC32 and size of the uncompressed data. Each member opens as its
decompressed contents; edits to them are written back by compressing them
again, keeping the member's header.
"""

import struct
import zlib
from collections.abc import Iterator
from dataclasses import replace

from ..data import Buffer, Data, EditError, Source, TemporarySource
from ..log import get_logger
from . import Format, Region
from .stream import CHUNK, Decoded, decode_chunks

logger = get_logger(__name__)

MAGIC = b"\x1f\x8b\x08"
TRAILER = 8

# header flags
FHCRC = 0x02
FEXTRA = 0x04
FNAME = 0x08
FCOMMENT = 0x10


class GzipError(Exception):
    pass


def _read_header(data: Data, start: int) -> tuple[int, str]:
    """Parse the member header at ``start``. Returns where the deflate stream starts, and the stored name."""
    header = data.read(start, 10)
    if len(header) < 10 or header[:3] != MAGIC:
        raise GzipError("not a gzip header")
    flags = header[3]
    at = start + 10
    if flags & FEXTRA:
        (length,) = struct.unpack("<H", data.read(at, 2))
        at += 2 + length
    name = ""
    for flag in (FNAME, FCOMMENT):
        if flags & flag:
            end = at
            while True:
                chunk = data.read(end, 256)
                if not chunk:
                    raise GzipError("header runs off the end")
                zero = chunk.find(b"\0")
                if zero >= 0:
                    end += zero
                    break
                end += len(chunk)
            if flag == FNAME:
                name = data.read(at, end - at).decode("latin-1")
            at = end + 1
    if flags & FHCRC:
        at += 2
    return at, name


def _inflate(data: Data, start: int, result: Decoded) -> Iterator[bytes]:
    """Yield contents and validate their trailer, recording the end of the member."""
    yield from decode_chunks(data, start, zlib.decompressobj(-zlib.MAX_WBITS), result)
    trailer = data.read(result.stop, TRAILER)
    if len(trailer) != TRAILER:
        raise GzipError("trailer is truncated")
    crc, size = struct.unpack("<II", trailer)
    if crc != result.crc or size != result.size & 0xFFFFFFFF:
        raise GzipError("invalid checksum or uncompressed size")
    result.stop += TRAILER


class Gzip(Format):
    """A gzip stream: each member opens as its decompressed contents."""

    name = "gzip"
    has_regions = True
    can_resize = True

    @classmethod
    def sniff(cls, data: Data) -> float:
        return 0.9 if data.read(0, 3) == MAGIC else 0.0

    @classmethod
    def regions(cls, data: Data) -> list[Region]:
        out = []
        start = 0
        while start < data.size and data.read(start, 3) == MAGIC:
            try:
                data_start, name = _read_header(data, start)
                result = Decoded()
                for _ in _inflate(data, data_start, result):
                    pass
            except (GzipError, ValueError, zlib.error, struct.error) as e:
                logger.warning(f"gzip parse stopped early: {e}")
                break
            stop = result.stop
            out.append(
                Region(
                    name or "(contents)",
                    start,
                    stop,
                    data_start,
                    stop - data_start,
                    kind="file",
                    openable=True,
                    info=(f"{result.size} bytes",),
                )
            )
            start = stop
        return out

    @classmethod
    def open(cls, data: Data, region: Region) -> Data:
        try:
            source = TemporarySource(_inflate(data, region.data_start, Decoded()))
        except (GzipError, ValueError, zlib.error, struct.error) as e:
            raise EditError(f"couldn't decode gzip: {e}") from e
        return Buffer(source, parent=data)

    @classmethod
    def _encoded_chunks(cls, contents: Data) -> Iterator[bytes]:
        """A new deflate stream and trailer for the contents; the header stays as it is."""
        deflater = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
        crc = 0
        for offset in range(0, contents.size, CHUNK):
            chunk = contents.read(offset, CHUNK)
            crc = zlib.crc32(chunk, crc)
            yield deflater.compress(chunk)
        yield deflater.flush()
        yield struct.pack("<II", crc, contents.size & 0xFFFFFFFF)

    @classmethod
    def encode(cls, data: Data, region: Region, contents: Data) -> Source:
        """Stream a new deflate payload and trailer to disk; keep the header."""
        try:
            return TemporarySource(cls._encoded_chunks(contents))
        except (ValueError, zlib.error) as e:
            raise EditError(f"couldn't encode gzip: {e}") from e

    @classmethod
    def fixup(cls, data: Data, region: Region, size: int) -> Region:
        # the header doesn't record the size, and encode() wrote the trailer
        return replace(region, stop=region.data_start + size, data_size=size)
