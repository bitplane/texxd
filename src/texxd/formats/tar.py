"""Uncompressed tar archives."""

import stat
import tarfile
import time

from ..data import Data, DataReader
from ..log import get_logger
from . import Entry, Format

logger = get_logger(__name__)

BLOCK = 512

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
    has_entries = True

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
    def entries(cls, data: Data) -> list[Entry]:
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
                    out.append(cls._entry(info))
        except tarfile.TarError as e:
            logger.warning(f"not a readable tar: {e}")
        return out

    @staticmethod
    def _entry(info: tarfile.TarInfo) -> Entry:
        size = info.size if info.isreg() else 0
        padded = -(-size // BLOCK) * BLOCK
        kind = KINDS.get(info.type, "other")
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(info.mtime))
        name = info.name
        if info.issym() or info.islnk():
            name = f"{name} -> {info.linkname}"
        return Entry(
            name=name,
            start=info.offset,
            stop=info.offset_data + padded,
            data_start=info.offset_data,
            data_size=size,
            kind=kind,
            openable=info.isreg(),
            info=(stat.filemode(FILE_TYPES.get(info.type, stat.S_IFREG) | info.mode), mtime),
        )
