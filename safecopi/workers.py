"""Background tasks: source scan and rsync with progress parsing."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QProcess,
    QProcessEnvironment,
    QTimer,
    Signal,
    Slot,
)

from safecopi.utils import (
    build_rsync_command_argv,
    parse_rsync_transfer_progress_line,
    scan_source_tree_stats,
    should_log_rsync_stderr_line,
)


class SourceScanWorker(QObject):
    """Compute file count and total bytes in one tree walk (interactive on slow LAN mounts)."""

    # Third flag: True when the user requested stop before the walk finished.
    finished = Signal(object, object, object)
    failed = Signal(str)
    phase = Signal(str)
    # Use object, not int: Qt's int is 32-bit; total bytes exceed 2^31 on large trees.
    scan_progress = Signal(object, object)  # files_seen: int, total_bytes_so_far: int

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self._source_path = ""

    def prepare_source(self, source_path: str) -> None:
        """Set the tree to scan; call before ``thread.start()`` and the slot ``run()``."""
        self._source_path = source_path

    def request_cancel(self) -> None:
        """Request cooperative cancellation (checked between files during the walk)."""
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            source_path = self._source_path
            self._cancel.clear()
            path = Path(source_path).expanduser()
            if not path.is_dir():
                self.failed.emit(f"Source is not a directory: {source_path}")
                return
            self.phase.emit(
                "Walking source tree — summing file sizes (can be slow on network mounts)."
            )
            self.scan_progress.emit(0, 0)
            try:

                def _on_prog(n: int, total_b: int) -> None:
                    self.scan_progress.emit(n, total_b)

                count, size_b = scan_source_tree_stats(
                    str(path),
                    on_progress=_on_prog,
                    should_cancel=lambda: self._cancel.is_set(),
                )
            except Exception as e:  # noqa: BLE001
                self.failed.emit(str(e))
                return
            if count is None:
                self.failed.emit(
                    "Could not read the source tree (permissions or I/O error)."
                )
                return
            was_cancelled = self._cancel.is_set()
            self.finished.emit(count, size_b, was_cancelled)
        finally:
            # Repatriate to the GUI thread before the worker QThread stops. Otherwise
            # thread.finished → worker.deleteLater targets a dead event loop (Qt warning,
            # crashes / SIGBUS with PySide).
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
    sync_finished = Signal(int, bool)  # last_exit_code, success
    stopped_by_user = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._process: Optional[QProcess] = None
        self._retry_timer: Optional[QTimer] = None
        self._stop_requested = False
        self._sync_active = False
        self._attempt = 1
        self._source = ""
        self._dest = ""
        self._timeout_sec = 60
        self._retry_wait_sec = 15
        self._extra_args: List[str] = []
        self._recursive: bool = True
        self._env: Optional[QProcessEnvironment] = None

    def is_syncing(self) -> bool:
        return self._sync_active

    def configure(
        self,
        source: str,
        dest: str,
        timeout_sec: int,
        retry_wait_sec: int,
        extra_args: Optional[List[str]] = None,
        *,
        recursive: bool = True,
    ) -> None:
        self._source = source
        self._dest = dest
        self._timeout_sec = max(1, timeout_sec)
        self._retry_wait_sec = max(1, retry_wait_sec)
        self._extra_args = list(extra_args or [])
        self._recursive = recursive
        self._attempt = 1
        self._stop_requested = False
        self._cancel_retry_timer()

    def set_process_environment(self, env: QProcessEnvironment) -> None:
        self._env = env

    def stop(self) -> None:
        self._stop_requested = True
        self._cancel_retry_timer()
        if self._process and self._process.state() != QProcess.NotRunning:
            # Do not block the GUI thread on waitForFinished(); let _on_finished clean up.
            self._process.kill()
        elif self._sync_active:
            self._sync_active = False
            self.log_line.emit("Stopped by user.")
            self.stopped_by_user.emit()

    def start_sync_loop(self) -> None:
        self._stop_requested = False
        self._cancel_retry_timer()
        self._sync_active = True
        self._attempt = 1
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
        self._run_one_attempt()

    def _run_one_attempt(self) -> None:
        if self._stop_requested:
            self._sync_active = False
            self.log_line.emit("Stopped by user.")
            self.stopped_by_user.emit()
            return

        self.attempt_changed.emit(self._attempt)
        self.log_line.emit(
            f"[Attempt {self._attempt}] rsync → {self._dest} (timeout={self._timeout_sec}s)"
        )

        if self._process is not None:
            old = self._process
            self._process = None
            old.blockSignals(True)
            try:
                old.disconnect(self)
            except (RuntimeError, TypeError):
                pass
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
            self._source,
            self._dest,
            self._timeout_sec,
            self._extra_args,
            recursive=self._recursive,
        )
        program, args = argv[0], argv[1:]

        self._process.start(program, args)
        if not self._process.waitForStarted(5000):
            self.log_line.emit("ERROR: could not start rsync. Is it installed?")
            bad = self._process
            self._process = None
            bad.blockSignals(True)
            try:
                bad.disconnect(self)
            except (RuntimeError, TypeError):
                pass
            bad.deleteLater()
            self._sync_active = False
            self.sync_finished.emit(-1, False)

    def _on_stdout(self) -> None:
        if not self._process:
            return
        data = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            line = line.rstrip()
            if line:
                self.log_line.emit(line)

    def _on_stderr(self) -> None:
        if not self._process:
            return
        data = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            line = line.rstrip()
            if not line:
                continue
            snap = parse_rsync_transfer_progress_line(line)
            if snap is not None:
                self.progress.emit(snap)
            elif should_log_rsync_stderr_line(line):
                self.log_line.emit(line)

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        proc = self._process
        self._process = None
        if proc is not None:
            proc.blockSignals(True)
            try:
                proc.disconnect(self)
            except (RuntimeError, TypeError):
                pass
            proc.deleteLater()

        if self._stop_requested:
            self._sync_active = False
            self.log_line.emit("Stopped by user.")
            self.stopped_by_user.emit()
            return

        # QProcess exitCode: on Unix, process exit status
        code = exit_code
        if exit_status != QProcess.NormalExit:
            code = -1
            self.log_line.emit("rsync terminated abnormally.")

        if code == 0:
            self.log_line.emit("Sync completed successfully.")
            self._sync_active = False
            self.sync_finished.emit(0, True)
            return

        self.log_line.emit(
            f"rsync exited with code {code}. Retrying in {self._retry_wait_sec}s…"
        )
        self._attempt += 1
        self._schedule_retry()
