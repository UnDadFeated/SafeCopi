"""Helpers for paths, remote destinations, and disk space."""

from __future__ import annotations

import math
import os
import re
import shlex
import time
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

_ASKPASS_WRAPPER: Optional[Path] = None

# Upper bounds for user-supplied rsync knobs (match UI where applicable; clamp API callers too).
RSYNC_TIMEOUT_SEC_MAX: int = 86400
RSYNC_RETRY_WAIT_SEC_MAX: int = 3600
EXTRA_RSYNC_ARG_LINE_MAX_CHARS: int = 32_768
EXTRA_RSYNC_ARG_COUNT_MAX: int = 512

# user@host:/path or host:/path — path may contain colons rarely; first : after host is separator
_RSYNC_REMOTE = re.compile(
    r"^(?:(?P<user>[^@]+)@)?(?P<host>[^:]+):(?P<path>.+)$"
)


@dataclass(frozen=True)
class RemoteTarget:
    """Parsed rsync/scp-style remote destination."""

    host: str
    path: str
    user: Optional[str] = None

    def ssh_spec(self) -> str:
        if self.user:
            return f"{self.user}@{self.host}"
        return self.host


def parse_rsync_destination(dest: str) -> Tuple[Optional[RemoteTarget], str]:
    """
    Return (RemoteTarget, local_path) for a destination string.
    If not remote, RemoteTarget is None and local_path is dest stripped.
    """
    s = dest.strip()
    m = _RSYNC_REMOTE.match(s)
    if not m:
        return None, s
    user = m.group("user")
    host = m.group("host")
    path = m.group("path")
    return RemoteTarget(host=host, path=path, user=user), path


