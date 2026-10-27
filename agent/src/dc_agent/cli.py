"""Compatibility entrypoint; implementation lives in entrypoints.cli."""

from .entrypoints.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
