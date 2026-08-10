"""Entry point for ``python -m sqreader`` and for the compiled binary.

Nuitka compiles a *real* ``__main__`` module, so the packaged build needs this
file to exist rather than relying on the console-script shim setuptools
generates — that shim lives in the venv's ``bin/``, which never makes it into
a onefile artifact.

The import below is ABSOLUTE on purpose. Nuitka compiles this file as the
top-level ``__main__``, which has no parent package, so ``from .cli import
main`` raises "attempted relative import with no known parent package" — the
compile succeeds and the artifact dies on first run. The absolute form resolves
against the bundled ``sqreader`` package and works identically under
``python -m sqreader``.

Keep this module free of side effects beyond dispatching: it runs before
anything else in the binary.
"""
from __future__ import annotations

from sqreader.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
