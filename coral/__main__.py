"""Module entrypoint for ``python -m coral`` (delegates to the Typer CLI)."""

from coral.cli import app

if __name__ == "__main__":
    app()
