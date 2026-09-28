"""Uncompressed tar archives."""

import stat
import tarfile
import time
from dataclasses import replace

from ..data import Data, DataReader, ResizeError
from ..log import get_logger
from . import Format, Region

logger = get_logger(__name__)

BLOCK = 512
# where the size and checksum fields are in a header
SIZE_FIELD = slice(124, 136)
CHECKSUM_FIELD = slice(148, 156)

FILE_TYPES = {
    tarfile.DIRTYPE: stat.S_IFDIR,
    tarfile.SYMTYPE: stat.S_IFLNK,
    tarfile.CHRTYPE: stat.S_IFCHR,
    tarfile.BLKTYPE: stat.S_IFBLK,
    tarfile.FIFOTYPE: stat.S_IFIFO,
}

KINDS = {
    tarfile.REGTYPE: "file",
    tarfile.AREGTYPE: "file",
    tarfile.CONTTYPE: "file",
    tarfile.DIRTYPE: "dir",
    tarfile.SYMTYPE: "symlink",
    tarfile.LNKTYPE: "hardlink",
    tarfile.CHRTYPE: "char dev",
    tarfile.BLKTYPE: "block dev",
    tarfile.FIFOTYPE: "fifo",
}


class Tar(Format):
    """A tar archive: a series of 512 byte headers, each followed by the file's data."""

    name = "tar"
    has_regions = True
    can_resize = True

    @classmethod
    def sniff(cls, data: Data) -> float:
        block = data.read(0, BLOCK)
        if len(block) < BLOCK:
            return 0.0
        if block[257:262] == b"ustar":
            return 0.9
        # old v7 tars have no magic, but the header checksum still has to add up
        try:
            tarfile.TarInfo.frombuf(block, tarfile.ENCODING, "surrogateescape")
        except tarfile.HeaderError:
            return 0.0
        return 0.6

    @classmethod
    def regions(cls, data: Data) -> list[Region]:
        out = []
        try:
            with tarfile.open(fileobj=DataReader(data), mode="r:") as archive:
                while True:
                    try:
                        info = archive.next()
                    except tarfile.TarError as e:
                        logger.warning(f"tar parse stopped early: {e}")
                        break
                    if info is None:
                        break
                    out.append(cls._region(info))
        except tarfile.TarError as e:
            logger.warning(f"not a readable tar: {e}")
        return out

    @classmethod
    def fixup(cls, data: Data, region: Region, size: int) -> Region:
        """Rewrite the member's size and header checksum, and re-pad it to a whole block."""
        header_at = region.data_start - BLOCK
        if header_at > region.start and b" size=" in data.read(region.start, header_at - region.start):
            raise ResizeError("tar: the size is in a pax header, which can't be updated yet")
        if size >= 8**11:
            raise ResizeError("tar: too big for a size field")
        header = bytearray(data.read(header_at, BLOCK))
        header[SIZE_FIELD] = f"{size:011o}\0".encode()
        header[CHECKSUM_FIELD] = b" " * 8
        header[CHECKSUM_FIELD] = f"{sum(header):06o}\0 ".encode()
        data.write(header_at + SIZE_FIELD.start, bytes(header[SIZE_FIELD]))
        data.write(header_at + CHECKSUM_FIELD.start, bytes(header[CHECKSUM_FIELD]))

        old_padding = region.stop - region.data_stop
        new_padding = -size % BLOCK
        padding_at = region.data_start + size
        if new_padding > old_padding:
            data.insert(padding_at, bytes(new_padding - old_padding))
        elif new_padding < old_padding:
            data.delete(padding_at, old_padding - new_padding)
        return replace(region, stop=padding_at + new_padding, data_size=size)

    @staticmethod
    def _region(info: tarfile.TarInfo) -> Region:
        size = info.size if info.isreg() else 0
        padded = -(-size // BLOCK) * BLOCK
        kind = KINDS.get(info.type, "other")
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(info.mtime))
        name = info.name
        if info.issym() or info.islnk():
            name = f"{name} -> {info.linkname}"
        return Region(
            name=name,
            start=info.offset,
            stop=info.offset_data + padded,
            data_start=info.offset_data,
            data_size=size,
            kind=kind,
            openable=info.isreg(),
            info=(stat.filemode(FILE_TYPES.get(info.type, stat.S_IFREG) | info.mode), mtime),
        )
