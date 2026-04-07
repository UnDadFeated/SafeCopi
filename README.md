# SafeCopi

Desktop application for **synchronizing local directories to remote hosts** over SSH using **rsync**. It targets long-running backups and archives: interactive preflight checks, parsed transfer progress, bounded I/O timeouts, and automatic retries on failure.

| Attribute | Details |
| :--- | :--- |
| Stack | Python 3.10+, PySide6 |
| Transport | OpenSSH + rsync |
| Version | [`safecopi/__init__.py`](safecopi/__init__.py) · [`CHANGELOG.md`](CHANGELOG.md) |

---

## Overview

SafeCopi wraps a production-style rsync workflow in a fixed-layout, dark-themed window (720×880). It emphasizes **visibility** (source scan, remote free space, SSH test) and **resilience** (`--info=progress2`, optional `--partial`, configurable timeouts and retry delays) without requiring a hand-maintained shell script.

Session fields—including paths, dry-run, recursion, bandwidth limit, and extra rsync arguments—persist via `QSettings`. The SSH password field is **never** written to disk.

---

## Features

- **Preflight** — Walk the source tree for file count and total size (incremental UI updates for slow or network-backed paths).
- **Remote space** — Query free space with `df` over SSH, walking to parent paths when the destination directory does not exist yet.
- **SSH** — Password-capable transport aligned with **Test SSH** (`PubkeyAuthentication=no` when a password is supplied so keyboard-interactive/password auth is reachable; `SSH_ASKPASS` / optional `sshpass`).
- **Sync** — Builds rsync argv centrally (`--info=progress2`, `--info=name0`, timeouts, archive-style modes); worker loop retries on non-zero exit until success or user stop.
- **Progress** — Transfer panel driven by parsed rsync progress lines; activity log filters routine stderr noise while retaining errors and milestones.

---

## Requirements

| Dependency | Notes |
| --- | --- |
| Python | 3.10 or newer |
| Packages | [`requirements.txt`](requirements.txt) (PySide6) |
| System tools | `rsync`, `ssh` on `PATH` |
| Password auth (optional) | `sshpass` recommended when not using keys; on Arch/CachyOS: `sudo pacman -S sshpass` |

SSH **public keys** are recommended for unattended copies. If authentication fails with repeated “Permission denied”, verify credentials and server policy with `ssh user@host` from a terminal.

---

## Installation

Create a virtual environment and install dependencies (works in **bash**, **zsh**, and **fish** without relying on `activate` for day-to-day runs):

```bash
cd /path/to/SafeCopi
python -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

Alternatively, from the repository root:

```sh
./bootstrap
```

**Fish:** Do not `source .venv/bin/activate` (that file targets bash/zsh). Prefer `.venv/bin/python` directly, or `source .venv/bin/activate.fish` only if you want a traditional activate workflow.

**PEP 668 (e.g. Arch):** Use the venv interpreter explicitly (`.venv/bin/pip`, `.venv/bin/python`) so installs do not hit the system-managed environment error.

---

## Running the application

```bash
cd /path/to/SafeCopi
.venv/bin/python -m safecopi
```

Or, after `chmod +x run-safecopi`:

```bash
./run-safecopi
```

---

## Usage

1. **Source** — Local directory (including mounted NAS paths).
2. **Destination** — Rsync-style URI, e.g. `user@host:/mnt/backup/Archive/`.
3. **Trailing slash** — Rsync semantics differ for `path` vs `path/`; the UI calls this out.
4. **Check destination space** — Recommended before large runs.
5. **Scan source** — Optional; counts files and sums sizes with periodic UI updates (~250 files or ~200 ms). Totals reflect `getsize` sums (may differ slightly from `du` for sparse or special files).
6. **Dry run** — Adds `--dry-run` (no writes).
7. **Subdirectories** — Recursive copy (`-ah`) is default; disabling limits to the top level (`-hlptgoD` without `-r`).
8. **Partial files** — Choose resume (`--partial`) or full re-copy of interrupted files on the next run.
9. **Sync log** — High-signal lines only; per-file path spam is suppressed via `--info=name0` and filtering while the progress panel shows overall transfer state.

---

## Development

Install test dependencies and run the suite headlessly:

```bash
.venv/bin/pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/
```

---

## GitHub repository metadata

Use the following in the repository **About** settings (description, topics, and optional website).

**Description** (copy into the *Description* field, ≤350 characters):

```text
Desktop app to sync local directories to remote paths over SSH with rsync—progress UI, retries, timeouts, and preflight checks (space, SSH). Built with Python and PySide6.
```

**Topics** (suggested):

`rsync` `ssh` `backup` `sync` `pyside6` `pyqt` `python` `linux` `gui` `file-transfer`

**Website** (optional): leave blank or set to the repository URL if you do not publish a separate site.

---

## Changelog

Release history: [`CHANGELOG.md`](CHANGELOG.md).
