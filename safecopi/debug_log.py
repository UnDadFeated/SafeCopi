"""Append-only diagnostics to ``debug.log`` (app/session issues, not rsync transfer stream)."""

from __future__ import annotations

import atexit
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

_lock = threading.RLock()
_log_path: Optional[Path] = None
_session_id: Optional[str] = None
_shutdown_hooks_registered = False

# Retain only the most recent N app sessions (each begins with ``[SESSION] start``).
_DEBUG_LOG_MAX_SESSIONS: int = 3


def _resolve_log_dir() -> Path:
    try:
        from PySide6.QtCore import QCoreApplication, QStandardPaths

        if QCoreApplication.instance() is not None:
            loc = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)
            if loc:
                return Path(loc) / "SafeCopi"
    except Exception:
        pass
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "SafeCopi"


def _now_ts() -> str:
    """Local wall time with millisecond resolution (for correlation)."""
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _format_debug_line(
    component: str, event: str, data: Optional[Dict[str, Any]] = None
) -> str:
    line = f"{_now_ts()} [{component}] {event}"
    if data:
        line += " " + json.dumps(data, ensure_ascii=False, default=str)
    return line + "\n"


def _trim_log_to_last_sessions(raw: str, keep: int) -> tuple[str, bool, int]:
    """
    Return (trimmed_text, did_trim, dropped_session_count).

    Sessions start at lines containing ``[SESSION] start``.
    """
    if keep < 1 or not raw.strip():
        return raw, False, 0
    lines = raw.splitlines(keepends=True)
    idxs = [i for i, ln in enumerate(lines) if "[SESSION] start" in ln]
    if len(idxs) <= keep:
        return raw, False, 0
    cut = idxs[-keep]
    dropped = len(idxs) - keep
    return "".join(lines[cut:]), True, dropped


def _append_line_to_log_file(
    path: Path, component: str, event: str, data: Optional[Dict[str, Any]] = None
) -> None:
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(_format_debug_line(component, event, data))
            try:
                f.flush()
                os.fsync(f.fileno())
            except OSError:
                pass
    except OSError:
        pass


def _rewrite_log_file(path: Path, content: str) -> None:
    try:
        path.write_text(content, encoding="utf-8")
        try:
            fd = os.open(path, os.O_RDWR)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
    except OSError:
        pass


def init_debug_log() -> Path:
    """
    Create log directory, rotate to the last ``_DEBUG_LOG_MAX_SESSIONS`` sessions, and
    write a new session header. Safe to call once after ``QApplication`` exists.
    """
    global _log_path, _session_id
    with _lock:
        if _log_path is not None:
            return _log_path
        d = _resolve_log_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / "debug.log"
        prior = ""
        try:
            if path.is_file():
                prior = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            prior = ""
        trimmed, did_trim, dropped = _trim_log_to_last_sessions(
            prior, _DEBUG_LOG_MAX_SESSIONS
        )
        if did_trim:
            meta = _format_debug_line(
                "LOG",
                "rotated",
                {
                    "kept_sessions": _DEBUG_LOG_MAX_SESSIONS,
                    "dropped_sessions": dropped,
                },
            )
            trimmed = meta + trimmed
            _rewrite_log_file(path, trimmed)
        _log_path = path
        _session_id = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
        session_payload = {
            "id": _session_id,
            "argv": sys.argv,
            "executable": sys.executable,
        }
    _append_line_to_log_file(path, "SESSION", "start", session_payload)
    return path


def log_path() -> Optional[Path]:
    return _log_path


def debug_log(component: str, event: str, **data: Any) -> None:
    """Record one line to ``debug.log`` (thread-safe). Lazily initializes path."""
    global _log_path
    with _lock:
        if _log_path is None:
            try:
                init_debug_log()
            except Exception:
                return
        path = _log_path
    if path is None:
        return
    _append_line_to_log_file(path, component, event, data if data else None)


def register_shutdown_debug_hooks() -> None:
    """
    Log abrupt exits: ``SIGTERM``/``SIGINT`` (not ``SIGKILL``), and process ``atexit``.

    Normal GUI quit also runs ``atexit`` after the event loop exits.
    """
    global _shutdown_hooks_registered
    if _shutdown_hooks_registered:
        return
    _shutdown_hooks_registered = True

    def _atexit_log() -> None:
        debug_log("APP", "process_atexit")

    atexit.register(_atexit_log)

    if os.name == "nt":
        return

    def _on_signal(signum: int, _frame: object) -> None:
        try:
            name = signal.Signals(signum).name
        except (ValueError, AttributeError):
            name = str(signum)
        debug_log("APP", "signal_shutdown", signal=name, number=signum)
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _on_signal)
        except (OSError, ValueError):
            pass
