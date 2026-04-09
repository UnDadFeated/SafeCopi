# SafeCopi

<p align="center">
  <a href="https://github.com/UnDadFeated/SafeCopi/releases"><img src="https://img.shields.io/github/v/release/UnDadFeated/SafeCopi?sort=semver&amp;logo=github&amp;label=release" alt="GitHub release"></a>
  <a href="https://github.com/UnDadFeated/SafeCopi"><img src="https://img.shields.io/github/stars/UnDadFeated/SafeCopi?style=flat&amp;logo=github" alt="GitHub stars"></a>
  <a href="https://github.com/UnDadFeated/SafeCopi/commits/main/"><img src="https://img.shields.io/github/last-commit/UnDadFeated/SafeCopi/main?logo=github" alt="Last commit"></a>
  <br>
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/GUI-PySide6-41CD52?logo=qt&amp;logoColor=white" alt="PySide6">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey" alt="Platform">
  <img src="https://img.shields.io/badge/rsync-OpenSSH-333333?logo=openssh" alt="rsync over SSH">
</p>

**SafeCopi** is a desktop application for **synchronizing directories to remote hosts** with **OpenSSH** and **rsync**. It is aimed at backups and long-running archive jobs: preflight checks (tree scan, remote free space, SSH connectivity), parsed transfer progress, bounded I/O timeouts, automatic retries, and an activity log with timestamps.

The UI is a fixed-layout, dark-themed window (720×880). **Preflight** and **File transfer** share the main row (~30% / ~70% width). Session options persist via **Qt `QSettings`**; **SSH password fields are never saved to disk**.

---

## Contents

- [Capabilities](#capabilities)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running](#running)
- [Usage](#usage)
- [Development](#development)
- [Releases & history](#releases--history)

---

## Capabilities

| Area | Behavior |
| :--- | :--- |
| **Sources** | One or more **local** folders (up to **64**); each appears under the destination by folder name. A **single** remote source `user@host:/path` is supported and **cannot** be combined with extra list entries. |
| **Preflight** | Optional recursive scan of all listed local trees (counts and byte totals, throttled UI updates). While scanning, path fields, rsync options, **Test SSH**, **Dest. space**, and **Start sync** are disabled. |
| **Diagnostics** | Append-only **`debug.log`** under Qt’s app config dir (often ``~/.config/SafeCopi/SafeCopi/debug.log`` on Linux when org/app are both ``SafeCopi``): **last 3 app sessions** retained (rotation on startup), **ms-resolution** timestamps, session/settings/preflight/sync milestones, SSH/space/update **error excerpts**, and **shutdown** signals where possible — **not** rsync transfer/file listings. |

| **Guide pulse** | Idle highlight follows setup order: **Add folder** (if no sources) → fix invalid local paths → destination **Browse** when empty → **Scan source** (optional, only after destination is set for local folders) → **Test SSH** / **Check destination space** when needed → **Start sync**. Invalid multi-source mixes (local + remote in one list) target **Remove**. |
| **Remote space** | `df` over SSH, walking to parents when the destination path does not exist yet. |
| **SSH** | Password-capable when required (`PubkeyAuthentication=no` with password, `SSH_ASKPASS`, optional `sshpass`). **Test SSH** uses the destination host if remote, otherwise the source host. |
| **Sync** | Centralized rsync argv (`--info=progress2`, timeouts, archive-style modes). **If file exists** defaults to **Skip (if filename and size is same)** (`--size-only`). Multi-source runs are **sequential** in one session. **Pause** / **Resume** (POSIX **SIGSTOP**/**SIGCONT** or deferred retry). Retries until success, failure, or user stop. |
| **Progress** | 0–10000 bar with percentage, bytes remaining (vs last scan when available), throughput, and ETA; monotonic behavior. For multi-source sessions, one cumulative bar runs from 0% to 100% across all source folders (no per-source reset). The activity log records **rsync errors/warnings and ``rsync:`` diagnostics** only — not per-file ``-v`` chatter or routine transfer totals. |

---

## Requirements

| Component | Notes |
| :--- | :--- |
| **Python** | 3.10 or newer |
| **Python packages** | [`requirements.txt`](requirements.txt) (PySide6) |
| **System** | `rsync` and `ssh` on `PATH` |
| **Optional** | `sshpass` for password-based SSH when keys are not used (e.g. Arch/CachyOS: `sudo pacman -S sshpass`) |

SSH **public keys** are recommended for unattended runs. If you see repeated “Permission denied”, verify with `ssh user@host` in a terminal.

---

## Installation

```bash
git clone https://github.com/UnDadFeated/SafeCopi.git
cd SafeCopi
python -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

Alternatively, from the repository root:

```bash
./bootstrap
```

**Fish:** Prefer `.venv/bin/python` directly, or `source .venv/bin/activate.fish`. Do not use the bash-oriented `activate` script.

**PEP 668 (system Python, e.g. Arch):** Always install into the venv (`.venv/bin/pip`, `.venv/bin/python`).

---

## Running

```bash
cd SafeCopi
.venv/bin/python -m safecopi
```

Or, after `chmod +x run-safecopi`:

```bash
./run-safecopi
```

---

## Usage

1. **Source** — Add one or more **local** directories. With multiple entries, each syncs to `destination/FolderName/…`. One **user@host:/path** remote source is allowed **alone** (not with extra list rows).
2. **Destination** — Rsync-style target, e.g. `user@host:/mnt/backup/Archive/`.
3. **Trailing slashes** — Rsync semantics differ for `path` vs `path/`; see the in-app Preflight note when typing paths.
4. **Check destination space** — Recommended before large transfers.
5. **Scan source** — Optional; aggregates all listed local trees. Totals use file sizes (may differ slightly from `du` for sparse or special files).
6. **Dry run** — Adds `--dry-run` (no writes).
7. **Subdirectories** — Recursive copy (`-ah`) is the default; turning it off limits to the top level (`-hlptgoD` without `-r`).
8. **Partial files** — Choose `--partial` for resumable interrupted files where appropriate.
9. **Extra rsync arguments** — Append flags as needed; add `--info=name0` to reduce per-file path verbosity in the UI if desired.

---

## Development

Install dev dependencies and run tests headlessly:

```bash
.venv/bin/pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/
```

---

## Releases & history

- **Current version:** [`safecopi/__init__.py`](safecopi/__init__.py) (`__version__`)
- **Release notes:** [`CHANGELOG.md`](CHANGELOG.md)

**Suggested GitHub topics:** `rsync`, `ssh`, `backup`, `sync`, `pyside6`, `pyqt`, `python`, `linux`, `gui`, `file-transfer`

---

<p align="center">
  <sub>Repository: <a href="https://github.com/UnDadFeated/SafeCopi">github.com/UnDadFeated/SafeCopi</a></sub>
</p>
