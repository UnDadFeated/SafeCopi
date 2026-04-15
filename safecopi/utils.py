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
from urllib.error import URLError, HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

_ASKPASS_WRAPPER: Optional[Path] = None
_RSYNC_CRTIMES_SUPPORTED: Optional[bool] = None

# Upper bounds for user-supplied rsync knobs (match UI where applicable; clamp API callers too).
RSYNC_TIMEOUT_SEC_MAX: int = 86400
RSYNC_RETRY_WAIT_SEC_MAX: int = 3600
# Receiver-only: relative to the destination root. Keeps rsync temp files out of deep trees so
# mkstemp is less likely to hit ENOENT on laggy or strict NAS/CIFS paths.
RSYNC_RECEIVER_TEMP_SUBDIR: str = ".safecopi-rsync-tmp"
EXTRA_RSYNC_ARG_LINE_MAX_CHARS: int = 32_768
EXTRA_RSYNC_ARG_COUNT_MAX: int = 512

# Ceiling beyond SSH ``ConnectTimeout`` for ``subprocess.run`` / UI watchdog timers.
SSH_SUBPROCESS_MAX_RUNTIME_OVERHEAD_SEC: int = 120
# Remote ``df`` space check: one subprocess; keep total wait below typical UI expectations.
SSH_DF_SUBPROCESS_OVERHEAD_SEC: int = 60
# Standalone **Test SSH** uses this ``ConnectTimeout`` (matches UI watchdog duration).
SSH_TEST_CONNECT_TIMEOUT_SEC: int = 12

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

    def to_rsync_uri(self) -> str:
        """Rsync/OpenSSH form ``[user@]host:/path`` (path is absolute on the remote)."""
        p = self.path if self.path.startswith("/") else f"/{self.path}"
        if self.user:
            return f"{self.user}@{self.host}:{p}"
        return f"{self.host}:{p}"


# Dolphin/KDE and similar paste ``sftp://…`` / ``ssh://…`` / ``fish://…`` URLs here.
_URL_REMOTE_SCHEMES = frozenset({"sftp", "ssh", "fish"})


def parse_rsync_destination(dest: str) -> Tuple[Optional[RemoteTarget], str]:
    """
    Return (RemoteTarget, local_path) for a destination string.
    If not remote, RemoteTarget is None and local_path is dest stripped.

    Accepts rsync/scp style ``user@host:/path`` and URL forms such as
    ``sftp://user@host/path`` (Dolphin). Non-default SSH ports in the URL are not
    carried into :class:`RemoteTarget` (OpenSSH uses port 22 unless overridden in ssh config).
    """
    s = dest.strip()
    if not s:
        return None, s

    if "://" in s[:24]:
        parsed = urlparse(s)
        scheme = parsed.scheme.lower() if parsed.scheme else ""
        if scheme in _URL_REMOTE_SCHEMES and parsed.hostname:
            path = unquote(parsed.path or "/")
            if not path.startswith("/"):
                path = "/" + path if path else "/"
            user = parsed.username
            host = parsed.hostname
            return RemoteTarget(host=host, path=path, user=user), path

    m = _RSYNC_REMOTE.match(s)
    if not m:
        return None, s
    user = m.group("user")
    host = m.group("host")
    path = m.group("path")
    return RemoteTarget(host=host, path=path, user=user), path


def canonical_rsync_path(path: str) -> str:
    """
    Normalize remote paths for rsync/ssh: URL-style remotes become ``user@host:/path``.
    Local paths and already-canonical remotes are returned trimmed unchanged.
    """
    s = path.strip()
    remote, _ = parse_rsync_destination(s)
    if remote is None:
        return s
    return remote.to_rsync_uri()


