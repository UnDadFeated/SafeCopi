"""Main SafeCopi window: resilient rsync with preflight checks and progress."""

from __future__ import annotations

import os
import shlex
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QElapsedTimer,
    QProcessEnvironment,
    QSettings,
    QThread,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QCheckBox,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from safecopi import __version__
from safecopi.utils import (
    RemoteTarget,
    RsyncProgressSnapshot,
    build_rsync_command_argv,
    ensure_ssh_askpass_wrapper,
    fetch_latest_github_version,
    format_rsync_hms_for_display,
    format_seconds_as_hms_display,
    human_bytes,
    is_remote_version_newer,
    parse_rsync_speed_to_bytes_per_sec,
    local_free_bytes,
    parse_extra_rsync_args,
    parse_rsync_destination,
    remote_df_free_bytes,
    rsync_ssh_e_shell,
    run_ssh_command,
)
from safecopi.workers import RsyncWorker, SourceScanWorker


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"SafeCopi — resilient sync ({__version__})")
        self.setFixedSize(720, 880)

        self._settings_loading = False
        self._last_sync_was_dry_run = False
        self._last_scan: Tuple[Optional[int], Optional[int]] = (None, None)
        self._dest_free_bytes: Optional[int] = None
        self._ssh_ok_this_session: bool = False
        self._scan_thread: Optional[QThread] = None
        self._scan_worker_ref: Optional[SourceScanWorker] = None
        self._scan_pending_n: int = 0
        self._scan_pending_b: int = 0
        self._scan_ui_timer = QTimer(self)
        self._scan_ui_timer.setSingleShot(True)
        self._scan_ui_timer.timeout.connect(self._flush_scan_progress_ui)
        self._guide_pulse_timer = QTimer(self)
        self._guide_pulse_timer.setInterval(550)
        self._guide_pulse_timer.timeout.connect(self._pulse_guide)
        self._guide_glow_phase = 0
        self._guide_target: Optional[QPushButton] = None
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.timeout.connect(self._persist_settings)
        self._sync_progress_timer = QTimer(self)
        self._sync_progress_timer.setSingleShot(True)
        self._sync_progress_timer.timeout.connect(self._flush_sync_transfer_ui)
        self._session_elapsed = QElapsedTimer()
        self._sync_session_active = False
        self._sync_session_wall_timer = QTimer(self)
        self._sync_session_wall_timer.setInterval(500)
        self._sync_session_wall_timer.timeout.connect(self._update_sync_session_elapsed_label)
        self._pending_sync_snap: Optional[RsyncProgressSnapshot] = None
        self._sync_attempt_shown = 1
        self._sync_bar_peak: int = 0
        self._rsync = RsyncWorker(self)

        self._source = QLineEdit("/mnt/nas/Archive/")
        self._dest = QLineEdit("htpc@192.168.4.112:/mnt/media_hdd/Backup/Archive/")
        for pe in (self._source, self._dest):
            pe.setObjectName("PathLineEdit")
            pe.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            pe.setMinimumHeight(28)
            pe.setClearButtonEnabled(True)
        self._source.setPlaceholderText('"user@ip:/path" or "/path"')
        self._dest.setPlaceholderText('"user@ip:/path" or "/path"')
        self._timeout = QSpinBox()
        self._timeout.setRange(10, 86400)
        self._timeout.setValue(60)
        self._timeout.setSuffix(" s")
        self._retry = QSpinBox()
        self._retry.setRange(5, 3600)
        self._retry.setValue(15)
        self._retry.setSuffix(" s")

        self._dry_run = QCheckBox("Dry run (no writes)")
        self._recursive_subdirs = QCheckBox("Include subdirectories (recursive)")
        self._recursive_subdirs.setChecked(True)
        self._recursive_subdirs.setToolTip(
            "When on, rsync uses archive mode (-a) and copies the full tree. "
            "When off, only the top directory level is synced (no -r); subfolders are skipped."
        )

        self._radio_resume_partial = QRadioButton("Resume with --partial")
        self._radio_redo_partial = QRadioButton("Redo interrupted files (no --partial)")
        self._radio_resume_partial.setChecked(True)
        self._radio_resume_partial.setToolTip(
            "Keep incomplete files and continue where the last run stopped."
        )
        self._radio_redo_partial.setToolTip(
            "Do not keep incomplete files: if a file transfer stops, rsync removes the partial "
            "file and the next run copies that file from the beginning."
        )
        self._bg_partial = QButtonGroup(self)
        self._bg_partial.addButton(self._radio_resume_partial)
        self._bg_partial.addButton(self._radio_redo_partial)

        self._ssh_password_src = QLineEdit()
        self._ssh_password_src.setEchoMode(QLineEdit.EchoMode.Password)
        self._ssh_password_src.setPlaceholderText("Optional (remote src)")
        self._ssh_password_src.setToolTip(
            "When the source is user@host:/path, optional password for sshpass or SSH_ASKPASS. "
            "Not saved. Empty if you use SSH keys."
        )

        self._ssh_password = QLineEdit()
        self._ssh_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._ssh_password.setPlaceholderText("Optional (remote dest)")
        self._ssh_password.setToolTip(
            "When the destination is user@host:/path, optional password for sshpass or SSH_ASKPASS. "
            "Not saved. Empty if you use SSH keys. If both ends are remote, sshpass uses this field "
            "first when set; otherwise the source password."
        )

        self._btn_browse = QPushButton("Browse…")
        self._btn_browse.setMinimumHeight(28)
        self._btn_browse.setToolTip("Choose a local source directory.")
        self._btn_browse.clicked.connect(self._browse_source)
        self._btn_browse_dest = QPushButton("Browse…")
        self._btn_browse_dest.setMinimumHeight(28)
        self._btn_browse_dest.setToolTip(
            "Choose a local destination folder. For SSH/rsync remote targets, type "
            "user@host:/path in the field; choosing a folder here sets a local path instead."
        )
        self._btn_browse_dest.clicked.connect(self._browse_destination)

        self._lbl_ssh_pw_src = QLabel("Src. password")
        self._lbl_ssh_pw_src.setStyleSheet("color: #bac2de;")
        self._lbl_ssh_pw_src.setToolTip("Password for the remote source host when the source is user@host:/path.")
        self._ssh_pw_src_wrap = QWidget()
        _spws = QHBoxLayout(self._ssh_pw_src_wrap)
        _spws.setContentsMargins(10, 0, 0, 0)
        _spws.setSpacing(6)
        _spws.addWidget(self._lbl_ssh_pw_src, 0)
        _spws.addWidget(self._ssh_password_src, 0)
        self._ssh_password_src.setMinimumWidth(120)
        self._ssh_password_src.setMaximumWidth(180)
        self._ssh_password_src.setMinimumHeight(28)
        self._ssh_pw_src_wrap.setVisible(False)

        self._lbl_ssh_pw = QLabel("Dest. password")
        self._lbl_ssh_pw.setStyleSheet("color: #bac2de;")
        self._lbl_ssh_pw.setToolTip(
            "Password for the remote destination host when the destination is user@host:/path."
        )
        self._ssh_pw_wrap = QWidget()
        _spw = QHBoxLayout(self._ssh_pw_wrap)
        _spw.setContentsMargins(10, 0, 0, 0)
        _spw.setSpacing(6)
        _spw.addWidget(self._lbl_ssh_pw, 0)
        _spw.addWidget(self._ssh_password, 0)
        self._ssh_password.setMinimumWidth(120)
        self._ssh_password.setMaximumWidth(180)
        self._ssh_password.setMinimumHeight(28)
        self._ssh_pw_wrap.setVisible(False)
        self._btn_ssh = QPushButton("Test SSH")
        self._btn_ssh.clicked.connect(self._test_ssh)
        self._btn_space = QPushButton("Dest. space")
        self._btn_space.setToolTip("Query free disk space on the destination (local df or remote over SSH).")
        self._btn_space.clicked.connect(self._check_dest_space)
        self._btn_scan = QPushButton("Scan source")
        self._btn_scan.clicked.connect(self._scan_source)
        self._btn_stop_scan = QPushButton("Stop scan")
        self._btn_stop_scan.setEnabled(False)
        self._btn_stop_scan.clicked.connect(self._stop_scan)
        self._btn_start = QPushButton("Start sync")
        self._btn_start.clicked.connect(self._start_sync)
        self._btn_pause = QPushButton("Pause")
        self._btn_pause.setToolTip(
            "Suspend the running rsync (POSIX SIGSTOP) or hold the next retry. "
            "Use Resume to continue. Stop still terminates the sync."
        )
        self._btn_pause.setEnabled(False)
        self._btn_pause.clicked.connect(self._toggle_sync_pause)
        self._btn_stop = QPushButton("Stop")
        self._btn_stop.clicked.connect(self._stop_sync)
        self._btn_stop.setEnabled(False)

        self._lbl_files = QLabel("—")
        self._lbl_src_size = QLabel("—")
        self._lbl_dest_free = QLabel("—")

        self._lbl_scan_idle = QLabel("—")
        self._scan_bar = QProgressBar()
        self._scan_bar.setRange(0, 0)
        self._scan_bar.setFixedHeight(14)
        self._scan_bar.setTextVisible(False)
        self._scan_row = QWidget()
        _srl = QHBoxLayout(self._scan_row)
        _srl.setContentsMargins(0, 0, 0, 0)
        _srl.setSpacing(8)
        _srl.addWidget(self._lbl_scan_idle, 0)
        _srl.addWidget(self._scan_bar, 1)
        self._scan_bar.setVisible(False)
        self._scan_bar.setObjectName("ScanBar")
        self._lbl_hint = QLabel(
            "Source trailing slash: <code>dir/</code> copies contents; <code>dir</code> copies the folder."
        )
        self._lbl_hint.setWordWrap(True)
        self._lbl_hint.setTextFormat(Qt.TextFormat.RichText)
        self._lbl_hint.setStyleSheet("color: #8ab4d8; font-size: 10px; margin-top: 2px;")

        transfer = QGroupBox("File transfer")
        tlay = QVBoxLayout(transfer)
        tlay.setSpacing(4)
        tlay.setContentsMargins(5, 6, 5, 5)

        self._progress = QProgressBar()
        self._progress.setObjectName("SyncTransferBar")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%p%")
        self._progress.setMinimumHeight(26)

        self._lbl_sync_elapsed = QLabel("Session elapsed: —")
        self._lbl_sync_elapsed.setStyleSheet(
            "color: #a6adc8; font-size: 11px; font-family: monospace;"
        )
        self._lbl_sync_elapsed.setToolTip(
            "Wall time since Start sync was pressed (this run). Independent of rsync’s internal elapsed field."
        )

        self._lbl_sync_detail = QLabel()
        self._lbl_sync_detail.setWordWrap(True)
        self._lbl_sync_detail.setStyleSheet("color: #bac2de; font-size: 11px;")

        tlay.addWidget(self._progress)
        tlay.addWidget(self._lbl_sync_elapsed)
        tlay.addWidget(self._lbl_sync_detail)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(8000)
        log_font = QFont("monospace")
        log_font.setStyleHint(QFont.Monospace)
        self._log.setFont(log_font)

        paths = QGroupBox("Paths")
        fl = QFormLayout(paths)
        self._compact_form(fl)
        row_src = QHBoxLayout()
        row_src.setSpacing(6)
        row_src.setContentsMargins(0, 0, 0, 0)
        row_src.addWidget(self._source, 1)
        row_src.addWidget(self._btn_browse, 0)
        row_src.addWidget(self._ssh_pw_src_wrap, 0)
        w_src = QWidget()
        w_src.setLayout(row_src)
        fl.addRow("Source", w_src)
        row_dest = QHBoxLayout()
        row_dest.setSpacing(6)
        row_dest.setContentsMargins(0, 0, 0, 0)
        row_dest.addWidget(self._dest, 1)
        row_dest.addWidget(self._btn_browse_dest, 0)
        row_dest.addWidget(self._ssh_pw_wrap, 0)
        w_dest = QWidget()
        w_dest.setLayout(row_dest)
        fl.addRow("Destination", w_dest)

        opts = QGroupBox("Rsync")
        fo = QFormLayout(opts)
        self._compact_form(fo)
        self._timeout.setMaximumWidth(110)
        self._retry.setMaximumWidth(110)
        spin_row = QHBoxLayout()
        spin_row.setSpacing(6)
        spin_row.addWidget(QLabel("I/O"))
        spin_row.addWidget(self._timeout)
        spin_row.addSpacing(4)
        spin_row.addWidget(QLabel("Retry"))
        spin_row.addWidget(self._retry)
        spin_row.addStretch()
        spin_w = QWidget()
        spin_w.setLayout(spin_row)
        fo.addRow("Delays", spin_w)
        row_chk = QHBoxLayout()
        row_chk.setSpacing(12)
        row_chk.addWidget(self._dry_run)
        row_chk.addWidget(self._recursive_subdirs)
        row_chk.addStretch()
        wc = QWidget()
        wc.setLayout(row_chk)
        fo.addRow("Options", wc)
        partial_row = QHBoxLayout()
        partial_row.setSpacing(16)
        partial_row.addWidget(self._radio_resume_partial)
        partial_row.addWidget(self._radio_redo_partial)
        partial_row.addStretch()
        partial_w = QWidget()
        partial_w.setLayout(partial_row)
        fo.addRow("Partial files", partial_w)

        self._bwlimit = QSpinBox()
        self._bwlimit.setRange(0, 999_999)
        self._bwlimit.setValue(0)
        self._bwlimit.setSpecialValueText("off")
        self._bwlimit.setToolTip("rsync --bwlimit in KiB/s. 0 disables throttling.")
        fo.addRow("BW limit", self._bwlimit)

        self._extra_rsync = QLineEdit()
        self._extra_rsync.setPlaceholderText('e.g. --delete --exclude=.git')
        self._extra_rsync.setToolTip(
            "Extra rsync flags, POSIX shell–split (quoted groups allowed). "
            "Appended after built-in options; use with care."
        )
        fo.addRow("Extra args", self._extra_rsync)

        cmd_box = QGroupBox("Command preview")
        cbl = QVBoxLayout(cmd_box)
        cbl.setContentsMargins(5, 5, 5, 5)
        self._rsync_preview = QPlainTextEdit()
        self._rsync_preview.setReadOnly(True)
        self._rsync_preview.setMaximumBlockCount(4)
        self._rsync_preview.setFixedHeight(36)
        self._rsync_preview.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        pf = QFont("monospace")
        pf.setStyleHint(QFont.Monospace)
        self._rsync_preview.setFont(pf)
        cbl.addWidget(self._rsync_preview)

        stats = QGroupBox("Preflight")
        fs = QFormLayout(stats)
        self._compact_form(fs)
        fs.setHorizontalSpacing(4)
        fs.addRow("Source files", self._lbl_files)
        fs.addRow("Source size", self._lbl_src_size)
        fs.addRow("Destination free", self._lbl_dest_free)
        fs.addRow("Scan", self._scan_row)
        fs.addRow(self._lbl_hint)

        actions = QHBoxLayout()
        actions.setSpacing(5)
        actions.setContentsMargins(0, 2, 0, 0)
        for b in (
            self._btn_ssh,
            self._btn_space,
            self._btn_scan,
            self._btn_stop_scan,
            self._btn_start,
            self._btn_pause,
            self._btn_stop,
        ):
            b.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        actions.addWidget(self._btn_ssh, 0)
        actions.addWidget(self._btn_space, 0)
        actions.addStretch(1)
        actions.addWidget(self._btn_scan, 0)
        actions.addWidget(self._btn_stop_scan, 0)
        actions.addStretch(1)
        actions.addWidget(self._btn_start, 0)
        actions.addWidget(self._btn_pause, 0)
        actions.addWidget(self._btn_stop, 0)

        preflight_transfer_row = QWidget()
        pt_lay = QHBoxLayout(preflight_transfer_row)
        pt_lay.setContentsMargins(0, 0, 0, 0)
        pt_lay.setSpacing(6)
        # Preflight ~25% width, File transfer ~75% (one row to save vertical space).
        pt_lay.addWidget(stats, 1, Qt.AlignmentFlag.AlignTop)
        pt_lay.addWidget(transfer, 3, Qt.AlignmentFlag.AlignTop)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)
        root.addWidget(paths)
        root.addWidget(opts)
        root.addWidget(preflight_transfer_row)
        root.addLayout(actions)
        root.addWidget(cmd_box)

        log_btns = QHBoxLayout()
        log_btns.setSpacing(6)
        self._btn_copy_log = QPushButton("Copy log")
        self._btn_copy_log.setToolTip("Copy the full log to the clipboard.")
        self._btn_copy_log.clicked.connect(self._copy_log)
        self._btn_save_log = QPushButton("Save log…")
        self._btn_save_log.setToolTip("Save the log to a text file.")
        self._btn_save_log.clicked.connect(self._save_log)
        self._btn_check_update = QPushButton("Check for update…")
        self._btn_check_update.setToolTip(
            "Check GitHub for a newer SafeCopi release (compares against this build’s version)."
        )
        self._btn_check_update.clicked.connect(self._check_for_update)
        log_btns.addWidget(self._btn_copy_log)
        log_btns.addWidget(self._btn_save_log)
        log_btns.addStretch()
        log_btns.addWidget(self._btn_check_update)
        root.addLayout(log_btns)

        root.addWidget(self._log, stretch=1)

        self._apply_style()

        self._wire_settings_persistence()
        self._load_settings_from_disk()
        self._refresh_rsync_preview()

        self._rsync.log_line.connect(self._append_log)
        self._rsync.progress.connect(self._on_rsync_progress, Qt.QueuedConnection)
        self._rsync.attempt_changed.connect(self._on_attempt)
        self._rsync.transfer_pause_state_changed.connect(
            self._on_rsync_pause_state_changed, Qt.QueuedConnection
        )
        self._rsync.sync_finished.connect(self._on_sync_finished)
        self._rsync.stopped_by_user.connect(self._on_stopped)

        self._reset_sync_transfer_panel()
        self._sync_guide_pulse()

    @staticmethod
    def _compact_form(form: QFormLayout) -> None:
        form.setSpacing(2)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(3)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background-color: #1e1e2e; color: #e0e0e8; font-size: 12px; }
            QGroupBox {
                font-weight: 600;
                border: 1px solid #45475a;
                border-radius: 6px;
                margin-top: 6px;
                padding: 5px 6px 5px 6px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 7px; padding: 0 3px; }
            QLineEdit, QPlainTextEdit {
                background-color: #313244;
                border: 1px solid #585b70;
                border-radius: 4px;
                padding: 4px 7px;
                selection-background-color: #89b4fa;
            }
            QLineEdit#PathLineEdit {
                min-height: 28px;
                padding: 5px 9px;
                font-family: "JetBrains Mono", "Cascadia Code", "Cascadia Mono", "Fira Code",
                    Consolas, "Liberation Mono", monospace;
                font-size: 11px;
                border: 1px solid #6c7086;
                background-color: #292c3c;
            }
            QLineEdit#PathLineEdit:focus {
                border: 1px solid #89b4fa;
                background-color: #313244;
            }
            QSpinBox {
                background-color: #313244;
                border: 1px solid #585b70;
                border-radius: 4px;
                padding: 2px 4px;
                min-height: 24px;
                min-width: 76px;
                selection-background-color: #89b4fa;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 18px;
                border-left: 1px solid #585b70;
                background-color: #3b3f54;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #45475a;
            }
            QPushButton {
                background-color: #45475a;
                border: 2px solid #585b70;
                border-radius: 4px;
                padding: 4px 9px;
                min-width: 0;
            }
            QPushButton:hover { background-color: #585b70; }
            QPushButton:pressed { background-color: #313244; }
            QPushButton:disabled { color: #6c7086; }
            QProgressBar {
                border: 1px solid #585b70;
                border-radius: 4px;
                text-align: center;
                height: 20px;
                background-color: #313244;
            }
            QProgressBar::chunk { background-color: #a6e3a1; border-radius: 3px; }
            QCheckBox { spacing: 6px; }
            QRadioButton { spacing: 6px; }
            QProgressBar#ScanBar { background-color: #313244; border: 1px solid #585b70; border-radius: 4px; height: 14px; }
            QProgressBar#ScanBar::chunk { background-color: #89b4fa; border-radius: 3px; }
            QProgressBar#SyncTransferBar {
                border: 1px solid #585b70;
                border-radius: 5px;
                min-height: 26px;
                text-align: center;
                font-weight: 600;
                background-color: #313244;
            }
            QProgressBar#SyncTransferBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #74c7ec, stop:1 #89b4fa);
                border-radius: 4px;
            }
            QPushButton[guidePulse="true"] {
                border: 2px solid #ef4444;
                padding: 4px 9px;
            }
            """
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        self._persist_settings()
        if self._scan_thread is not None and self._scan_thread.isRunning():
            QMessageBox.warning(
                self,
                "Quit",
                "Wait for the source scan to finish or click Stop scan, then close again.",
            )
            event.ignore()
            return
        if self._rsync.is_syncing():
            QMessageBox.warning(
                self,
                "Quit",
                "Stop the sync with the Stop button, then close again.",
            )
            event.ignore()
            return
        super().closeEvent(event)

    def _wire_settings_persistence(self) -> None:
        for w in (self._source, self._dest, self._extra_rsync):
            w.textChanged.connect(self._debounce_settings_and_preview)
        self._source.textChanged.connect(self._on_paths_or_ssh_context_changed)
        self._dest.textChanged.connect(self._on_paths_or_ssh_context_changed)
        self._source.textChanged.connect(self._update_ssh_password_visibility)
        self._dest.textChanged.connect(self._update_ssh_password_visibility)
        self._timeout.valueChanged.connect(self._debounce_settings_and_preview)
        self._retry.valueChanged.connect(self._debounce_settings_and_preview)
        self._bwlimit.valueChanged.connect(self._debounce_settings_and_preview)
        self._dry_run.toggled.connect(self._debounce_settings_and_preview)
        self._recursive_subdirs.toggled.connect(self._debounce_settings_and_preview)
        self._radio_resume_partial.toggled.connect(self._debounce_settings_and_preview)
        self._radio_redo_partial.toggled.connect(self._debounce_settings_and_preview)

    @Slot()
    def _on_paths_or_ssh_context_changed(self) -> None:
        self._ssh_ok_this_session = False
        self._sync_guide_pulse()

    @Slot()
    def _debounce_settings_and_preview(self) -> None:
        if self._settings_loading:
            return
        self._persist_timer.start(450)

    @Slot()
    def _persist_settings(self) -> None:
        if self._settings_loading:
            return
        s = QSettings("SafeCopi", "SafeCopi")
        s.setValue("source", self._source.text())
        s.setValue("dest", self._dest.text())
        s.setValue("io_timeout", self._timeout.value())
        s.setValue("retry_delay", self._retry.value())
        s.setValue("dry_run", self._dry_run.isChecked())
        s.setValue("recursive_subdirs", self._recursive_subdirs.isChecked())
        s.setValue("partial_resume", self._radio_resume_partial.isChecked())
        s.setValue("extra_rsync", self._extra_rsync.text())
        s.setValue("bwlimit", self._bwlimit.value())
        s.sync()
        self._refresh_rsync_preview()

    @staticmethod
    def _settings_int(
        s: QSettings, key: str, default: int, lo: int, hi: int
    ) -> int:
        raw = s.value(key, default)
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    def _load_settings_from_disk(self) -> None:
        self._settings_loading = True
        try:
            s = QSettings("SafeCopi", "SafeCopi")
            self._source.setText(s.value("source", self._source.text(), type=str))
            self._dest.setText(s.value("dest", self._dest.text(), type=str))
            self._timeout.setValue(
                self._settings_int(s, "io_timeout", self._timeout.value(), 10, 86400)
            )
            self._retry.setValue(
                self._settings_int(s, "retry_delay", self._retry.value(), 5, 3600)
            )
            self._dry_run.setChecked(s.value("dry_run", self._dry_run.isChecked(), type=bool))
            self._recursive_subdirs.setChecked(
                s.value("recursive_subdirs", self._recursive_subdirs.isChecked(), type=bool)
            )
            pr = s.value("partial_resume", self._radio_resume_partial.isChecked(), type=bool)
            self._radio_resume_partial.setChecked(pr)
            self._radio_redo_partial.setChecked(not pr)
            self._extra_rsync.setText(s.value("extra_rsync", "", type=str))
            self._bwlimit.setValue(self._settings_int(s, "bwlimit", 0, 0, 999_999))
        finally:
            self._settings_loading = False
            self._update_ssh_password_visibility()

    def _refresh_rsync_preview(self) -> None:
        try:
            self._parsed_user_extra_args()
        except ValueError as e:
            self._rsync_preview.setPlainText(f"(invalid extra args: {e})")
            return
        argv = build_rsync_command_argv(
            self._source.text().strip(),
            self._dest.text().strip(),
            self._timeout.value(),
            self._collect_rsync_modifiers(),
            recursive=self._recursive_subdirs.isChecked(),
        )
        self._rsync_preview.setPlainText(shlex.join(argv))

    def _parsed_user_extra_args(self) -> List[str]:
        return parse_extra_rsync_args(self._extra_rsync.text())

    def _collect_rsync_modifiers(self) -> List[str]:
        args: List[str] = []
        if self._radio_resume_partial.isChecked():
            args.append("--partial")
        if self._dry_run.isChecked():
            args.append("--dry-run")
        bw = self._bwlimit.value()
        if bw > 0:
            args.append(f"--bwlimit={bw}")
        args.extend(self._parsed_user_extra_args())
        src_remote, _ = self._parsed_source()
        dst_remote, _rpath = self._parsed_destination()
        if src_remote is not None or dst_remote is not None:
            args.extend(
                [
                    "-e",
                    rsync_ssh_e_shell(
                        self._timeout.value(),
                        self._ssh_batch_mode(),
                        password_for_sshpass=self._rsync_sshpass_password(),
                    ),
                ]
            )
        return args

    def _disconnect_scan_worker(self, worker: Optional[SourceScanWorker]) -> None:
        if worker is None:
            return
        for sig, slot in (
            (worker.phase, self._on_scan_phase),
            (worker.scan_progress, self._on_scan_progress),
            (worker.finished, self._on_source_scan_finished),
            (worker.failed, self._on_source_scan_failed),
        ):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    @Slot()
    def _copy_log(self) -> None:
        QApplication.clipboard().setText(self._log.toPlainText())

    @Slot()
    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save log",
            "",
            "Text (*.log *.txt);;All files (*)",
        )
        if path:
            try:
                Path(path).write_text(self._log.toPlainText(), encoding="utf-8")
            except OSError as e:
                QMessageBox.warning(self, "Save log", str(e))

    def _reset_sync_transfer_panel(self) -> None:
        self._sync_progress_timer.stop()
        self._stop_sync_session_wall_clock(reset_label=True)
        self._btn_pause.setText("Pause")
        self._btn_pause.setEnabled(False)
        self._pending_sync_snap = None
        self._sync_attempt_shown = 1
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")
        self._sync_bar_peak = 0
        self._lbl_sync_detail.setText(
            "Idle — no transfer running.\n\n"
            "Details below update during sync (rsync elapsed, bytes, counters). "
            "Progress and throughput are shown in the bar above."
        )

    def _sync_panel_starting(self) -> None:
        self._sync_progress_timer.stop()
        self._pending_sync_snap = None
        self._progress.setRange(0, 10_000)
        self._progress.setValue(0)
        self._progress.setFormat("Starting rsync…")
        self._sync_attempt_shown = 1
        self._sync_bar_peak = 0
        self._lbl_sync_detail.setText(
            "Waiting for the first progress line (can take a few seconds over SSH)."
        )
        self._sync_session_active = True
        self._session_elapsed.start()
        self._sync_session_wall_timer.start()
        self._update_sync_session_elapsed_label()

    @staticmethod
    def _format_wall_elapsed_ms(ms: int) -> str:
        if ms < 0:
            ms = 0
        sec = ms // 1000
        h, r = divmod(sec, 3600)
        m, s = divmod(r, 60)
        if h < 100:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{h}:{m:02d}:{s:02d}"

    def _stop_sync_session_wall_clock(self, *, reset_label: bool = False) -> None:
        self._sync_session_wall_timer.stop()
        if self._sync_session_active:
            self._lbl_sync_elapsed.setText(
                "Session elapsed: "
                + self._format_wall_elapsed_ms(self._session_elapsed.elapsed())
            )
        self._sync_session_active = False
        if reset_label:
            self._lbl_sync_elapsed.setText("Session elapsed: —")

    @Slot()
    def _update_sync_session_elapsed_label(self) -> None:
        if not self._sync_session_active:
            return
        self._lbl_sync_elapsed.setText(
            "Session elapsed: "
            + self._format_wall_elapsed_ms(self._session_elapsed.elapsed())
        )

    def _sync_transfer_bar_units(self, snap: RsyncProgressSnapshot) -> int:
        """
        Map transfer state to 0..10000 for the bar (finer than integer percent on huge trees).

        When a **preflight source scan** total exists, progress follows **bytes sent / scanned
        size** (not rsync's internal % or ``xfr#``, which jump between files). Without a scan,
        falls back to rsync's overall percent. The UI applies a monotonic peak so the bar never
        moves backward between updates.
        """
        scan_b = self._last_scan[1]
        if (
            scan_b is not None
            and scan_b > 0
            and snap.transferred_bytes is not None
            and snap.transferred_bytes >= 0
        ):
            return min(10_000, int(10_000 * min(1.0, snap.transferred_bytes / scan_b)))
        if scan_b is not None and scan_b > 0:
            return min(10_000, max(0, int(10_000 * snap.percent / 100)))
        return min(10_000, max(0, snap.percent * 100))

    @Slot()
    def _flush_sync_transfer_ui(self) -> None:
        snap = self._pending_sync_snap
        if snap is None:
            return
        if self._progress.maximum() != 10_000:
            self._progress.setRange(0, 10_000)
        scan_b = self._last_scan[1]
        tb = snap.transferred_bytes

        left_b: Optional[int] = None
        if scan_b is not None and scan_b > 0:
            if tb is not None:
                left_b = max(0, scan_b - tb)
            else:
                left_b = max(0, int(scan_b * (100 - snap.percent) / 100))

        speed_bps = parse_rsync_speed_to_bytes_per_sec(snap.speed)
        use_scan_eta = (
            left_b is not None
            and speed_bps is not None
            and speed_bps > 1e-9
        )
        if use_scan_eta:
            eta_disp = format_seconds_as_hms_display(left_b / speed_bps)
        else:
            eta_disp = format_rsync_hms_for_display(snap.eta)

        rsync_eta_norm = format_rsync_hms_for_display(snap.eta)
        if snap.percent >= 99 and (
            (use_scan_eta and eta_disp == "00:00:00")
            or (not use_scan_eta and rsync_eta_norm == "00:00:00")
            or (left_b is not None and left_b == 0)
        ):
            eta_disp = "finishing…"

        raw_u = self._sync_transfer_bar_units(snap)
        self._sync_bar_peak = max(self._sync_bar_peak, raw_u)
        self._progress.setValue(self._sync_bar_peak)
        pct_bar = min(100.0, self._sync_bar_peak / 100.0)
        # Single U+0025 so Qt does not interpret "%%" as two visible percent signs on all styles.
        _pct = "\u0025"
        fmt_bits: List[str] = [f"{pct_bar:.2f}{_pct}"]
        if left_b is not None:
            fmt_bits.append(f"{human_bytes(left_b)} left")
        elif tb is not None:
            fmt_bits.append(f"{human_bytes(tb)} sent")
        elif snap.transferred_display:
            fmt_bits.append(f"{snap.transferred_display} sent")
        elif scan_b is not None and scan_b > 0:
            fmt_bits.append(f"of {human_bytes(scan_b)} scanned")
        fmt_bits.append(snap.speed)
        fmt_bits.append(f"ETA {eta_disp}")
        self._progress.setFormat(" · ".join(fmt_bits))
        parts = [f"Elapsed {format_rsync_hms_for_display(snap.elapsed)}"]
        if snap.transferred_bytes is not None:
            parts.append(human_bytes(snap.transferred_bytes))
        if snap.stats_human:
            parts.append(snap.stats_human)
        parts.append(f"Attempt {self._sync_attempt_shown}")
        self._lbl_sync_detail.setText(" · ".join(parts))

    @Slot(object)
    def _on_rsync_progress(self, snap: object) -> None:
        if not isinstance(snap, RsyncProgressSnapshot):
            return
        self._pending_sync_snap = snap
        self._sync_progress_timer.start(80)

    def _append_log(self, line: str) -> None:
        sb = self._log.verticalScrollBar()
        for segment in line.splitlines():
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._log.appendPlainText(f"[{ts}] {segment}")
        sb.setValue(sb.maximum())

    @Slot()
    def _browse_source(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select source directory", self._source.text())
        if d:
            self._source.setText(d if d.endswith("/") else d + "/")

    @Slot()
    def _browse_destination(self) -> None:
        start = self._dest.text().strip()
        remote, _ = parse_rsync_destination(start)
        if remote is not None:
            start = str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Select destination directory", start)
        if d:
            self._dest.setText(d if d.endswith("/") else d + "/")

    @Slot()
    def _update_ssh_password_visibility(self) -> None:
        self._ssh_pw_src_wrap.setVisible(self._parsed_source()[0] is not None)
        self._ssh_pw_wrap.setVisible(self._parsed_destination()[0] is not None)

    @Slot(str)
    def _on_scan_phase(self, text: str) -> None:
        self._scan_bar.setToolTip(text)

    @Slot(object, object)
    def _on_scan_progress(self, n: object, total_b: object) -> None:
        try:
            self._scan_pending_n = int(n)
            self._scan_pending_b = int(total_b)
        except (TypeError, ValueError):
            return
        # Coalesce: many queued signals from the worker thread would freeze the GUI.
        self._scan_ui_timer.start(50)

    @Slot()
    def _flush_scan_progress_ui(self) -> None:
        n = self._scan_pending_n
        tb = self._scan_pending_b
        self._lbl_files.setText(f"{n:,}")
        self._lbl_src_size.setText(human_bytes(tb))

    @Slot()
    def _stop_scan(self) -> None:
        if self._scan_worker_ref is not None:
            self._scan_worker_ref.request_cancel()
            self._btn_stop_scan.setEnabled(False)

    @Slot(object, object, object)
    def _on_source_scan_finished(
        self, count: object, size_b: object, user_stopped: object
    ) -> None:
        self._scan_ui_timer.stop()
        self._flush_scan_progress_ui()
        worker = self._scan_worker_ref
        self._scan_worker_ref = None
        self._disconnect_scan_worker(worker)
        cancelled = bool(user_stopped)
        c = count if isinstance(count, int) or count is None else None
        s = size_b if isinstance(size_b, int) or size_b is None else None
        self._last_scan = (c, s)  # type: ignore[assignment]
        self._lbl_files.setText(str(c) if c is not None else "—")
        self._lbl_src_size.setText(human_bytes(s) if s is not None else "—")
        if cancelled:
            self._append_log(
                f"Scan stopped (partial): {c if c is not None else '?'} files, "
                f"{human_bytes(s) if s is not None else '?'}"
            )
        else:
            self._append_log(
                f"Scan done: {c if c is not None else '?'} files, "
                f"{human_bytes(s) if s is not None else '?'}"
            )
        self._set_scan_source_interaction_locked(False)
        self._lbl_scan_idle.setVisible(True)
        self._scan_bar.setVisible(False)
        self._scan_bar.setToolTip("")
        t = self._scan_thread
        if t is not None:
            t.quit()

    @Slot(str)
    def _on_source_scan_failed(self, msg: str) -> None:
        self._scan_ui_timer.stop()
        self._flush_scan_progress_ui()
        worker = self._scan_worker_ref
        self._scan_worker_ref = None
        self._disconnect_scan_worker(worker)
        QMessageBox.warning(self, "Scan", msg)
        self._append_log(f"Scan failed: {msg}")
        self._lbl_files.setText("—")
        self._lbl_src_size.setText("—")
        self._set_scan_source_interaction_locked(False)
        self._lbl_scan_idle.setVisible(True)
        self._scan_bar.setVisible(False)
        self._scan_bar.setToolTip("")
        t = self._scan_thread
        if t is not None:
            t.quit()

    def _parsed_source(self) -> Tuple[Optional[RemoteTarget], str]:
        return parse_rsync_destination(self._source.text().strip())

    def _parsed_destination(self) -> Tuple[Optional[RemoteTarget], str]:
        return parse_rsync_destination(self._dest.text().strip())

    def _remote_for_source(self) -> Optional[RemoteTarget]:
        return self._parsed_source()[0]

    def _remote_for_dest(self) -> Optional[RemoteTarget]:
        return self._parsed_destination()[0]

    def _ssh_source_is_remote(self) -> bool:
        return self._parsed_source()[0] is not None

    def _ssh_destination_is_remote(self) -> bool:
        return self._parsed_destination()[0] is not None

    def _ssh_either_remote(self) -> bool:
        return self._ssh_source_is_remote() or self._ssh_destination_is_remote()

    def _ssh_batch_mode(self) -> bool:
        # Rsync -e: allow password / kbd-interactive when either side is remote.
        return not self._ssh_either_remote()

    def _ssh_password_dest_plain(self) -> str:
        return self._ssh_password.text().strip()

    def _ssh_password_src_plain(self) -> str:
        return self._ssh_password_src.text().strip()

    def _dest_sshpass_password(self) -> Optional[str]:
        if not self._ssh_destination_is_remote():
            return None
        pw = self._ssh_password_dest_plain()
        if pw and shutil.which("sshpass"):
            return pw
        return None

    def _source_sshpass_password(self) -> Optional[str]:
        if not self._ssh_source_is_remote():
            return None
        pw = self._ssh_password_src_plain()
        if pw and shutil.which("sshpass"):
            return pw
        return None

    def _rsync_sshpass_password(self) -> Optional[str]:
        """
        Password passed to sshpass for rsync's single -e transport.

        If both source and destination are remote, destination field is preferred when set,
        otherwise source (same SSHPASS for both hops — use keys if passwords differ).
        """
        if not shutil.which("sshpass"):
            return None
        dp = self._dest_sshpass_password()
        sp = self._source_sshpass_password()
        if self._ssh_destination_is_remote() and self._ssh_source_is_remote():
            return dp or sp
        if self._ssh_destination_is_remote():
            return dp
        if self._ssh_source_is_remote():
            return sp
        return None

    def _ssh_extra_env(self) -> Optional[Dict[str, str]]:
        if not self._ssh_destination_is_remote():
            return None
        if self._dest_sshpass_password():
            return None
        w = ensure_ssh_askpass_wrapper()
        return {
            "SSH_ASKPASS": str(w),
            "SSH_ASKPASS_REQUIRE": "force",
        }

    def _source_ssh_extra_env(self) -> Optional[Dict[str, str]]:
        if not self._ssh_source_is_remote():
            return None
        if self._source_sshpass_password():
            return None
        w = ensure_ssh_askpass_wrapper()
        return {
            "SSH_ASKPASS": str(w),
            "SSH_ASKPASS_REQUIRE": "force",
        }

    def _ssh_qprocess_env(self) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        secret = self._rsync_sshpass_password()
        src_r = self._ssh_source_is_remote()
        dst_r = self._ssh_destination_is_remote()
        if (src_r or dst_r) and secret:
            env.insert("SSHPASS", secret)
            env.remove("SSH_ASKPASS")
            env.remove("SSH_ASKPASS_REQUIRE")
        elif src_r or dst_r:
            w = ensure_ssh_askpass_wrapper()
            env.insert("SSH_ASKPASS", str(w))
            env.insert("SSH_ASKPASS_REQUIRE", "force")
            env.remove("SSHPASS")
        else:
            env.remove("SSH_ASKPASS")
            env.remove("SSH_ASKPASS_REQUIRE")
            env.remove("SSHPASS")
        return env

    @Slot()
    def _test_ssh(self) -> None:
        dest_remote = self._remote_for_dest()
        src_remote = self._remote_for_source()
        if dest_remote is not None:
            remote = dest_remote
            role = "destination"
            pw = self._ssh_password_dest_plain()
            extra_env = self._ssh_extra_env()
            pw_sshpass = self._dest_sshpass_password()
        elif src_remote is not None:
            remote = src_remote
            role = "source"
            pw = self._ssh_password_src_plain()
            extra_env = self._source_ssh_extra_env()
            pw_sshpass = self._source_sshpass_password()
        else:
            QMessageBox.information(
                self,
                "SSH test",
                "SSH applies when the source or destination is user@host:/path.",
            )
            return
        self._append_log(f"SSH: testing {remote.ssh_spec()} ({role}) …")
        if pw and not shutil.which("sshpass"):
            self._append_log(
                "Note: sshpass not found — password field ignored; using GUI prompts. "
                "Install: sudo pacman -S sshpass (Arch/CachyOS)."
            )
        if (
            pw
            and not shutil.which("sshpass")
            and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        ):
            QMessageBox.warning(
                self,
                "SSH password prompt",
                "Password is set but sshpass is missing and no DISPLAY/WAYLAND_DISPLAY — "
                "the GUI password dialog may not appear.",
            )
        try:
            proc = run_ssh_command(
                remote,
                "echo ok",
                connect_timeout=12,
                batch_mode=self._ssh_batch_mode(),
                extra_env=extra_env,
                password_for_sshpass=pw_sshpass,
            )
            out = (proc.stdout or "").strip()
            if proc.returncode == 0 and "ok" in out:
                QMessageBox.information(
                    self,
                    "SSH test",
                    "Connection OK (password / GUI or keys as configured).",
                )
                self._append_log("SSH: OK")
                self._ssh_ok_this_session = True
                self._sync_guide_pulse()
            else:
                err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
                hint = ""
                el = err.lower()
                if "please try again" in el:
                    hint = (
                        "\n\nThe server is asking for a password; “please try again” "
                        "usually means the password was wrong or the account cannot log in. "
                        "Verify with a terminal: ssh USER@HOST (same user as in the destination)."
                    )
                elif "permission denied" in el and "publickey" in el:
                    hint = (
                        "\n\nThis often means wrong password, missing key, or sshd only allows keys. "
                        "Try: ssh USER@HOST in a terminal."
                    )
                QMessageBox.warning(self, "SSH test", f"Failed:\n{err}{hint}")
                self._append_log(f"SSH: failed — {err}")
                self._ssh_ok_this_session = False
                self._sync_guide_pulse()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "SSH test", str(e))
            self._append_log(f"SSH: error — {e}")
            self._ssh_ok_this_session = False
            self._sync_guide_pulse()

    @Slot()
    def _check_dest_space(self) -> None:
        dest = self._dest.text().strip()
        remote, rpath = self._parsed_destination()
        if remote is None:
            free = local_free_bytes(dest)
            self._dest_free_bytes = free
            self._lbl_dest_free.setText(human_bytes(free) if free is not None else "— (unreadable)")
            self._append_log(f"Local destination free: {human_bytes(free)}")
            self._sync_guide_pulse()
            return
        self._append_log(f"Querying free space on {remote.ssh_spec()}:{rpath} …")
        try:
            free = remote_df_free_bytes(
                remote,
                rpath,
                batch_mode=self._ssh_batch_mode(),
                extra_env=self._ssh_extra_env(),
                password_for_sshpass=self._dest_sshpass_password(),
                connect_timeout=min(max(10, self._timeout.value()), 120),
            )
            self._dest_free_bytes = free
            self._lbl_dest_free.setText(human_bytes(free) if free is not None else "—")
            if free is None:
                QMessageBox.warning(
                    self,
                    "Space check",
                    "Could not read remote free space. Verify SSH and the path.",
                )
            else:
                self._append_log(f"Remote free (df): {human_bytes(free)}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Space check", str(e))
            self._append_log(f"Space check error: {e}")
        self._sync_guide_pulse()

    @Slot()
    def _scan_source(self) -> None:
        path = self._source.text().strip()
        if self._parsed_source()[0] is not None:
            QMessageBox.information(
                self,
                "Scan",
                "Source scan only supports local directories. "
                "Remote sources (user@host:/path) are not walked from this app.",
            )
            return
        p = Path(path).expanduser()
        if not p.is_dir():
            QMessageBox.warning(self, "Scan", "Source must be an existing local directory.")
            return
        if self._scan_thread and self._scan_thread.isRunning():
            QMessageBox.information(self, "Scan", "A scan is already running.")
            return

        self._scan_pending_n = 0
        self._scan_pending_b = 0
        self._set_scan_source_interaction_locked(True)
        self._sync_guide_pulse()
        self._append_log(f"Scanning source (may take a while): {path}")
        self._lbl_files.setText("—")
        self._lbl_src_size.setText("…")
        self._lbl_scan_idle.setVisible(False)
        self._scan_bar.setVisible(True)
        self._scan_bar.setToolTip("")

        thread = QThread(self)
        worker = SourceScanWorker()
        worker.moveToThread(thread)

        self._scan_worker_ref = worker
        worker.prepare_source(path)
        thread.started.connect(worker.run)
        worker.phase.connect(self._on_scan_phase, Qt.QueuedConnection)
        worker.scan_progress.connect(self._on_scan_progress, Qt.QueuedConnection)
        worker.finished.connect(self._on_source_scan_finished, Qt.QueuedConnection)
        worker.failed.connect(self._on_source_scan_failed, Qt.QueuedConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._scan_thread = thread
        thread.start()

    def _extra_rsync_args(self) -> List[str]:
        return self._collect_rsync_modifiers()

    def _set_path_and_rsync_controls_enabled(self, enabled: bool) -> None:
        """Paths, rsync options, and preflight actions — disabled for the duration of a sync."""
        for w in (
            self._source,
            self._dest,
            self._ssh_password_src,
            self._ssh_password,
            self._btn_browse,
            self._btn_browse_dest,
            self._timeout,
            self._retry,
            self._dry_run,
            self._recursive_subdirs,
            self._radio_resume_partial,
            self._radio_redo_partial,
            self._bwlimit,
            self._extra_rsync,
            self._btn_ssh,
            self._btn_space,
            self._btn_scan,
        ):
            w.setEnabled(enabled)
        if enabled:
            scan_running = (
                self._scan_thread is not None and self._scan_thread.isRunning()
            )
            self._btn_scan.setEnabled(not scan_running)
            self._btn_stop_scan.setEnabled(scan_running)

    def _set_scan_source_interaction_locked(self, locked: bool) -> None:
        """
        While a local source scan runs, disable paths, rsync options, preflight actions that could
        change the session, and **Start sync**. **Stop scan** stays enabled until cancelled or done.
        """
        fields = (
            self._source,
            self._dest,
            self._ssh_password_src,
            self._ssh_password,
            self._btn_browse,
            self._btn_browse_dest,
            self._timeout,
            self._retry,
            self._dry_run,
            self._recursive_subdirs,
            self._radio_resume_partial,
            self._radio_redo_partial,
            self._bwlimit,
            self._extra_rsync,
            self._btn_ssh,
            self._btn_space,
            self._btn_start,
        )
        if locked:
            for w in fields:
                w.setEnabled(False)
            self._btn_scan.setEnabled(False)
            self._btn_stop_scan.setEnabled(True)
            self._sync_guide_pulse()
            return
        syncing = self._rsync.is_syncing()
        for w in fields:
            w.setEnabled(not syncing)
        if syncing:
            self._btn_scan.setEnabled(False)
            self._btn_stop_scan.setEnabled(False)
        else:
            self._btn_scan.setEnabled(True)
            self._btn_stop_scan.setEnabled(False)
        self._sync_guide_pulse()

    def _get_guide_target(self) -> Optional[QPushButton]:
        if self._rsync.is_syncing():
            return None
        if self._scan_thread is not None and self._scan_thread.isRunning():
            return None
        src_remote, _ = self._parsed_source()
        dst_remote, _ = self._parsed_destination()
        src = self._source.text().strip()
        dst = self._dest.text().strip()
        # 1) Local source path must exist — Browse helps; remote source is typed (no pulse).
        if src_remote is None:
            if not src or not Path(src).expanduser().is_dir():
                return self._btn_browse
            if self._last_scan[1] is None:
                return self._btn_scan
        elif not src:
            return None
        # 2) Destination path
        if not dst:
            return self._btn_browse_dest
        # 3) Any remote endpoint — confirm SSH before space check / sync.
        if (src_remote is not None or dst_remote is not None) and not self._ssh_ok_this_session:
            return self._btn_ssh
        # 4) Remote destination: check free space once (local dest skips this guided step).
        if dst_remote is not None and self._dest_free_bytes is None:
            return self._btn_space
        return self._btn_start

    def _clear_guide_glow(self, w: Optional[QPushButton]) -> None:
        if not w:
            return
        w.setProperty("guidePulse", False)
        w.style().unpolish(w)
        w.style().polish(w)
        w.update()

    @Slot()
    def _sync_guide_pulse(self) -> None:
        busy = self._rsync.is_syncing() or (
            self._scan_thread is not None and self._scan_thread.isRunning()
        )
        if busy:
            self._guide_pulse_timer.stop()
            self._guide_glow_phase = 0
            self._clear_guide_glow(self._guide_target)
            self._guide_target = None
            return
        if not self._guide_pulse_timer.isActive():
            self._guide_glow_phase = 0
            self._guide_pulse_timer.start()
        self._pulse_guide()

    @Slot()
    def _pulse_guide(self) -> None:
        target = self._get_guide_target()
        if target != self._guide_target:
            self._clear_guide_glow(self._guide_target)
            self._guide_target = target
        if not target or not target.isEnabled():
            self._guide_pulse_timer.stop()
            self._clear_guide_glow(self._guide_target)
            self._guide_target = None
            return
        self._guide_glow_phase = 1 - self._guide_glow_phase
        target.setProperty("guidePulse", bool(self._guide_glow_phase))
        target.style().unpolish(target)
        target.style().polish(target)
        target.update()

    def _preflight_warnings(self) -> bool:
        """Return True if user accepts or no blocking issue."""
        if self._parsed_source()[0] is None:
            src = Path(self._source.text().strip()).expanduser()
            if not src.is_dir():
                QMessageBox.warning(self, "Sync", "Source directory does not exist.")
                return False

        if self._dry_run.isChecked():
            return True

        size_b = self._last_scan[1]
        free_b = self._dest_free_bytes
        if size_b is not None and free_b is not None and free_b < size_b:
            r = QMessageBox.question(
                self,
                "Low space",
                f"Destination free ({human_bytes(free_b)}) is less than scanned source size "
                f"({human_bytes(size_b)}). Incremental updates may still fit, but a full copy "
                f"might not.\n\nStart anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            return r == QMessageBox.Yes

        if free_b is None and self._parsed_destination()[0] is not None:
            r = QMessageBox.question(
                self,
                "Space unknown",
                "Remote free space has not been checked this session. Run “Check destination space” "
                "for a safer estimate.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            return r == QMessageBox.Yes

        return True

    @Slot()
    def _start_sync(self) -> None:
        try:
            self._parsed_user_extra_args()
        except ValueError as e:
            QMessageBox.warning(self, "Extra rsync args", str(e))
            return
        if not self._preflight_warnings():
            return
        if not self._dry_run.isChecked():
            dest = self._dest.text().strip()
            r = QMessageBox.question(
                self,
                "Start sync",
                f"Start copying to:\n{dest}\n\nThe destination will be modified (not a dry run).",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return
        if self._rsync.is_syncing():
            QMessageBox.information(self, "Sync", "A sync is already running.")
            return

        self._last_sync_was_dry_run = self._dry_run.isChecked()
        self._btn_start.setEnabled(False)
        self._btn_pause.setText("Pause")
        self._btn_pause.setEnabled(True)
        self._btn_stop.setEnabled(True)
        self._set_path_and_rsync_controls_enabled(False)
        self._sync_guide_pulse()
        self._sync_panel_starting()

        try:
            self._rsync.set_process_environment(self._ssh_qprocess_env())
            self._rsync.configure(
                self._source.text().strip(),
                self._dest.text().strip(),
                self._timeout.value(),
                self._retry.value(),
                self._extra_rsync_args(),
                recursive=self._recursive_subdirs.isChecked(),
            )
            self._rsync.start_sync_loop()
        except OSError as e:
            self._set_path_and_rsync_controls_enabled(True)
            self._btn_start.setEnabled(True)
            self._btn_pause.setText("Pause")
            self._btn_pause.setEnabled(False)
            self._btn_stop.setEnabled(False)
            self._reset_sync_transfer_panel()
            self._sync_guide_pulse()
            QMessageBox.critical(self, "Sync", str(e))
            self._append_log(f"Sync setup error: {e}")

    @Slot()
    def _stop_sync(self) -> None:
        self._rsync.stop()

    @Slot()
    def _toggle_sync_pause(self) -> None:
        if self._btn_pause.text() == "Pause":
            if self._rsync.pause_transfer():
                self._btn_pause.setText("Resume")
        else:
            if self._rsync.resume_transfer():
                self._btn_pause.setText("Pause")

    @Slot(int)
    def _on_attempt(self, n: int) -> None:
        self._sync_attempt_shown = n
        self._append_log(f"--- Attempt {n} ---")
        self._btn_pause.setText("Pause")

    @Slot(bool)
    def _on_rsync_pause_state_changed(self, paused: bool) -> None:
        self._btn_pause.setText("Resume" if paused else "Pause")

    @Slot(int, bool)
    def _on_sync_finished(self, code: int, ok: bool) -> None:
        was_dry = self._last_sync_was_dry_run
        self._last_sync_was_dry_run = False
        self._btn_start.setEnabled(True)
        self._btn_pause.setText("Pause")
        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)
        self._set_path_and_rsync_controls_enabled(True)
        self._sync_guide_pulse()
        if ok:
            self._sync_progress_timer.stop()
            self._stop_sync_session_wall_clock(reset_label=False)
            self._pending_sync_snap = None
            self._progress.setRange(0, 10_000)
            self._progress.setValue(10_000)
            self._progress.setFormat("100.00\u0025 · complete")
            self._lbl_sync_detail.setText(
                "Dry run finished." if was_dry else "Transfer finished successfully."
            )
            if was_dry:
                tail = "\n".join(self._log.toPlainText().splitlines()[-40:])
                QMessageBox.information(
                    self,
                    "Dry run finished",
                    f"Exit code: {code}\n\nLast log lines:\n{tail}",
                )
            else:
                QMessageBox.information(self, "Sync", "Backup completed successfully.")
        else:
            self._sync_progress_timer.stop()
            self._stop_sync_session_wall_clock(reset_label=False)
            self._pending_sync_snap = None
            self._progress.setRange(0, 100)
            self._progress.setValue(0)
            self._progress.setFormat("%p%")
            self._sync_bar_peak = 0
            self._lbl_sync_detail.setText(
                f"Stopped or failed (exit code {code}). "
                "Check the log for details. The worker may retry until you press Stop."
            )
            if code != 0:
                QMessageBox.warning(self, "Sync", f"Sync did not complete successfully (code {code}).")

    @Slot()
    def _on_stopped(self) -> None:
        self._last_sync_was_dry_run = False
        self._btn_start.setEnabled(True)
        self._btn_pause.setText("Pause")
        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)
        self._set_path_and_rsync_controls_enabled(True)
        self._sync_guide_pulse()
        self._sync_progress_timer.stop()
        self._stop_sync_session_wall_clock(reset_label=False)
        self._pending_sync_snap = None
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")
        self._sync_bar_peak = 0
        self._lbl_sync_detail.setText("Stopped by user.")

    @Slot()
    def _check_for_update(self) -> None:
        latest = fetch_latest_github_version()
        if latest is None:
            QMessageBox.information(
                self,
                "Check for update",
                "Could not contact GitHub or read the latest release. "
                "Check your network connection and try again.",
            )
            return
        cur = __version__
        if not is_remote_version_newer(cur, latest):
            QMessageBox.information(
                self,
                "Check for update",
                f"You are running SafeCopi {cur}.\n\n"
                f"The latest GitHub release is {latest}.\n\n"
                "No newer version is available.",
            )
            return
        QMessageBox.information(
            self,
            "Update available",
            f"You are running SafeCopi {cur}.\n\n"
            f"A newer GitHub release is available: {latest}.\n\n"
            "Visit the project page to download the latest version:\n"
            "https://github.com/UnDadFeated/SafeCopi",
        )


def run_app() -> int:
    app = QApplication([])
    app.setApplicationName("SafeCopi")
    w = MainWindow()
    w.show()
    return app.exec()
