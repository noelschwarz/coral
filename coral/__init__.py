"""Coral: local-first browser session bridge for AI agents."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    # Distribution renamed to ``coralbridge`` in Track G; the import name
    # ``coral`` stayed put. Fall back to the legacy name if anyone happens
    # to be running an old install.
    __version__: str = version("coralbridge")
except PackageNotFoundError:
    try:
        __version__ = version("coral")
    except PackageNotFoundError:
        __version__ = "0.0.0"
