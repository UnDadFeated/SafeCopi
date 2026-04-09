"""Background tasks: destination space checks, updates, and rsync with progress parsing."""

from __future__ import annotations

import os
import re
import signal
from dataclasses import replace
from typing import Dict, List, Optional

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QProcess,
    QProcessEnvironment,
    QTimer,
    Signal,
    Slot,
)

from safecopi.debug_log import debug_log
from safecopi.utils import (
    RSYNC_RETRY_WAIT_SEC_MAX,
    RSYNC_TIMEOUT_SEC_MAX,
    build_rsync_command_argv,
    fetch_latest_github_version,
    is_rsync_filename_only_stderr_line,
    local_free_bytes,
    parse_rsync_transfer_progress_line,
    should_log_rsync_stderr_line,
)

# Bound memory if rsync emits huge chunks without newlines or a single gigantic “line”.
_RSYNC_STREAM_BUFFER_MAX: int = 256 * 1024
_RSYNC_IO_LINE_MAX_CHARS: int = 64 * 1024


class DestSpaceWorker(QObject):
    """Query free space on a **local** path without blocking the GUI thread."""

    # free_bytes, error_message (exception text), was_remote (always False)
    finished = Signal(object, object, bool)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._local_dest = ""
        self._local_query_timeout_sec: float = 120.0

    def prepare_local(self, dest: str, *, query_timeout_sec: float = 120.0) -> None:
        self._local_dest = dest
        self._local_query_timeout_sec = max(5.0, float(query_timeout_sec))

    @Slot()
    def run(self) -> None:
        debug_log("SPACE", "check_start", remote=False, transport="thread_local")
        try:
            free = local_free_bytes(
                self._local_dest, timeout_sec=self._local_query_timeout_sec
            )
            self.finished.emit(free, None, False)
        except Exception as e:  # noqa: BLE001
            debug_log("SPACE", "check_exception", error=str(e), transport="thread_local")
            self.finished.emit(None, str(e), False)
        finally:
            debug_log("SPACE", "check_worker_thread_end", transport="thread_local")
            app = QCoreApplication.instance()
            if app is not None:
                gui = app.thread()
                if gui is not None and self.thread() is not gui:
                    self.moveToThread(gui)


class GitHubUpdateCheckWorker(QObject):
    """Fetch latest release tag from GitHub API without blocking the GUI thread."""

    finished = Signal(object)  # Optional[str] tag_name

    @Slot()
    def run(self) -> None:
        debug_log("UPDATE", "github_check_start")
        try:
            tag, err = fetch_latest_github_version()
            if tag is None and err:
                debug_log("UPDATE", "github_check_failed", error=err)
            self.finished.emit(tag)
        finally:
            debug_log("UPDATE", "github_check_worker_end")
            app = QCoreApplication.instance()
            if app is not None:
                gui = app.thread()
                if gui is not None and self.thread() is not gui:
                    self.moveToThread(gui)


