"""texxd - A hex editor built with Textual."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("texxd")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = ["__version__"]
