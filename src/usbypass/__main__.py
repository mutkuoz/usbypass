"""Allow ``python -m usbypass ...`` to invoke the CLI."""

from usbypass.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
