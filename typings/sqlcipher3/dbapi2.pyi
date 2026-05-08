"""Typing stubs for :mod:`sqlcipher3.dbapi2`.

Runtime bindings expose SQLite-compatible APIs; inherit SQLite stubs so Pyright
sees ``executescript``, ``commit``, cursors, and exceptions.
"""

from sqlite3 import Connection as _SQLiteConnection
from sqlite3 import Error, OperationalError


class Connection(_SQLiteConnection):
    """Encrypted-database connection (SQLCipher)."""


def connect(database: str, *args: object, **kwargs: object) -> Connection: ...

__all__ = ["Connection", "Error", "OperationalError", "connect"]
