# SafeCopi

<p align="center">
  <a href="https://github.com/UnDadFeated/SafeCopi/releases"><img src="https://img.shields.io/github/v/release/UnDadFeated/SafeCopi?sort=semver&amp;logo=github&amp;label=release" alt="GitHub release"></a>
  <a href="https://github.com/UnDadFeated/SafeCopi"><img src="https://img.shields.io/github/stars/UnDadFeated/SafeCopi?style=flat&amp;logo=github" alt="GitHub stars"></a>
  <a href="https://github.com/UnDadFeated/SafeCopi/commits/main/"><img src="https://img.shields.io/github/last-commit/UnDadFeated/SafeCopi/main?logo=github" alt="Last commit"></a>
  <br>
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey" alt="Platform">
  <img src="https://img.shields.io/badge/rsync-OpenSSH-333333?logo=openssh" alt="rsync over SSH">
</p>

SafeCopi helps you copy and back up files safely, especially when your network is unreliable.

If your transfer keeps failing because Wi-Fi drops, VPN disconnects, or a remote server goes offline for a moment, SafeCopi is made for that.

---

## What It Does

- Copies one or many sources (local folders and/or remote paths) to a destination.
- Works with local drives and remote destinations (`user@host:/path`).
- Automatically retries after errors or disconnects.
- Lets you pause and resume a running transfer.
- Shows progress, speed, and time remaining.
- Can check destination free space before starting.
- Can test SSH from the **Add/Edit source** dialog for remote sources, and **Test SSH** next to the destination when it is remote.

---

## Why People Use It

- Large backups that run for hours or days.
- Home server / NAS backups over shaky links.
- Remote copies where normal rsync commands are easy to mistype.
- Anyone who wants a safer, simpler workflow than running shell commands by hand.

---

## Quick Start

1. Use **Add source…** for each entry: pick **Local folder** or **Remote (SSH / SFTP)** (`user@host:/path` / `sftp://…`); optional password per remote row is session-only (not saved).
2. Set destination (local path or `user@host:/path`).
3. For a remote destination, use **Test SSH** under **Browse…** if you want to verify SSH (optional **Dest. password**).
4. For remote sources, use **Test SSH connection…** in **Add source…** / **Edit…** if you want to verify before adding or saving the row.
5. Run **Dest. space** to confirm capacity.
6. Click **Start sync**.

SafeCopi will keep retrying if the connection drops, based on your retry settings.

---

## Requirements

- Python 3.10 or newer
- `rsync` and `ssh` installed on your system
- Python packages in [`requirements.txt`](requirements.txt)
- Optional: `sshpass` if you do password-based SSH without keys

---

## Install

```bash
git clone https://github.com/UnDadFeated/SafeCopi.git
cd SafeCopi
python -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

---

## Running

```bash
cd SafeCopi
.venv/bin/python -m safecopi
```

or:

```bash
./run-safecopi
```

---

## Helpful Notes

- If you use remote paths, SSH keys are recommended.
- **Start sync** checks destination free space, then runs rsync. Progress and size hints come from rsync’s own output (percent, bytes, and queue counters), not from a separate folder walk.
- You can run a dry run first to preview behavior before writing files.
- The app writes troubleshooting events to `debug.log`.
- Local source folders are copied as folders into the destination (for example, selecting `Sofie Backup/` creates `Destination/Sofie Backup/`).
- Change a listed source with **Edit…** or by double-clicking the row (same dialog as **Add source…**, with fields prefilled).
- Source list **icons** (remote rows only): hollow circle when no password is set; grey dot when a password is set but not yet tested; green check or red **X** after the last **Test SSH connection…** result from **Add/Edit source** for that row. Passwords are **not** saved—only paths and other options are stored in settings.

## Releases

- Current release: `2.1.3`
- Version source: [`safecopi/__init__.py`](safecopi/__init__.py)
- Full history: [`CHANGELOG.md`](CHANGELOG.md)
- Version bumps for this repo are applied only when the maintainer requests a release (e.g. by saying **push update**); routine work does not change the tagged version by default.

---

<p align="center">
  <sub>Repository: <a href="https://github.com/UnDadFeated/SafeCopi">github.com/UnDadFeated/SafeCopi</a></sub>
</p>
