#!/usr/bin/env python3
"""SSH_ASKPASS helper: OpenSSH runs this in a separate process when no TTY is available."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit
    except ImportError:
        return 1

    _app = QApplication(["safecopi-ssh-askpass"])  # noqa: F841  # must exist for QInputDialog
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Password:"
    text, ok = QInputDialog.getText(
        None,
        "SSH authentication",
        prompt,
        QLineEdit.EchoMode.Password,
    )
    if ok and text is not None:
        sys.stdout.write(text)
        sys.stdout.flush()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
