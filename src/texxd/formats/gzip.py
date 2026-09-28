"""Gzip streams.

A gzip file is one or more members, each a header, a raw deflate stream,
then the CRC32 and size of the uncompressed data. Each member opens as its
decompressed contents; edits to them are written back by compressing them
again, keeping the member's header.
"""

import struct
import zlib
from dataclasses import replace

from ..data import Buffer, BytesSource, Data
from ..log import get_logger
from . import Format, Region

logger = get_logger(__name__)

MAGIC = b"\x1f\x8b\x08"
TRAILER = 8

# header flags
FHCRC = 0x02
FEXTRA = 0x04
FNAME = 0x08
FCOMMENT = 0x10

CHUNK = 64 * 1024


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


def _inflate(data: Data, start: int) -> tuple[bytes, int]:
    """Decompress the deflate stream at ``start``. Returns the bytes and where the stream ends."""
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    out = []
    at = start
    while not inflater.eof:
        chunk = data.read(at, CHUNK)
        if not chunk:
            raise GzipError("stream is truncated")
        out.append(inflater.decompress(chunk))
        at += len(chunk)
    return b"".join(out), at - len(inflater.unused_data)


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
                contents, end = _inflate(data, data_start)
            except (GzipError, zlib.error, struct.error) as e:
                logger.warning(f"gzip parse stopped early: {e}")
                break
            stop = end + TRAILER
            out.append(
                Region(
                    name or "(contents)",
                    start,
                    stop,
                    data_start,
                    stop - data_start,
                    kind="file",
                    openable=True,
                    info=(f"{len(contents)} bytes",),
                )
            )
            start = stop
        return out

    @classmethod
    def open(cls, data: Data, region: Region) -> Data:
        contents, _ = _inflate(data, region.data_start)
        return Buffer(BytesSource(contents), parent=data)

    @classmethod
    def encode(cls, data: Data, region: Region, contents: Data) -> bytes:
        """A new deflate stream and trailer for the contents; the header stays as it is."""
        deflater = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
        out = []
        crc = 0
        for offset in range(0, contents.size, CHUNK):
            chunk = contents.read(offset, CHUNK)
            crc = zlib.crc32(chunk, crc)
            out.append(deflater.compress(chunk))
        out.append(deflater.flush())
        out.append(struct.pack("<II", crc, contents.size & 0xFFFFFFFF))
        return b"".join(out)

    @classmethod
    def fixup(cls, data: Data, region: Region, size: int) -> Region:
        # the header doesn't record the size, and encode() wrote the trailer
        return replace(region, stop=region.data_start + size, data_size=size)