def remote_rsync_uri_strip_trailing_slashes(uri: str) -> str:
    """
    For ``user@host:/path`` (or URL forms normalized via :func:`canonical_rsync_path`),
    remove trailing slashes from the remote path component.

    With multiple sources, this matches “copy each directory as a named folder under the
    destination” (no trailing slash on the source). A trailing slash would mean “copy
    only the contents” into the same destination tree, merging everything together.
    """
    s = uri.strip()
    remote, rpath = parse_rsync_destination(s)
    if remote is None:
        return s.rstrip("/") or s
    t = rpath.rstrip("/")
    if not t:
        t = "/"
    fixed = RemoteTarget(host=remote.host, path=t, user=remote.user)
    return fixed.to_rsync_uri()


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


def _local_free_bytes_impl(path: str) -> Optional[int]:
    """Free space on the filesystem containing ``path`` (local); may block on bad mounts."""
    p = Path(path).expanduser()
    try:
        def _disk_free(target: Path) -> int:
            # Avoid Path.resolve() for local free-space checks: resolving symlinks/parents can
            # fail on permission-restricted mount points while disk_usage on the path still works.
            return shutil.disk_usage(os.path.abspath(str(target))).free

        if p.exists():
            return _disk_free(p)
        cur = p.parent
        while cur != cur.parent:
            if cur.exists():
                return _disk_free(cur)
            cur = cur.parent
        return shutil.disk_usage("/").free
    except OSError:
        return None


def local_free_bytes(path: str, *, timeout_sec: Optional[float] = None) -> Optional[int]:
    """
    Free space on the filesystem containing ``path`` (local).

    With ``timeout_sec``, the stat walk runs in a worker thread so a stuck NFS/FUSE path
    cannot block indefinitely (returns ``None`` on timeout).
    """
    if timeout_sec is None:
        return _local_free_bytes_impl(path)
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

    t = float(timeout_sec)
    if t <= 0:
        return None
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(_local_free_bytes_impl, path)
        try:
            return fut.result(timeout=t)
        except FuturesTimeoutError:
            # Do not block waiting for a potentially stuck filesystem probe thread.
            fut.cancel()
            return None
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


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


def build_ssh_command_argv(
    remote: RemoteTarget,
    remote_cmd: str,
    *,
    connect_timeout: int = 15,
    batch_mode: bool = True,
    password_for_sshpass: Optional[str] = None,
) -> List[str]:
    """
    Build ``ssh`` argv (or ``sshpass -e ssh …`` when a password is supplied).
    Raises ``FileNotFoundError`` if ``password_for_sshpass`` is set but ``sshpass`` is missing.
    """
    core = [
        "ssh",
        *ssh_extra_argv(connect_timeout, batch_mode, for_rsync=False),
        remote.ssh_spec(),
        remote_cmd,
    ]
    if password_for_sshpass:
        ss = shutil.which("sshpass")
        if not ss:
            raise FileNotFoundError(
                "sshpass is not installed; install it (e.g. pacman -S sshpass) or leave the password field empty to use the GUI askpass."
            )
        return [ss, "-e", *core]
    return core


def ssh_command_environment(
    extra_env: Optional[Dict[str, str]],
    password_for_sshpass: Optional[str],
) -> Dict[str, str]:
    """Environment for standalone ``ssh`` / ``sshpass`` (matches :func:`run_ssh_command`)."""
    env = os.environ.copy()
    if password_for_sshpass:
        env["SSHPASS"] = password_for_sshpass
        if extra_env:
            for k, v in extra_env.items():
                if k not in ("SSH_ASKPASS", "SSH_ASKPASS_REQUIRE"):
                    env[k] = v
    else:
        if extra_env:
            env.update(extra_env)
    return env


