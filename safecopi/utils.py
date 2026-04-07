"""Helpers for paths, remote destinations, and disk space."""

from __future__ import annotations

import os
import re
import shlex
import time
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Tuple

_ASKPASS_WRAPPER: Optional[Path] = None

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
    if n is None:
        return "—"
    x = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(x) < 1024.0 or unit == "PiB":
            if unit == "B":
                return f"{int(x)} B"
            return f"{x:.2f} {unit}"
        x /= 1024.0
    return f"{x:.2f} PiB"


def parse_extra_rsync_args(line: str) -> List[str]:
    """
    Split a user-entered argument line using POSIX shell rules (``shlex.split``).

    Raises ``ValueError`` if quotes are unbalanced.
    """
    s = line.strip()
    if not s:
        return []
    try:
        return shlex.split(s, posix=True)
    except ValueError as e:
        raise ValueError("Unbalanced or invalid quotes in extra rsync arguments.") from e


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
    ``--info=name0`` suppresses per-file name lines on stderr while keeping
    ``progress2`` and high-level messages.
    """
    t = max(1, int(timeout_sec))
    if recursive:
        mode = ["-ah", "--no-inc-recursive"]
    else:
        mode = ["-hlptgoD"]
    out: List[str] = [
        "rsync",
        *mode,
        "--info=progress2",
        # Suppress per-file name lines on stderr (still get progress2 + errors).
        "--info=name0",
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
    r"^\s*\S+\s+(\d+)%\s+(\S+/s)\s+(\d+:\d+:\d+)(?:\s+(\([^)]*\)))?\s*$"
)


@dataclass(frozen=True)
class RsyncProgressSnapshot:
    """One line of overall progress from ``rsync --info=progress2``."""

    percent: int
    elapsed: str
    speed: str
    eta: str
    stats_raw: str
    stats_human: str


def _format_rsync_count_ratio(chunk: str) -> str:
    chunk = chunk.strip()
    if "/" not in chunk:
        return chunk
    left, right = chunk.split("/", 1)
    try:
        return f"{int(left):,} / {int(right):,}"
    except ValueError:
        return chunk


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
    pct_s, speed, eta = m.group(1), m.group(2), m.group(3)
    pct = max(0, min(100, int(pct_s)))
    raw = ""
    g4 = m.group(4)
    if g4:
        inner = g4.strip()
        if inner.startswith("(") and inner.endswith(")"):
            raw = inner[1:-1].strip()
        else:
            raw = inner
    human = humanize_rsync_progress_stats(raw)
    return RsyncProgressSnapshot(pct, "—", speed, eta, raw, human)


def should_log_rsync_stderr_line(line: str) -> bool:
    """
    Return False for lines that would flood the UI log (paths, transfer stat noise).

    Call only for lines that did not parse as transfer progress; progress-like lines
    that failed to parse are still suppressed when they contain ``%`` and a ``/s`` speed.
    """
    s = line.strip()
    if not s:
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
    if "%" in s and "/s" in s:
        return False
    if "/" in s and "%" not in s and len(s) < 4096:
        return False
    return True
