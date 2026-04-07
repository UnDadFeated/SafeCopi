"""Launch SafeCopi: `python -m safecopi`."""

from __future__ import annotations

import sys


def main() -> int:
    from safecopi.main_window import run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