def run_ssh_command(
    remote: RemoteTarget,
    remote_cmd: str,
    *,
    connect_timeout: int = 15,
    batch_mode: bool = True,
    extra_env: Optional[Dict[str, str]] = None,
    password_for_sshpass: Optional[str] = None,
    max_runtime_overhead_sec: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run a shell command on the remote host via SSH."""
    ssh_cmd = build_ssh_command_argv(
        remote,
        remote_cmd,
        connect_timeout=connect_timeout,
        batch_mode=batch_mode,
        password_for_sshpass=password_for_sshpass,
    )
    env = ssh_command_environment(extra_env, password_for_sshpass)
    overhead = (
        SSH_SUBPROCESS_MAX_RUNTIME_OVERHEAD_SEC
        if max_runtime_overhead_sec is None
        else max(1, int(max_runtime_overhead_sec))
    )
    run_kw: Dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "text": True,
        "timeout": connect_timeout + overhead,
        "env": env,
    }
    # New session so ``timeout`` can drop the whole tree (ssh/sshpass) on POSIX.
    if os.name != "nt":
        run_kw["start_new_session"] = True
    # No TTY on stdin: if ssh inherits a terminal (e.g. app started from a shell), it would
    # read the password there instead of invoking SSH_ASKPASS — no GUI popup.
    return subprocess.run(ssh_cmd, **run_kw)


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


def build_remote_df_shell_command(path_on_remote: str) -> str:
    """
    Remote shell command: try ``df -B1 -P`` on ``path_on_remote`` and each parent until one exists.
    """
    cands = _remote_df_path_candidates(path_on_remote)
    quoted = []
    for c in cands:
        q = c.replace("'", "'\"'\"'")
        quoted.append(f"'{q}'")
    inner = " ".join(quoted)
    return (
        f"sh -c 'for d in {inner}; do "
        r'o=$(df -B1 -P "$d" 2>/dev/null | tail -n +2 | head -n1); '
        r'[ -n "$o" ] && echo "$o" && exit 0; '
        f"done; exit 1'"
    )


def parse_remote_df_stdout(stdout: str) -> Optional[int]:
    """Parse ``df -B1 -P`` data line from captured remote stdout (last non-empty line)."""
    text = (stdout or "").strip()
    if not text:
        return None
    line = text.splitlines()[-1].strip()
    if not line or line.lower().startswith("filesystem"):
        return None
    parts = line.split()
    if len(parts) < 4:
        return None
    try:
        return int(parts[3])
    except ValueError:
        return None


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
    remote_cmd = build_remote_df_shell_command(path_on_remote)
    try:
        proc = run_ssh_command(
            remote,
            remote_cmd,
            connect_timeout=connect_timeout,
            batch_mode=batch_mode,
            extra_env=extra_env,
            password_for_sshpass=password_for_sshpass,
            max_runtime_overhead_sec=SSH_DF_SUBPROCESS_OVERHEAD_SEC,
        )
        return parse_remote_df_stdout(proc.stdout or "")
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


def parse_rsync_eta_token_to_seconds(eta: str) -> Optional[float]:
    """
    Parse rsync ETA fields such as ``0:03:30``, ``34:08:53``, or ``102:05:03`` into seconds.

    Returns ``None`` for empty, em dash, malformed tokens, or values outside a safe bound
    (used only for UI estimates, not scheduling).
    """
    t = (eta or "").strip()
    if not t or t == "—":
        return None
    parts = t.split(":")
    try:
        if len(parts) == 3:
            h = float(parts[0])
            m = float(parts[1])
            sec = float(parts[2])
        elif len(parts) == 2:
            h = 0.0
            m = float(parts[0])
            sec = float(parts[1])
        else:
            return None
    except ValueError:
        return None
    if h < 0 or m < 0 or m >= 60 or sec < 0 or sec >= 60:
        return None
    out = h * 3600.0 + m * 60.0 + sec
    # Match plausible rsync ETAs; reject garbage that would imply absurd byte estimates.
    if out < 0.25 or out > 5.0 * 365 * 86400:
        return None
    return out


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
    ("Skip (if filename and size is same)", "skip_name_size"),
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


def fetch_latest_github_version(
    owner: str = "UnDadFeated", repo: str = "SafeCopi",
) -> Tuple[Optional[str], str]:
    """
    Return ``(tag_name, error_detail)``.

    On success, ``error_detail`` is ``""``. On failure, ``tag_name`` is ``None`` and
    ``error_detail`` is a short reason (for diagnostics / logging).
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    req = Request(url, headers={"User-Agent": "SafeCopi update check"})
    try:
        with urlopen(req, timeout=5) as resp:  # type: ignore[call-arg]
            data = resp.read()
    except HTTPError as e:
        return None, f"http_{e.code}:{e.reason!s}"[:800]
    except URLError as e:
        return None, f"url:{e.reason!s}"[:800]
    except (OSError, ValueError) as e:
        return None, f"io:{e!s}"[:800]
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except (TypeError, ValueError) as e:
        return None, f"json:{e!s}"[:400]
    tag = payload.get("tag_name") or payload.get("name")
    if not isinstance(tag, str) or not tag.strip():
        return None, "missing_tag_name"
    return tag.strip(), ""


def ensure_local_rsync_receiver_temp_dir(dest: str) -> None:
    """
    Create :data:`RSYNC_RECEIVER_TEMP_SUBDIR` under a **local** destination if needed.

    Rsync also creates this directory on the receiver for remote destinations; calling this
    ahead of time avoids a race on some mounts where the first deep ``mkstemp`` runs before
    intermediate directories are visible.
    """
    s = dest.strip()
    if not s:
        return
    remote, _ = parse_rsync_destination(s)
    if remote is not None:
        return
    base = Path(s.rstrip("/")).expanduser()
    try:
        base = base.resolve(strict=False)
    except OSError:
        return
    tmp = base / RSYNC_RECEIVER_TEMP_SUBDIR
    try:
        tmp.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def rsync_supports_crtimes() -> bool:
    """Return True if the local rsync binary was compiled with ``--crtimes`` (``-N``) support.

    The result is cached after the first probe.  Returns False when rsync is missing or its
    capabilities line contains ``no crtimes``.
    """
    global _RSYNC_CRTIMES_SUPPORTED
    if _RSYNC_CRTIMES_SUPPORTED is not None:
        return _RSYNC_CRTIMES_SUPPORTED
    try:
        proc = subprocess.run(
            ["rsync", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        # rsync prints "no crtimes" when compiled without support; bare "crtimes" when supported.
        out = proc.stdout or ""
        if "no crtimes" in out:
            _RSYNC_CRTIMES_SUPPORTED = False
        elif "crtimes" in out:
            _RSYNC_CRTIMES_SUPPORTED = True
        else:
            _RSYNC_CRTIMES_SUPPORTED = False
    except (OSError, subprocess.TimeoutExpired):
        _RSYNC_CRTIMES_SUPPORTED = False
    return _RSYNC_CRTIMES_SUPPORTED


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

    **Timestamp preservation**: ``-a`` includes ``-t`` (modification times). When the
    local rsync supports ``--crtimes`` (``-N``), creation / birth times are preserved too.

    ``extra_args`` are inserted before ``-v`` and the source/destination paths.

    For a **local** destination, uses a relative ``--temp-dir`` under the destination root
    (:data:`RSYNC_RECEIVER_TEMP_SUBDIR`) plus a ``protect`` filter so ``--delete`` does not remove
    that folder; shallow temps avoid ``mkstemp`` failures (ENOENT) in deep paths on some NAS/CIFS
    mounts. For a **remote** destination, uses ``--temp-dir=/tmp`` on the receiver (must exist;
    rsync 3.4+ errors if the temp directory is missing).

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
    ]
    # Preserve creation times (birth times) when the local rsync supports --crtimes.
    # Modification times are already preserved via -a (-t); -N is not part of archive mode.
    if rsync_supports_crtimes():
        out.append("--crtimes")
    # Shallow temp dir: avoids receiver mkstemp in deep paths (ENOENT on some NAS/CIFS).
    # Local: under dest (same FS as backup); remote receiver: /tmp always exists (rsync 3.4+).
    ds = dest.strip()
    if ds:
        rem, _ = parse_rsync_destination(ds)
        if rem is not None:
            out.append("--temp-dir=/tmp")
        else:
            out.append(f"--temp-dir={RSYNC_RECEIVER_TEMP_SUBDIR}")
            out.append(f"--filter=protect {RSYNC_RECEIVER_TEMP_SUBDIR}/")
    out.append(f"--timeout={t}")
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


def parse_rsync_queue_remaining_total(stats_raw: str) -> Optional[Tuple[int, int]]:
    """
    Parse ``to-chk=left/total`` (or ``ir-chk=`` while rsync is still scanning) from progress stats.

    Returns ``(remaining, total)`` or ``None`` if no recognized fragment exists.
    ``to-chk`` is preferred when both appear.
    """
    if not stats_raw or not stats_raw.strip():
        return None
    for key in ("to-chk", "ir-chk"):
        m = re.search(rf"(?i){key}=([0-9]+)/([0-9]+)", stats_raw)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def estimate_rsync_total_bytes_from_progress(
    transferred_bytes: Optional[int], percent: int
) -> Optional[int]:
    """
    Infer total transfer size from rsync's cumulative byte counter and overall percent.

    Only meaningful when ``percent`` is in ``1..99`` and ``transferred_bytes`` is set; returns
    ``None`` on ambiguous input (e.g. ``percent == 0``).
    """
    if transferred_bytes is None or transferred_bytes < 0:
        return None
    p = max(0, min(100, int(percent)))
    if p <= 0:
        return None
    if p >= 100:
        return transferred_bytes
    # Round to nearest byte; avoid float drift on huge values.
    return (transferred_bytes * 100 + p // 2) // p


def clamp_monotonic_data_left_bytes(
    left_b: Optional[int],
    transferred_bytes: Optional[int],
    prev_tb: Optional[int],
    prev_left: Optional[int],
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """
    While cumulative transferred bytes only move forward, cap "data left" so it does not jump
    upward from rsync refining totals or ETA noise (remaining should drop by about the bytes
    sent since the last tick).

    Returns ``(adjusted_left, new_prev_tb, new_prev_left)``. Clears state when ``left_b`` is
    ``None``. Ignores the ceiling when ``transferred_bytes`` drops (new attempt / counter reset).
    """
    if left_b is None:
        return None, None, None
    tb = transferred_bytes
    if tb is None or tb < 0:
        return left_b, None, None
    out = left_b
    if prev_tb is not None and prev_left is not None and tb >= prev_tb:
        ceiling = prev_left - (tb - prev_tb)
        if ceiling >= 0:
            out = min(out, ceiling)
    return out, tb, out


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
    Return True only for stderr lines that should appear in the activity log: rsync
    diagnostics (``rsync:``) and other probable **errors, warnings, or issues**.

    Routine transfer chatter (file lists, ``sent``/``total``/``speedup``, ``done``, etc.)
    and per-file paths are suppressed — they are not passed here if identified as paths,
    and benign status lines are rejected below.
    """
    s = line.strip()
    if not s:
        return False
    if "%" in s and "/s" in s:
        return False
    if _rsync_stderr_line_is_probable_file_path(line):
        return False
    low = s.lower()
    # Benign rsync status (not ``rsync:``-prefixed diagnostics).
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
    if low.startswith("created ") and "directory" in low:
        return False
    if low.startswith("sent ") or low.startswith("total ") or low.startswith("speedup is"):
        return False
    if s == "done" or low.startswith("done "):
        return False

    if low.startswith("rsync:"):
        return True

    problem_markers = (
        " error",
        "error:",
        " error:",
        "warning:",
        " warning",
        "fatal",
        "cannot ",
        "can't ",
        "could not ",
        "failed",
        "failure",
        "timeout",
        "timed out",
        "connection reset",
        "connection refused",
        "broken pipe",
        "closed by remote",
        "unexpected end",
        "permission denied",
        "protocol version",
        " host is down",
        " no route to host",
        "network is unreachable",
        "no space left",
        " out of disk space",
        "i/o error",
        "read errors",
        "write error",
        "vanished",
    )
    if any(m in low for m in problem_markers):
        return True
    return False
