"""Shared fixtures."""

import io
import tarfile

import pytest


def make_tar(files: dict) -> bytes:
    """Build an uncompressed tar in memory from {name: bytes}; a None value makes a directory."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.mtime = 0
            if data is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            else:
                info.size = len(data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


@pytest.fixture
def nested_tar(tmp_path):
    """outer.tar containing a dir, inner.tar (containing data.json) and README."""
    inner = make_tar({"data.json": b'{"a": 1}\n', "sub": None})
    outer = make_tar({"dir": None, "inner.tar": inner, "README": b"read me\n"})
    path = tmp_path / "outer.tar"
    path.write_bytes(outer)
    return path
