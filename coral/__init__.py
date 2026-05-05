"""Coral: local-first browser session bridge for AI agents."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__: str = version("coral")
except PackageNotFoundError:
    __version__ = "0.0.0"