def bytes_from_du_path(path: str) -> Optional[int]:
    """Return total size in bytes for a local directory using `du -sb` (GNU)."""
    p = Path(path).expanduser()
    if not p.exists():
        return None
    try:
        out = subprocess.run(
            ["du", "-sb", str(p)],
            capture_output=True,
            text=True,
            timeout=86400,
            check=False,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        first = out.stdout.strip().split()[0]
        return int(first)
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


def ensure_ssh_askpass_wrapper() -> Path:
    """
    Create (once) a small executable shell script that invokes ``ssh_askpass.py``
    with the same Python interpreter as the running app (so PySide6 is available).
    """
    global _ASKPASS_WRAPPER
    if _ASKPASS_WRAPPER is not None and _ASKPASS_WRAPPER.is_file():
        return _ASKPASS_WRAPPER
    askpass_py = Path(__file__).resolve().parent / "ssh_askpass.py"
    if not askpass_py.is_file():
        raise FileNotFoundError(f"Missing {askpass_py}")
    fd, name = tempfile.mkstemp(prefix="safecopi-askpass-", suffix=".sh")
    os.close(fd)
    path = Path(name)
    py = sys.executable
    path.write_text(f"#!/bin/sh\nexec '{py}' '{askpass_py}' \"$@\"\n", encoding="utf-8")
    path.chmod(0o700)
    _ASKPASS_WRAPPER = path
    return path


def local_free_bytes(path: str) -> Optional[int]:
    """Free space on the filesystem containing ``path`` (local)."""
    p = Path(path).expanduser()
    try:
        if p.exists():
            return shutil.disk_usage(str(p.resolve())).free
        cur = p.parent
        while cur != cur.parent:
            if cur.exists():
                return shutil.disk_usage(str(cur.resolve())).free
            cur = cur.parent
        return shutil.disk_usage("/").free
    except OSError:
        return None


def ssh_extra_argv(
    connect_timeout: int,
    batch_mode: bool,
    *,
    for_rsync: bool = False,
) -> List[str]:
    """
    OpenSSH options for direct ``ssh`` calls and for ``rsync -e`` transport.

    When ``batch_mode`` is False (password / kbd-interactive), public-key attempts
    are disabled so the client reaches password authentication and can use
    ``SSH_ASKPASS``.

    ``BatchMode=yes`` is applied only for standalone ``ssh`` (tests / ``df``), not
    for ``rsync -e``, so encrypted keys can still use the agent or askpass without
    being blocked on the rsync transport.
    """
    opts: List[str] = ["-o", f"ConnectTimeout={connect_timeout}"]
    if batch_mode:
        if not for_rsync:
            opts += ["-o", "BatchMode=yes"]
    else:
        opts += ["-o", "PubkeyAuthentication=no"]
        opts += ["-o", "GSSAPIAuthentication=no"]
        opts += ["-o", "PasswordAuthentication=yes"]
        opts += ["-o", "KbdInteractiveAuthentication=yes"]
        opts += ["-o", "NumberOfPasswordPrompts=6"]
        opts += ["-o", "PreferredAuthentications=keyboard-interactive,password"]
    return opts


def rsync_ssh_e_shell(
    connect_timeout: int,
    batch_mode: bool,
    password_for_sshpass: Optional[str] = None,
) -> str:
    """
    Single ``rsync -e`` string. If ``password_for_sshpass`` is set, ``sshpass -e`` is
    prepended (``SSHPASS`` must be set in the process environment).
    """
    ssh_inner = ["ssh"] + ssh_extra_argv(connect_timeout, batch_mode, for_rsync=True)
    if password_for_sshpass:
        ss = shutil.which("sshpass")
        if not ss:
            raise FileNotFoundError(
                "sshpass is not installed; install it (e.g. pacman -S sshpass) or clear the password field."
            )
        parts = [ss, "-e"] + ssh_inner
    else:
        parts = ssh_inner
    return " ".join(shlex.quote(p) for p in parts)


def run_ssh_command(
    remote: RemoteTarget,
    remote_cmd: str,
    *,
    connect_timeout: int = 15,
    batch_mode: bool = True,
    extra_env: Optional[Dict[str, str]] = None,
    password_for_sshpass: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Run a shell command on the remote host via SSH."""
    core = [
        "ssh",
        *ssh_extra_argv(connect_timeout, batch_mode, for_rsync=False),
        remote.ssh_spec(),
        remote_cmd,
    ]
    env = os.environ.copy()
    if password_for_sshpass:
        ss = shutil.which("sshpass")
        if not ss:
            raise FileNotFoundError(
                "sshpass is not installed; install it (e.g. pacman -S sshpass) or leave the password field empty to use the GUI askpass."
            )
        env["SSHPASS"] = password_for_sshpass
        ssh_cmd = [ss, "-e", *core]
        if extra_env:
            filtered = {
                k: v
                for k, v in extra_env.items()
                if k not in ("SSH_ASKPASS", "SSH_ASKPASS_REQUIRE")
            }
            env.update(filtered)
    else:
        ssh_cmd = core
        if extra_env:
            env.update(extra_env)
    # No TTY on stdin: if ssh inherits a terminal (e.g. app started from a shell), it would
    # read the password there instead of invoking SSH_ASKPASS — no GUI popup.
    return subprocess.run(
        ssh_cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=connect_timeout + 120,
        env=env,
    )


def _remote_df_path_candidates(path_on_remote: str) -> List[str]:
    """Paths to try with ``df`` on the remote (deepest first), ending at ``/``."""
    p = PurePosixPath(path_on_remote.strip() or "/")
    out: List[str] = []
    while True:
        s = str(p)
        if s == ".":
            s = "/"
        if not s.startswith("/"):
            s = "/" + s
        if s not in out:
            out.append(s)
        if p == PurePosixPath("/"):
            break
        p = p.parent
    return out


def remote_df_free_bytes(
    remote: RemoteTarget,
    path_on_remote: str,
    *,
    batch_mode: bool = True,
    extra_env: Optional[Dict[str, str]] = None,
    password_for_sshpass: Optional[str] = None,
    connect_timeout: int = 15,
) -> Optional[int]:
    """
    Free bytes on the filesystem that will contain ``path_on_remote`` on the remote host.

    Tries ``df -B1 -P`` on the path and each parent until one exists: rsync destinations
    often do not exist yet, so ``df`` on the full path alone would fail.
    """
    cands = _remote_df_path_candidates(path_on_remote)
    quoted = []
    for c in cands:
        q = c.replace("'", "'\"'\"'")
        quoted.append(f"'{q}'")
    inner = " ".join(quoted)
    # Skip header line (tail -n +2); first successful df wins.
    remote_cmd = (
        f"sh -c 'for d in {inner}; do "
        r'o=$(df -B1 -P "$d" 2>/dev/null | tail -n +2 | head -n1); '
        r'[ -n "$o" ] && echo "$o" && exit 0; '
        f"done; exit 1'"
    )
    try:
        proc = run_ssh_command(
            remote,
            remote_cmd,
            connect_timeout=connect_timeout,
            batch_mode=batch_mode,
            extra_env=extra_env,
            password_for_sshpass=password_for_sshpass,
        )
        text = (proc.stdout or "").strip()
        if not text:
            return None
        line = text.splitlines()[-1].strip()
        if not line or line.lower().startswith("filesystem"):
            return None
        parts = line.split()
        if len(parts) < 4:
            return None
        avail = int(parts[3])
        return avail
    except (ValueError, subprocess.TimeoutExpired, OSError, IndexError):
        return None


def scan_source_tree_stats(
    root: str,
    *,
    emit_every_files: int = 250,
    emit_every_sec: float = 0.2,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """
    One pass over the tree: file count and total bytes (``os.path.getsize`` per file).

    Suitable for interactive progress on slow LAN/NFS mounts: emits often (by file count
    and by time) so the UI does not sit idle while ``du`` would block.

    Totals are a sum of file sizes (sparse/special files may differ slightly from ``du``).

    If ``should_cancel`` returns true, returns partial ``(count, total_bytes)`` so the UI can
    keep totals collected so far.
    """
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        return None, None
    n = 0
    total_b = 0
    last_emit_n = -1
    last_emit_t = time.monotonic()
    try:
        for dirpath, _dirnames, filenames in os.walk(root_path, followlinks=False):
            if should_cancel and should_cancel():
                if on_progress:
                    on_progress(n, total_b)
                return n, total_b
            for fn in filenames:
                if should_cancel and should_cancel():
                    if on_progress:
                        on_progress(n, total_b)
                    return n, total_b
                fp = os.path.join(dirpath, fn)
                n += 1
                try:
                    total_b += os.path.getsize(fp)
                except OSError:
                    pass
                now = time.monotonic()
                if on_progress and (
                    n - last_emit_n >= emit_every_files
                    or now - last_emit_t >= emit_every_sec
                ):
                    on_progress(n, total_b)
                    last_emit_t = now
                    last_emit_n = n
        if on_progress:
            on_progress(n, total_b)
        return n, total_b
    except OSError:
        return None, None


def count_files_local(
    root: str,
    *,
    progress_every: int = 0,
    on_progress: Optional[Callable[[int], None]] = None,
) -> Optional[int]:
    """
    Count regular files under root (local). May take a long time on large trees.

    If ``progress_every`` > 0 and ``on_progress`` is set, ``on_progress(n)`` is called
    every ``progress_every`` files and once at the end with the final count.
    """
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        return None
    n = 0
    last_emit = 0
    try:
        for _dir, _names, files in os.walk(root_path, followlinks=False):
            n += len(files)
            if (
                on_progress
                and progress_every > 0
                and n >= last_emit + progress_every
            ):
                on_progress(n)
                last_emit = n
        if on_progress and n > last_emit:
            on_progress(n)
        return n
    except OSError:
        return None


def human_bytes(n: Optional[int]) -> str:
    """Format byte counts using decimal (SI) steps: B, KB, MB, GB, TB, PB."""
    if n is None:
        return "—"
    x = float(abs(n))
    neg = n < 0
    prefix = "-" if neg else ""
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if x < 1000.0 or unit == "PB":
            if unit == "B":
                return f"{prefix}{int(x)} B"
            return f"{prefix}{x:.2f} {unit}"
        x /= 1000.0
    return f"{prefix}{x:.2f} PB"


def format_rsync_hms_for_display(s: str) -> str:
    """
    Normalize rsync ``H:M:S`` time tokens for stable UI width.

    Minutes and seconds are always two digits. Hours are zero-padded to two digits when below
    100 so ``0:08:28`` becomes ``00:08:28``; at 100+ hours the hour field uses as many digits as
    needed (e.g. ``71:36:26``, ``102:05:03``).
    """
    t = s.strip()
    if not t or t == "—":
        return s
    parts = t.split(":")
    if len(parts) != 3:
        return s
    try:
        h, mi, se = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return s
    h = max(0, h)
    mi = max(0, min(mi, 59))
    se = max(0, min(se, 59))
    if h < 100:
        return f"{h:02d}:{mi:02d}:{se:02d}"
    return f"{h}:{mi:02d}:{se:02d}"


def format_seconds_as_hms_display(total_sec: float) -> str:
    """
    Format a non-negative duration in seconds using the same width rules as
    :func:`format_rsync_hms_for_display`.
    """
    if math.isnan(total_sec) or math.isinf(total_sec):
        return "—"
    sec = int(round(max(0.0, total_sec)))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h < 100:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{h}:{m:02d}:{s:02d}"


def parse_rsync_speed_to_bytes_per_sec(tok: str) -> Optional[float]:
    """
    Parse rsync progress speed tokens (e.g. ``12.50MiB/s``, ``165.68MB/s``, ``0.00kB/s``)
    into bytes per second. Uses 1024-based steps to match rsync’s human-readable sizes.
    """
    t = tok.strip()
    if not t:
        return None
    tl = t.lower()
    if len(tl) < 3 or tl[-2:] != "/s":
        return None
    core = t[:-2].strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([A-Za-z]*)$", core)
    if not m:
        return None
    val = float(m.group(1))
    raw_u = m.group(2).strip()
    if not raw_u:
        return val
    u = raw_u.lower()
    while len(u) > 1 and u.endswith("b"):
        u = u[:-1]
    mult = {
        "k": 1024,
        "ki": 1024,
        "m": 1024**2,
        "mi": 1024**2,
        "g": 1024**3,
        "gi": 1024**3,
        "t": 1024**4,
        "ti": 1024**4,
        "p": 1024**5,
        "pi": 1024**5,
    }
    if u not in mult:
        return None
    return val * mult[u]


def parse_extra_rsync_args(line: str) -> List[str]:
    """
    Split a user-entered argument line using POSIX shell rules (``shlex.split``).

    Raises ``ValueError`` if quotes are unbalanced, or if the line or token count is excessive.
    """
    s = line.strip()
    if not s:
        return []
    if len(s) > EXTRA_RSYNC_ARG_LINE_MAX_CHARS:
        raise ValueError(
            f"Extra rsync arguments exceed {EXTRA_RSYNC_ARG_LINE_MAX_CHARS} characters."
        )
    try:
        parts = shlex.split(s, posix=True)
    except ValueError as e:
        raise ValueError("Unbalanced or invalid quotes in extra rsync arguments.") from e
    if len(parts) > EXTRA_RSYNC_ARG_COUNT_MAX:
        raise ValueError(
            f"Extra rsync arguments exceed {EXTRA_RSYNC_ARG_COUNT_MAX} tokens."
        )
    return parts


# UI: policy when the destination already has a path (one rsync modifier set, or none).
EXISTING_FILES_MODE_DEFAULT: str = "skip_name_size"

_EXISTING_FILES_MODE_ARGV: Dict[str, List[str]] = {
    "overwrite": [],
    # Same path on dest with matching size → skip (rsync does not compare mtime for this check).
    "skip_name_size": ["--size-only"],
    # Same path exists on dest → skip regardless of size.
    "skip_name": ["--ignore-existing"],
}

# Older settings keys map onto the simplified modes.
_LEGACY_EXISTING_FILES_MODE_MAP: Dict[str, str] = {
    "default": "skip_name_size",
    "update": "overwrite",
    "inplace": "overwrite",
    "backup": "overwrite",
    "existing_only": "overwrite",
    "ignore_existing": "skip_name",
}

# (label shown in combo box, mode key for settings / :func:`existing_files_mode_rsync_argv`).
EXISTING_FILES_MODE_CHOICES: List[Tuple[str, str]] = [
    ("Skip (if name and size is same)", "skip_name_size"),
    ("Skip (if only name is same)", "skip_name"),
    ("Overwrite", "overwrite"),
]


def normalize_existing_files_mode(mode: Optional[str]) -> str:
    """
    Return a known mode key; unknown or empty values become ``EXISTING_FILES_MODE_DEFAULT``.

    Legacy persisted keys (e.g. ``ignore_existing``, ``default``) are mapped to the new set.
    """
    if not isinstance(mode, str):
        return EXISTING_FILES_MODE_DEFAULT
    key = mode.strip()
    if not key:
        return EXISTING_FILES_MODE_DEFAULT
    if key in _EXISTING_FILES_MODE_ARGV:
        return key
    if key in _LEGACY_EXISTING_FILES_MODE_MAP:
        return _LEGACY_EXISTING_FILES_MODE_MAP[key]
    return EXISTING_FILES_MODE_DEFAULT


def existing_files_mode_rsync_argv(mode: Optional[str]) -> List[str]:
    """Argv tokens for :class:`~safecopi.main_window.MainWindow` “existing files” policy."""
    return list(_EXISTING_FILES_MODE_ARGV[normalize_existing_files_mode(mode)])


def _parse_semver_triplet(version: str) -> Tuple[int, int, int]:
    """
    Best-effort parse of ``MAJOR.MINOR.PATCH`` (optionally prefixed with ``v`` and
    optionally suffixed with pre-release / build metadata).
    """
    core = version.strip()
    if core.startswith(("v", "V")):
        core = core[1:]
    for sep in ("+", "-"):
        if sep in core:
            core = core.split(sep, 1)[0]
            break
    parts = core.split(".")
    nums: List[int] = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def is_remote_version_newer(current: str, remote: str) -> bool:
    """Return True if ``remote`` is a newer SemVer triplet than ``current``."""
    return _parse_semver_triplet(remote) > _parse_semver_triplet(current)


def fetch_latest_github_version(owner: str = "UnDadFeated", repo: str = "SafeCopi") -> Optional[str]:
    """
    Return the latest tagged version from GitHub releases, or None on error.

    Uses the public ``/releases/latest`` endpoint and reads ``tag_name``. Network and
    JSON errors are swallowed; callers should treat ``None`` as "could not check".
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    req = Request(url, headers={"User-Agent": "SafeCopi update check"})
    try:
        with urlopen(req, timeout=5) as resp:  # type: ignore[call-arg]
            data = resp.read()
    except (URLError, HTTPError, OSError, ValueError):
        return None
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except (TypeError, ValueError):
        return None
    tag = payload.get("tag_name") or payload.get("name")
    if not isinstance(tag, str) or not tag.strip():
        return None
    return tag.strip()


def build_rsync_command_argv(
    source: str,
    dest: str,
    timeout_sec: int,
    extra_args: List[str],
    *,
    recursive: bool = True,
) -> List[str]:
    """
    Build the rsync argv (program name first) matching :class:`RsyncWorker`.

    With ``recursive`` (default), uses ``-ah`` (archive + human-readable), which
    descends into subdirectories. When false, uses ``-hlptgoD`` — same archive
    preserves without ``-r`` — so only the top level of the source tree is
    traversed.

    ``extra_args`` are inserted before ``-v`` and the source/destination paths.

    Per-file path lines are emitted on stderr (with ``-v``) so the UI can show the
    active filename next to ``Attempt``; add ``--info=name0`` via **Extra rsync
    arguments** if that volume is undesirable.
    """
    try:
        t_raw = int(timeout_sec)
    except (TypeError, ValueError):
        t_raw = 60
    t = max(1, min(t_raw, RSYNC_TIMEOUT_SEC_MAX))
    if recursive:
        mode = ["-ah", "--no-inc-recursive"]
    else:
        mode = ["-hlptgoD"]
    out: List[str] = [
        "rsync",
        *mode,
        "--info=progress2",
        "--mkpath",
        f"--timeout={t}",
    ]
    out.extend(extra_args)
    out.extend(["-v", source, dest])
    return out


# rsync --info=progress2, e.g. "  0:01:02   3%  1.23MiB/s   0:45:00 (xfr#56, to-chk=1000/2000)"
# Speed token varies: kB/s, KiB/s, MiB/s, GB/s, etc.
_RSYNC_PROGRESS2_LINE = re.compile(
    r"^\s*(\d+:\d+:\d+)\s+(\d+)%\s+(\S+)\s+(\d+:\d+:\d+)"
)

# When rsync prints the current file on one line and stats on the next, the stats line is
# often "SIZE  PCT  SPEED  ETA" (no leading elapsed), e.g.
# "        206.50K   0%  165.68MB/s    0:00:00 (xfr#1, to-chk=295659/295662)"
_RSYNC_PROGRESS_SIZE_FIRST = re.compile(
    r"^\s*(\S+)\s+(\d+)%\s+(\S+/s)\s+(\d+:\d+:\d+)(?:\s+(\([^)]*\)))?\s*$"
)

# First column on size-first lines: "32.77K", "9.97M", "1.00G" (rsync uses K/M/G/T as 1024).
_RSYNC_TRANS_AMOUNT = re.compile(r"^(\d+(?:\.\d+)?)([kKmMgGtTpP])?$")


@dataclass(frozen=True)
class RsyncProgressSnapshot:
    """One line of overall progress from ``rsync --info=progress2``."""

    percent: int
    elapsed: str
    speed: str
    eta: str
    stats_raw: str
    stats_human: str
    # From size-first progress lines: cumulative bytes transferred (parsed) and raw token for UI.
    transferred_bytes: Optional[int] = None
    transferred_display: Optional[str] = None
    # Last stderr path line before this progress update (verbatim, for the transfer detail line).
    current_path: Optional[str] = None


def _format_rsync_count_ratio(chunk: str) -> str:
    chunk = chunk.strip()
    if "/" not in chunk:
        return chunk
    left, right = chunk.split("/", 1)
    try:
        return f"{int(left):,} / {int(right):,}"
    except ValueError:
        return chunk


def parse_rsync_xfr_count(stats_raw: str) -> Optional[int]:
    """Return the ``xfr#N`` counter from rsync progress parenthetical stats, if present."""
    if not stats_raw or not stats_raw.strip():
        return None
    m = re.search(r"(?i)xfr#(\d+)", stats_raw)
    return int(m.group(1)) if m else None


def humanize_rsync_progress_stats(paren_inner: str) -> str:
    """Turn parenthetical rsync progress fragments into short, readable phrases."""
    if not paren_inner or not paren_inner.strip():
        return ""
    parts: List[str] = []
    for piece in paren_inner.split(","):
        piece = piece.strip()
        if not piece:
            continue
        low = piece.lower()
        if low.startswith("xfr#"):
            tail = piece[4:].strip()
            parts.append(f"Transfer progress #{tail}")
        elif low.startswith("to-chk="):
            rest = piece.split("=", 1)[-1].strip()
            parts.append(f"Verify queue {_format_rsync_count_ratio(rest)}")
        elif low.startswith("ir-chk="):
            rest = piece.split("=", 1)[-1].strip()
            parts.append(f"Directory scan {_format_rsync_count_ratio(rest)}")
        else:
            parts.append(piece)
    return " · ".join(parts)


def parse_rsync_progress2_line(line: str) -> Optional[RsyncProgressSnapshot]:
    m = _RSYNC_PROGRESS2_LINE.match(line)
    if not m:
        return None
    elapsed, pct_s, speed, eta = m.group(1), m.group(2), m.group(3), m.group(4)
    pct = max(0, min(100, int(pct_s)))
    raw = ""
    if "(" in line:
        li = line.rfind("(")
        ri = line.rfind(")")
        if ri > li >= 0:
            raw = line[li + 1 : ri].strip()
    human = humanize_rsync_progress_stats(raw)
    return RsyncProgressSnapshot(pct, elapsed, speed, eta, raw, human)


def parse_rsync_transferred_amount_token(tok: str) -> Optional[int]:
    """
    Parse the leading size token from rsync size-first progress lines into bytes.

    Suffix letters follow rsync's human-readable convention (K/M/G/T/P as multiples of 1024).
    """
    t = tok.strip()
    if not t:
        return None
    m = _RSYNC_TRANS_AMOUNT.match(t)
    if not m:
        return None
    val = float(m.group(1))
    suf = (m.group(2) or "").upper()
    mult = {
        "": 1,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
        "P": 1024**5,
    }
    if suf not in mult:
        return None
    return int(val * mult[suf])


def parse_rsync_transfer_progress_line(line: str) -> Optional[RsyncProgressSnapshot]:
    """
    Parse one stderr line from an rsync transfer: ``--info=progress2`` form first,
    then the common ``SIZE  PCT  SPEED  ETA`` continuation line.
    """
    p2 = parse_rsync_progress2_line(line)
    if p2 is not None:
        return p2
    m = _RSYNC_PROGRESS_SIZE_FIRST.match(line)
    if not m:
        return None
    size_tok, pct_s, speed, eta = m.group(1), m.group(2), m.group(3), m.group(4)
    pct = max(0, min(100, int(pct_s)))
    raw = ""
    g5 = m.group(5)
    if g5:
        inner = g5.strip()
        if inner.startswith("(") and inner.endswith(")"):
            raw = inner[1:-1].strip()
        else:
            raw = inner
    human = humanize_rsync_progress_stats(raw)
    tb = parse_rsync_transferred_amount_token(size_tok)
    return RsyncProgressSnapshot(
        pct,
        "—",
        speed,
        eta,
        raw,
        human,
        transferred_bytes=tb,
        transferred_display=size_tok,
    )


def _rsync_stderr_line_is_probable_file_path(line: str) -> bool:
    """
    True when ``line`` is almost certainly a lone transferred path from rsync ``-v``,
    not a status/error sentence.

    Substring needles like ``deleting`` or ``error`` must **not** be applied to lines
    that contain ``/``, or legitimate directories such as ``backup/deleting/foo.jpg``
    are misclassified and skipped for the transfer detail line while the activity log
    shows noise.
    """
    s = line.strip()
    if not s or len(s) >= 4096:
        return False
    low = s.lower()
    if low.startswith("rsync:"):
        return False
    rest_star = s.lstrip("*").lower()
    if s.startswith("*") and rest_star.startswith("deleting"):
        return False

    if re.match(r"^[>][^\s]+\s+", s):
        return True

    if "/" in s:
        if "%" in s:
            return False
        if low.startswith(
            (
                "building file list",
                "receiving incremental file list",
                "receiving file list",
                "sending incremental file list",
                "file list done",
            )
        ) or low.startswith("file list "):
            return False
        if (
            low.startswith("created ")
            or low.startswith("sent ")
            or low.startswith("total ")
            or low.startswith("speedup is")
        ):
            return False
        if low.startswith("cannot ") or low.startswith("warning:") or low.startswith("warning "):
            return False
        if low.startswith("error:") or low.startswith("error "):
            return False
        if low.startswith("skipping ") or low.startswith("ignoring "):
            return False
        if low.startswith("failed ") or low.startswith("timeout ") or low.startswith("connection "):
            return False
        if low.startswith("permission denied") or low.startswith("protocol "):
            return False
        return True

    if " " not in s and len(s) <= 512:
        return True
    return False


def is_rsync_filename_only_stderr_line(line: str) -> bool:
    """
    True for a lone path/name line from rsync ``-v`` stderr (not progress, not status).

    Used to track the file being transferred without logging every path to the activity log.
    """
    s = line.strip()
    if not s or len(s) >= 4096:
        return False
    if parse_rsync_transfer_progress_line(line) is not None:
        return False
    low = s.lower()
    if s == "done" or s.startswith("done "):
        return False
    if "%" in s and "/s" in s:
        return False
    if "xfr#" in low or "to-chk" in low or "ir-chk" in low:
        return False
    if " " in s and "/" not in s:
        # Itemized changes: ">f.st.... path" (path may lack '/').
        if not re.match(r"^[>][^\s]+\s+", s):
            return False
    return _rsync_stderr_line_is_probable_file_path(line)


def should_log_rsync_stderr_line(line: str) -> bool:
    """
    Return False for lines that would flood the UI log (paths, transfer stat noise).

    Call only for lines that did not parse as transfer progress; progress-like lines
    that failed to parse are still suppressed when they contain ``%`` and a ``/s`` speed.
    """
    s = line.strip()
    if not s:
        return False
    if "%" in s and "/s" in s:
        return False
    if _rsync_stderr_line_is_probable_file_path(line):
        return False
    low = s.lower()
    needles = (
        "building file list",
        "receiving incremental file list",
        "receiving file list",
        "file list done",
        "rsync:",
        "sending incremental file list",
        "sent ",
        "total ",
        "speedup is",
        "created ",
        "deleting",
        "skipping",
        "ignoring",
        "warning",
        "error",
        "cannot",
        "failed",
        "timeout",
        "connection",
        "permission",
        "denied",
        "protocol",
        "auth",
    )
    if any(n in low for n in needles):
        return True
    if s == "done" or s.startswith("done "):
        return True
    return True
