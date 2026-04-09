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

- Copies one or many source folders to a destination.
- Works with local drives and remote destinations (`user@host:/path`).
- Automatically retries after errors or disconnects.
- Lets you pause and resume a running transfer.
- Shows progress, speed, and time remaining.
- Can check destination free space before starting.
- Can test SSH before starting remote copies.

---

## Why People Use It

- Large backups that run for hours or days.
- Home server / NAS backups over shaky links.
- Remote copies where normal rsync commands are easy to mistype.
- Anyone who wants a safer, simpler workflow than running shell commands by hand.

---

## Quick Start

1. Add your source folder(s).
2. Set destination (local path or `user@host:/path`).
3. For remote targets, run **Test SSH**.
4. Run **Dest. space** to confirm capacity.
5. Click **Start sync**.

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
- A source scan improves progress accuracy on very large jobs.
- You can run a dry run first to preview behavior before writing files.
- The app writes troubleshooting events to `debug.log`.

## Releases

- Current release: `1.7.13`
- Version source: [`safecopi/__init__.py`](safecopi/__init__.py)
- Full history: [`CHANGELOG.md`](CHANGELOG.md)

---

<p align="center">
  <sub>Repository: <a href="https://github.com/UnDadFeated/SafeCopi">github.com/UnDadFeated/SafeCopi</a></sub>
</p>
