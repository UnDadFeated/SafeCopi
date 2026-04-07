# SafeCopi

**Quick start (no `activate`, works in fish):** `./bootstrap` then `.venv/bin/python -m safecopi` or `./run-safecopi`.

Desktop utility for **reliable local-to-remote copies** using `rsync` over SSH. It wraps the same resilient pattern as a long shell script: overall progress (`--info=progress2`), I/O timeouts, automatic retries after failures, optional `--partial` resumes, and preflight checks (source scan, destination free space, SSH connectivity).

The window uses a **fixed size** (non-resizable) layout with a dark theme (**720×880** px).

Paths, delays, dry-run, **recursive copy (subdirectories)**, partial mode, bandwidth limit, and extra rsync flags are **remembered** between sessions (`QSettings`). The SSH password field is **never** saved to disk.

## Requirements

- Python 3.10+
- PySide6 (see `requirements.txt`)
- `rsync` and `ssh` on `PATH`
- For remote destinations `user@host:/path`, **SSH keys** are recommended for unattended syncs (leave the password field empty). For password auth, use the **SSH password** field with **`sshpass`** installed (`sudo pacman -S sshpass` on Arch/CachyOS), or rely on **`SSH_ASKPASS`** GUI prompts when sshpass is missing. If you see “Permission denied, please try again” in the log, the server reached password authentication—usually wrong password or account policy; confirm with `ssh user@host` in a terminal.

## Install

**Fish shell:** Do **not** run `source .venv/bin/activate` — that file is for bash/zsh and breaks in fish. You do **not** need to “activate” the venv at all.

**Arch / PEP 668:** Use the venv’s `pip` and `python` explicitly. Plain `pip` / `python` after a failed activate still target the system interpreter and trigger *externally-managed-environment*.

One-time setup (copy-paste as-is; works in bash, zsh, **fish**, etc.):

```bash
cd /path/to/SafeCopi
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Optional: if you use fish and want activation, use only `source .venv/bin/activate.fish`, never `activate` (bash).

## Run

**Recommended (no activation):**

```bash
cd /path/to/SafeCopi
.venv/bin/python -m safecopi
```

Or use the launcher (after `chmod +x run-safecopi` once):

```bash
./run-safecopi
```

## Usage notes

1. **Source** must be a **local** directory (mounted NAS is fine).
2. **Destination** is an rsync URI, e.g. `user@host:/mnt/backup/Archive/`.
3. Use **Check destination space** before a large run; remote free space is queried with `df` over SSH (tries the destination path and parent directories if the folder does not exist yet).
4. **Scan source** walks the tree once, summing each file’s size (`getsize`) while counting—updates about every **250 files** or **200 ms** so **LAN / NAS** paths stay responsive (no blocking `du` first). Total size is the sum of file lengths (can differ slightly from `du` for sparse/special files).
5. **Trailing slash** on the source path changes rsync semantics (`dir/` vs `dir`); the UI reminds you of this.
6. **Dry run** adds `--dry-run` (no writes).
7. **Include subdirectories (recursive)** is on by default (`-ah` / full tree). Turn it off to sync only the top level of the source folder (rsync without `-r`).
8. **Partial transfers**: choose **Resume (--partial)** to continue interrupted files, or **Re-copy interrupted files (no --partial)** so a failed file is removed and copied again from the start on the next run.
9. Remote syncs pass explicit `ssh` options via `rsync -e` so behavior matches **Test SSH** (including `PubkeyAuthentication=no` so password auth can run when keys are not used).
10. During sync, **overall progress** (`--info=progress2` plus size/speed lines) updates the transfer panel; the log omits per-file paths and routine progress chatter so errors and milestones stay visible (`--info=name0` and stderr filtering).

## Testing (optional)

```bash
.venv/bin/pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/
```

## Version

Current version: **1.4.1** (see `safecopi/__init__.py` and `CHANGELOG.md`).
