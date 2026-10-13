"""Bounded-output decoding shared by compressed formats."""

import zlib
from collections.abc import Iterator
from dataclasses import dataclass

from ..data import Data

CHUNK = 64 * 1024


@dataclass
class Decoded:
    """Statistics populated while a stream is consumed."""

    size: int = 0
    stop: int = 0
    crc: int = 0


def decode_chunks(data: Data, start: int, decoder, result: Decoded) -> Iterator[bytes]:
    """Yield decoded chunks, recording the exact encoded end at EOF."""
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
        result.size += len(chunk)
        result.crc = zlib.crc32(chunk, result.crc)
        yield chunk
    result.stop = at - len(decoder.unused_data)