class RsyncWorker(QObject):
    """Runs rsync in a loop until success or stopped; emits parsed progress."""

    log_line = Signal(str)
    progress = Signal(object)  # RsyncProgressSnapshot from utils
    attempt_changed = Signal(int)
    # Multi-source sync: 1-based index and total runs (emit on first attempt of each source only).
    source_run_changed = Signal(int, int)
    sync_finished = Signal(int, bool)  # last_exit_code, success
    stopped_by_user = Signal()
    transfer_pause_state_changed = Signal(bool)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._process: Optional[QProcess] = None
        self._retry_timer: Optional[QTimer] = None
        self._stop_requested = False
        self._sync_active = False
        self._attempt = 1
        self._sources: List[str] = []
        self._source_index = 0
        self._multi_source = False
        self._dest = ""
        self._timeout_sec = 60
        self._retry_wait_sec = 15
        self._extra_args: List[str] = []
        self._recursive: bool = True
        self._env: Optional[QProcessEnvironment] = None
        self._stdout_linebuf: str = ""
        self._stderr_linebuf: str = ""
        self._transfer_os_paused: bool = False
        self._retry_paused: bool = False
        self._last_transfer_path: str = ""

    def is_syncing(self) -> bool:
        return self._sync_active

    def is_transfer_paused(self) -> bool:
        return self._transfer_os_paused or self._retry_paused

    def pause_transfer(self) -> bool:
        """
        Suspend the running rsync (POSIX SIGSTOP) or hold the next retry until :meth:`resume_transfer`.

        Returns True if a pause was applied.
        """
        if not self._sync_active or self._stop_requested:
            return False
        proc = self._process
        if proc is not None and proc.state() == QProcess.Running:
            pid = proc.processId()
            if pid and int(pid) > 0 and hasattr(signal, "SIGSTOP"):
                try:
                    os.kill(int(pid), signal.SIGSTOP)
                    self._transfer_os_paused = True
                    debug_log("RSYNC", "pause_sigstop", pid=int(pid))
                    self.log_line.emit("Paused — rsync suspended (SIGSTOP).")
                    self.transfer_pause_state_changed.emit(True)
                    return True
                except OSError as e:
                    debug_log("RSYNC", "pause_failed", error=str(e))
                    self.log_line.emit(f"Pause failed: {e}")
                    return False
            self.log_line.emit("Pause not supported on this platform (no SIGSTOP).")
            return False
        if self._retry_timer is not None and self._retry_timer.isActive():
            self._retry_timer.stop()
            self._retry_paused = True
            debug_log("RSYNC", "pause_retry_held")
            self.log_line.emit("Paused — next retry held until Resume.")
            self.transfer_pause_state_changed.emit(True)
            return True
        return False

    def resume_transfer(self) -> bool:
        """Resume after :meth:`pause_transfer` (SIGCONT or reschedule retry)."""
        if not self._sync_active or self._stop_requested:
            return False
        if self._transfer_os_paused:
            proc = self._process
            if proc is not None and proc.state() == QProcess.Running:
                pid = proc.processId()
                if pid and int(pid) > 0 and hasattr(signal, "SIGCONT"):
                    try:
                        os.kill(int(pid), signal.SIGCONT)
                        self._transfer_os_paused = False
                        debug_log("RSYNC", "resume_sigcont", pid=int(pid))
                        self.log_line.emit("Resumed — rsync continued (SIGCONT).")
                        self.transfer_pause_state_changed.emit(False)
                        return True
                    except OSError as e:
                        debug_log("RSYNC", "resume_failed", error=str(e))
                        self.log_line.emit(f"Resume failed: {e}")
                        return False
            self._transfer_os_paused = False
        if self._retry_paused:
            self._retry_paused = False
            self._schedule_retry()
            debug_log("RSYNC", "resume_retry_restarted")
            self.log_line.emit("Resumed — retry countdown restarted.")
            self.transfer_pause_state_changed.emit(False)
            return True
        return False

    def configure(
        self,
        sources: List[str],
        dest: str,
        timeout_sec: int,
        retry_wait_sec: int,
        extra_args: Optional[List[str]] = None,
        *,
        recursive: bool = True,
    ) -> None:
        self._sources = [s.strip() for s in sources if s.strip()]
        if not self._sources:
            self._sources = [""]
        self._multi_source = len(self._sources) > 1
        self._source_index = 0
        self._dest = dest.strip()
        try:
            to = int(timeout_sec)
        except (TypeError, ValueError):
            to = 60
        try:
            rw = int(retry_wait_sec)
        except (TypeError, ValueError):
            rw = 15
        self._timeout_sec = max(1, min(to, RSYNC_TIMEOUT_SEC_MAX))
        self._retry_wait_sec = max(1, min(rw, RSYNC_RETRY_WAIT_SEC_MAX))
        self._extra_args = list(extra_args or [])
        self._recursive = recursive
        self._attempt = 1
        self._stop_requested = False
        self._transfer_os_paused = False
        self._retry_paused = False
        self._cancel_retry_timer()
        debug_log(
            "RSYNC",
            "configure",
            source_count=len(self._sources),
            multi_source=self._multi_source,
            dest_nonempty=bool(self._dest),
            timeout_sec=self._timeout_sec,
            retry_wait_sec=self._retry_wait_sec,
            recursive=self._recursive,
        )

    def set_process_environment(self, env: QProcessEnvironment) -> None:
        self._env = env

    def stop(self) -> None:
        self._stop_requested = True
        if self._transfer_os_paused and self._process and self._process.state() != QProcess.NotRunning:
            pid = self._process.processId()
            if pid and int(pid) > 0 and hasattr(signal, "SIGCONT"):
                try:
                    os.kill(int(pid), signal.SIGCONT)
                except OSError:
                    pass
        self._transfer_os_paused = False
        self._retry_paused = False
        self.transfer_pause_state_changed.emit(False)
        self._cancel_retry_timer()
        if self._process and self._process.state() != QProcess.NotRunning:
            debug_log("RSYNC", "stop_kill_process")
            # Do not block the GUI thread on waitForFinished(); let _on_finished clean up.
            self._process.kill()
        elif self._sync_active:
            self._sync_active = False
            debug_log("RSYNC", "stop_between_attempts")
            self.log_line.emit("Stopped by user.")
            self.stopped_by_user.emit()

    def start_sync_loop(self) -> None:
        self._stop_requested = False
        self._transfer_os_paused = False
        self._retry_paused = False
        self._cancel_retry_timer()
        self._sync_active = True
        self._attempt = 1
        self._source_index = 0
        debug_log("RSYNC", "sync_loop_start", multi_source=self._multi_source)
        self._run_one_attempt()

    def _cancel_retry_timer(self) -> None:
        if self._retry_timer is not None:
            self._retry_timer.stop()
            self._retry_timer.deleteLater()
            self._retry_timer = None

    def _schedule_retry(self) -> None:
        self._cancel_retry_timer()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._on_retry_timeout)
        self._retry_timer = timer
        timer.start(self._retry_wait_sec * 1000)

    def _on_retry_timeout(self) -> None:
        t = self._retry_timer
        self._retry_timer = None
        if t is not None:
            t.deleteLater()
        if self._retry_paused:
            return
        debug_log("RSYNC", "retry_timer_fired", next_attempt=self._attempt)
        self._run_one_attempt()

    def _run_one_attempt(self) -> None:
        if self._stop_requested:
            self._sync_active = False
            debug_log("RSYNC", "aborted_before_attempt")
            self.log_line.emit("Stopped by user.")
            self.stopped_by_user.emit()
            return

        self.attempt_changed.emit(self._attempt)
        src_line = self._sources[self._source_index]
        if self._multi_source and self._attempt == 1:
            self.source_run_changed.emit(self._source_index + 1, len(self._sources))
        tag = (
            f"source {self._source_index + 1}/{len(self._sources)} · "
            if self._multi_source
            else ""
        )
        argv_src = src_line.rstrip("/") if self._multi_source and src_line.strip() else src_line
        if not (argv_src or "").strip():
            debug_log("RSYNC", "error_empty_source", source_index=self._source_index)
            self.log_line.emit("ERROR: empty source path — check the source list.")
            self._sync_active = False
            self.sync_finished.emit(-1, False)
            return
        debug_log(
            "RSYNC",
            "attempt_start",
            attempt=self._attempt,
            source_index=self._source_index,
            sources_total=len(self._sources),
            multi_source=self._multi_source,
        )
        self.log_line.emit(
            f"[Attempt {self._attempt}] {tag}{argv_src} → {self._dest} "
            f"(timeout={self._timeout_sec}s)"
        )
        self._stdout_linebuf = ""
        self._stderr_linebuf = ""
        self._last_transfer_path = ""

        if self._process is not None:
            old = self._process
            self._process = None
            old.blockSignals(True)
            old.deleteLater()

        self._process = QProcess(self)
        if self._env is not None:
            self._process.setProcessEnvironment(self._env)
        # Same as ssh subprocess: avoid inherited TTY so SSH uses SSH_ASKPASS for rsync transport.
        self._process.setStandardInputFile(QProcess.nullDevice())

        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_finished)

        argv = build_rsync_command_argv(
            argv_src,
            self._dest,
            self._timeout_sec,
            self._extra_args,
            recursive=self._recursive,
        )
        program, args = argv[0], argv[1:]

        self._process.start(program, args)
        if not self._process.waitForStarted(5000):
            debug_log("RSYNC", "error_rsync_failed_to_start")
            self.log_line.emit("ERROR: could not start rsync. Is it installed?")
            bad = self._process
            self._process = None
            bad.blockSignals(True)
            bad.deleteLater()
            self._sync_active = False
            self.sync_finished.emit(-1, False)

    def _normalize_stream_text(self, data: bytes) -> str:
        """Decode and normalize newlines (rsync may use \\r on a pseudo-TTY path)."""
        return (
            data.decode("utf-8", errors="replace")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

    def _feed_line_buffer(self, buf_attr: str, chunk: str) -> None:
        buf = getattr(self, buf_attr) + chunk
        while True:
            nl = buf.find("\n")
            if nl >= 0:
                self._handle_rsync_io_line(buf[:nl].rstrip())
                buf = buf[nl + 1 :]
                continue
            if len(buf) > _RSYNC_STREAM_BUFFER_MAX:
                debug_log(
                    "RSYNC",
                    "io_buffer_cleared_no_newline",
                    approx_kib=len(buf) // 1024,
                )
                self.log_line.emit(
                    "[SafeCopi] Cleared rsync output stuck without newline "
                    f"({len(buf) // 1024} KiB); pipe may be binary or corrupted."
                )
                buf = ""
            break
        setattr(self, buf_attr, buf)

    def _handle_rsync_io_line(self, line: str) -> None:
        """Progress lines update the transfer UI only; everything else is filter-logged."""
        if not line:
            return
        if len(line) > _RSYNC_IO_LINE_MAX_CHARS:
            debug_log(
                "RSYNC",
                "ignored_overlong_line",
                approx_kib=len(line) // 1024,
            )
            self.log_line.emit(
                "[SafeCopi] Ignored overlong rsync line "
                f"({len(line) // 1024} KiB)."
            )
            return
        snap = parse_rsync_transfer_progress_line(line)
        if snap is not None:
            path = self._last_transfer_path.strip() or None
            if path:
                snap = replace(snap, current_path=path)
            self.progress.emit(snap)
        elif is_rsync_filename_only_stderr_line(line):
            raw = line.strip()
            m = re.match(r"^[>][^\s]+\s+(.+)$", raw)
            self._last_transfer_path = m.group(1).strip() if m else raw
        elif should_log_rsync_stderr_line(line):
            self.log_line.emit(line)

    def _flush_io_line_buffers(self) -> None:
        for attr in ("_stdout_linebuf", "_stderr_linebuf"):
            rest = getattr(self, attr)
            if rest.strip():
                self._handle_rsync_io_line(rest.rstrip())
            setattr(self, attr, "")

    def _on_stdout(self) -> None:
        if not self._process:
            return
        data = bytes(self._process.readAllStandardOutput())
        self._feed_line_buffer("_stdout_linebuf", self._normalize_stream_text(data))

    def _on_stderr(self) -> None:
        if not self._process:
            return
        data = bytes(self._process.readAllStandardError())
        self._feed_line_buffer("_stderr_linebuf", self._normalize_stream_text(data))

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._flush_io_line_buffers()
        self._transfer_os_paused = False
        self.transfer_pause_state_changed.emit(self.is_transfer_paused())
        proc = self._process
        self._process = None
        if proc is not None:
            proc.blockSignals(True)
            proc.deleteLater()

        if self._stop_requested:
            self._sync_active = False
            debug_log("RSYNC", "finished_user_stop")
            self.log_line.emit("Stopped by user.")
            self.stopped_by_user.emit()
            return

        # QProcess exitCode: on Unix, process exit status
        code = exit_code
        if exit_status != QProcess.NormalExit:
            code = -1
            debug_log(
                "RSYNC",
                "process_abnormal_exit",
                exit_status=str(exit_status),
            )
            self.log_line.emit("rsync terminated abnormally.")

        if code == 0:
            if self._multi_source and self._source_index + 1 < len(self._sources):
                debug_log(
                    "RSYNC",
                    "multi_source_segment_done",
                    completed_index=self._source_index,
                    total_sources=len(self._sources),
                )
                self.log_line.emit(
                    f"Finished source {self._source_index + 1}/{len(self._sources)}."
                )
                self._source_index += 1
                self._attempt = 1
                self._run_one_attempt()
                return
            debug_log("RSYNC", "sync_completed_success")
            self.log_line.emit("Sync completed successfully.")
            self._sync_active = False
            self.sync_finished.emit(0, True)
            return

        debug_log(
            "RSYNC",
            "exit_nonzero_scheduling_retry",
            exit_code=code,
            retry_wait_sec=self._retry_wait_sec,
            next_attempt=self._attempt + 1,
        )
        self.log_line.emit(
            f"rsync exited with code {code}. Retrying in {self._retry_wait_sec}s…"
        )
        self._attempt += 1
        self._schedule_retry()
