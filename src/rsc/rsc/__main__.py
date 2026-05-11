"""Allow ``python -m rsc ...`` invocation."""

from rsc.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
