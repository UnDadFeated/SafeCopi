"""Main SafeCopi window: resilient rsync with destination checks and live progress."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QElapsedTimer,
    QMetaObject,
    QProcess,
    QProcessEnvironment,
    QSettings,
    QSize,
    QThread,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtGui import (
    QBrush,
    QCloseEvent,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from safecopi import __version__
from safecopi.debug_log import debug_log, init_debug_log, register_shutdown_debug_hooks
from safecopi.utils import (
    RemoteTarget,
    RsyncProgressSnapshot,
    EXISTING_FILES_MODE_CHOICES,
    EXISTING_FILES_MODE_DEFAULT,
    SSH_DF_SUBPROCESS_OVERHEAD_SEC,
    SSH_TEST_CONNECT_TIMEOUT_SEC,
    build_remote_df_shell_command,
    build_rsync_command_argv,
    build_ssh_command_argv,
    canonical_rsync_path,
    existing_files_mode_rsync_argv,
    normalize_existing_files_mode,
    remote_rsync_uri_strip_trailing_slashes,
    ensure_ssh_askpass_wrapper,
    format_rsync_hms_for_display,
    format_seconds_as_hms_display,
    human_bytes,
    is_remote_version_newer,
    clamp_monotonic_data_left_bytes,
    estimate_rsync_total_bytes_from_progress,
    parse_extra_rsync_args,
    parse_rsync_eta_token_to_seconds,
    parse_rsync_queue_remaining_total,
    parse_rsync_speed_to_bytes_per_sec,
    parse_remote_df_stdout,
    parse_rsync_destination,
    rsync_ssh_e_shell,
    run_ssh_command,
    ssh_command_environment,
)
from safecopi.workers import DestSpaceWorker, GitHubUpdateCheckWorker, RsyncWorker


_LOG_LINE_MAX_CHARS: int = 12_000
# Cap source folders to avoid accidental huge lists and argv explosion.
_MAX_SOURCE_FOLDERS: int = 64
# Source field: list (left) + Add/Remove column (right); fixed height matches the list cap.
# ~3–4 visible lines; side column must fit three stacked buttons without vertical squeeze.
_PATHS_SOURCE_FIELD_TOTAL_H: int = 92
# Paths box: source + destination side columns share one button size (Browse matches Add/Edit/Remove).
_PATHS_SIDE_BTN_MIN_H: int = 26
# Per-item remote SSH password (runtime only; not persisted in QSettings).
_SOURCE_ITEM_PW_ROLE = int(Qt.ItemDataRole.UserRole) + 1
# QListWidgetItem: last SSH hint state for icon (runtime only).
_SOURCE_ITEM_SSH_HINT_ROLE = int(Qt.ItemDataRole.UserRole) + 2

_SSH_HINT_ICON_PX: int = 18
# ItemDataRole values for _SOURCE_ITEM_SSH_HINT_ROLE (debug / consistency).
_SSH_HINT_LOCAL: int = 0
_SSH_HINT_NONE_PW: int = 1
_SSH_HINT_PENDING: int = 2
_SSH_HINT_OK: int = 3
_SSH_HINT_FAIL: int = 4

_ssh_hint_icon_cache: Dict[str, QIcon] = {}


def _ssh_hint_icon_blank() -> QIcon:
    k = "blank"
    if k not in _ssh_hint_icon_cache:
        pm = QPixmap(_SSH_HINT_ICON_PX, _SSH_HINT_ICON_PX)
        pm.fill(Qt.GlobalColor.transparent)
        _ssh_hint_icon_cache[k] = QIcon(pm)
    return _ssh_hint_icon_cache[k]


def _ssh_hint_icon_no_password() -> QIcon:
    """Remote row with no password set (keys / SSH_ASKPASS only)."""
    k = "no_pw"
    if k not in _ssh_hint_icon_cache:
        pm = QPixmap(_SSH_HINT_ICON_PX, _SSH_HINT_ICON_PX)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#cdd6f4"))
        pen.setWidthF(1.75)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        m, d = 3, _SSH_HINT_ICON_PX
        p.drawEllipse(m, m, d - 2 * m, d - 2 * m)
        p.end()
        _ssh_hint_icon_cache[k] = QIcon(pm)
    return _ssh_hint_icon_cache[k]


def _ssh_hint_icon_pending() -> QIcon:
    """Remote row with password set, before / between Test SSH runs."""
    k = "pending"
    if k not in _ssh_hint_icon_cache:
        pm = QPixmap(_SSH_HINT_ICON_PX, _SSH_HINT_ICON_PX)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#6c7086")))
        d = _SSH_HINT_ICON_PX
        p.drawEllipse(d // 2 - 3, d // 2 - 3, 7, 7)
        p.end()
        _ssh_hint_icon_cache[k] = QIcon(pm)
    return _ssh_hint_icon_cache[k]


def _ssh_hint_icon_ok() -> QIcon:
    k = "ok"
    if k not in _ssh_hint_icon_cache:
        pm = QPixmap(_SSH_HINT_ICON_PX, _SSH_HINT_ICON_PX)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#a6e3a1"))
        pen.setWidthF(2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        d = _SSH_HINT_ICON_PX
        p.drawLine(4, d // 2 + 1, 8, d - 5)
        p.drawLine(8, d - 5, d - 4, 5)
        p.end()
        _ssh_hint_icon_cache[k] = QIcon(pm)
    return _ssh_hint_icon_cache[k]


def _ssh_hint_icon_fail() -> QIcon:
    k = "fail"
    if k not in _ssh_hint_icon_cache:
        pm = QPixmap(_SSH_HINT_ICON_PX, _SSH_HINT_ICON_PX)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#f38ba8"))
        pen.setWidthF(2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        d = _SSH_HINT_ICON_PX
        m = 5
        p.drawLine(m, m, d - m, d - m)
        p.drawLine(d - m, m, m, d - m)
        p.end()
        _ssh_hint_icon_cache[k] = QIcon(pm)
    return _ssh_hint_icon_cache[k]


# File transfer panel: match Session elapsed (monospace) for all stats and path text.
_TRANSFER_INFO_MONO = (
    "color: #a6adc8; font-size: 11px; font-family: monospace, ui-monospace, monospace;"
)
_TRANSFER_INFO_HDR = (
    "color: #6c7086; font-size: 10px; font-family: monospace, ui-monospace, monospace; "
    "font-weight: 600; letter-spacing: 0.05em;"
)
_TRANSFER_INFO_SUB = (
    "color: #6c7086; font-size: 10px; font-family: monospace, ui-monospace, monospace;"
)

# Upper bound for inferred “data remaining” / implied job totals (avoids absurd UI from bad ratios).
_MAX_INFERRED_TRANSFER_BYTES: int = 256 * 1024**4  # 256 TiB


def _qprocess_environment_from_environ_dict(env: Dict[str, str]) -> QProcessEnvironment:
    """Build a Qt process environment from a full merged OS-style mapping."""
    qe = QProcessEnvironment()
    for k, v in env.items():
        qe.insert(k, v)
    return qe


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"SafeCopi — resilient sync ({__version__})")
        self.setFixedSize(720, 900)

        self._settings_loading = False
        self._last_sync_was_dry_run = False
        self._dest_free_bytes: Optional[int] = None
        self._sync_waiting_for_dest_space: bool = False
        self._space_thread: Optional[QThread] = None
        self._space_process: Optional[QProcess] = None
        self._space_timed_out: bool = False
        self._space_connect_timeout_sec: int = 60
        self._space_timeout_timer = QTimer(self)
        self._space_timeout_timer.setSingleShot(True)
        self._space_timeout_timer.timeout.connect(self._on_space_process_timeout)
        self._update_check_thread: Optional[QThread] = None
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
        self._awaiting_first_rsync_progress = False
        self._sync_dot_phase = 0
        self._pending_sync_launch = False
        self._sync_session_wall_timer = QTimer(self)
        self._sync_session_wall_timer.setInterval(500)
        self._sync_session_wall_timer.timeout.connect(self._update_sync_session_elapsed_label)
        self._pending_sync_snap: Optional[RsyncProgressSnapshot] = None
        self._sync_attempt_shown = 1
        self._sync_bar_peak: int = 0
        self._sync_rsync_source_step: int = 0
        self._sync_total_source_runs: int = 1
        self._sync_left_monotone_tb: Optional[int] = None
        self._sync_left_monotone_display: Optional[int] = None
        self._sync_carry_tb: Optional[int] = None
        self._rsync = RsyncWorker(self)

        self._source_list = QListWidget()
        self._source_list.setObjectName("SourceList")
        self._source_list.setMinimumHeight(40)
        self._source_list.setMaximumHeight(62)
        self._source_list.setIconSize(QSize(_SSH_HINT_ICON_PX, _SSH_HINT_ICON_PX))
        self._source_list.setToolTip(
            "One or more sources: local folders and/or remote user@host:/path (or sftp://…). "
            "Use Add source… for each entry, or Edit… on a selected row; optional password per "
            "remote row is not saved (paths are saved in settings).\n\n"
            "Icon (remote rows): hollow circle — no password; grey dot — password set, not tested yet; "
            "green check — last dialog SSH test succeeded; red X — last test failed. Local folders "
            "have no icon.\n\n"
            "With several sources, each is copied into the destination under its own top-level "
            "name (e.g. …/Macie Backup/ → dest/Macie Backup/). Remote trailing slashes are "
            "normalized for multi-source so folders are not merged into the destination root.\n\n"
            "Single source only: a trailing slash still means “contents of this directory” "
            "(standard rsync)."
        )
        self._btn_add_source = QPushButton("Add source…")
        self._btn_edit_source = QPushButton("Edit…")
        self._btn_remove_source = QPushButton("Remove")
        self._btn_add_source.setToolTip(
            "Add a local folder or a remote path (with optional password for that host)."
        )
        self._btn_edit_source.setToolTip(
            "Change the selected path or password without removing the row."
        )
        self._btn_remove_source.setToolTip(
            "Remove the selected source from the list."
        )
        self._btn_add_source.clicked.connect(self._prompt_add_source)
        self._btn_edit_source.clicked.connect(self._prompt_edit_source)
        self._btn_remove_source.clicked.connect(self._remove_source_folder)

        self._dest = QLineEdit("htpc@192.168.4.112:/mnt/media_hdd/Backup/Archive/")
        self._dest.setObjectName("PathLineEdit")
        self._dest.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._dest.setMinimumHeight(22)
        self._dest.setClearButtonEnabled(True)
        self._dest.setPlaceholderText(
            "e.g. /mnt/backups/Archive/ or user@host:/mnt/backup/ (paste sftp://… from Dolphin)"
        )
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

        self._ssh_password = QLineEdit()
        self._ssh_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._ssh_password.setPlaceholderText("Optional (remote dest)")
        self._ssh_password.setToolTip(
            "When the destination is user@host:/path, optional password for sshpass or SSH_ASKPASS. "
            "Not saved. Empty if you use SSH keys. If both ends are remote, sshpass uses this field "
            "first when set; otherwise the source password."
        )

        self._btn_edit_dest = QPushButton("Browse…")
        self._btn_edit_dest.setToolTip(
            "Open a dialog to set a local or remote destination (with optional SSH password "
            "and Test SSH), just like Add/Edit source."
        )
        self._btn_edit_dest.clicked.connect(self._prompt_edit_dest)

        self._btn_test_ssh_dest = QPushButton("Test SSH")
        self._btn_test_ssh_dest.setToolTip(
            "Verify SSH to the destination host when it is user@host:/path (same as sync: "
            "keys, sshpass, or SSH_ASKPASS). Uses the destination field and Dest. password."
        )
        self._btn_test_ssh_dest.clicked.connect(self._test_ssh_destination)
        self._btn_test_ssh_dest.setVisible(False)

        self._lbl_ssh_pw = QLabel("Dest. password")
        self._lbl_ssh_pw.setStyleSheet("color: #bac2de;")
        self._lbl_ssh_pw.setToolTip(
            "Password for the remote destination host when the destination is user@host:/path."
        )
        self._ssh_pw_wrap = QWidget()
        _spw = QHBoxLayout(self._ssh_pw_wrap)
        _spw.setContentsMargins(10, 0, 0, 0)
        _spw.setSpacing(6)
        _spw.addWidget(self._lbl_ssh_pw, 0, Qt.AlignmentFlag.AlignVCenter)
        _spw.addWidget(self._ssh_password, 0, Qt.AlignmentFlag.AlignVCenter)
        self._ssh_password.setMinimumWidth(120)
        self._ssh_password.setMaximumWidth(180)
        self._ssh_password.setMinimumHeight(22)
        self._ssh_pw_wrap.setVisible(False)
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

        transfer = QGroupBox("File transfer")
        transfer.setMinimumHeight(220)
        tlay = QVBoxLayout(transfer)
        tlay.setSpacing(4)
        tlay.setContentsMargins(5, 4, 5, 4)

        self._progress = QProgressBar()
        self._progress.setObjectName("SyncTransferBar")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%p%")
        self._progress.setMinimumHeight(26)

        _stat_hdr = QLabel("TRANSFER STATS")
        _stat_hdr.setStyleSheet(_TRANSFER_INFO_HDR)
        self._lbl_stat_size = QLabel("—")
        self._lbl_stat_size.setStyleSheet(_TRANSFER_INFO_MONO)
        self._lbl_stat_size.setToolTip(
            "Estimated total transfer size from rsync bytes and percent (refines as the run progresses)."
        )
        self._lbl_stat_files = QLabel("—")
        self._lbl_stat_files.setStyleSheet(_TRANSFER_INFO_MONO)
        self._lbl_stat_files.setToolTip("Remaining items in rsync’s verify queue (to-chk), when reported.")
        self._lbl_stat_data_rem = QLabel("—")
        self._lbl_stat_data_rem.setStyleSheet(_TRANSFER_INFO_MONO)
        self._lbl_stat_speed = QLabel("—")
        self._lbl_stat_speed.setStyleSheet(_TRANSFER_INFO_MONO)
        self._lbl_stat_eta = QLabel("—")
        self._lbl_stat_eta.setStyleSheet(_TRANSFER_INFO_MONO)
        self._lbl_stat_dest_free = QLabel("—")
        self._lbl_stat_dest_free.setStyleSheet(_TRANSFER_INFO_MONO)
        self._lbl_stat_dest_free.setToolTip(
            "Free space on the destination (refreshed when you start a sync)."
        )

        self._stats_grid = QWidget()
        _sg = QGridLayout(self._stats_grid)
        _sg.setContentsMargins(0, 4, 0, 2)
        _sg.setHorizontalSpacing(20)
        _sg.setVerticalSpacing(4)
        _sg.setColumnStretch(0, 1)
        _sg.setColumnStretch(1, 1)
        _sg.setColumnStretch(2, 1)
        _lbl_sz = QLabel("Size (est.)")
        _lbl_sz.setStyleSheet(_TRANSFER_INFO_SUB)
        _lbl_fq = QLabel("File queue")
        _lbl_fq.setStyleSheet(_TRANSFER_INFO_SUB)
        _lbl_dr = QLabel("Data left")
        _lbl_dr.setStyleSheet(_TRANSFER_INFO_SUB)
        _lbl_sp = QLabel("Speed")
        _lbl_sp.setStyleSheet(_TRANSFER_INFO_SUB)
        _lbl_et = QLabel("ETA")
        _lbl_et.setStyleSheet(_TRANSFER_INFO_SUB)
        _lbl_df = QLabel("Dest. free")
        _lbl_df.setStyleSheet(_TRANSFER_INFO_SUB)
        _sg.addWidget(_stat_hdr, 0, 0, 1, 3)
        _sg.addWidget(_lbl_sz, 1, 0)
        _sg.addWidget(_lbl_fq, 1, 1)
        _sg.addWidget(_lbl_dr, 1, 2)
        _sg.addWidget(self._lbl_stat_size, 2, 0)
        _sg.addWidget(self._lbl_stat_files, 2, 1)
        _sg.addWidget(self._lbl_stat_data_rem, 2, 2)
        _sg.addWidget(_lbl_sp, 3, 0)
        _sg.addWidget(_lbl_et, 3, 1)
        _sg.addWidget(_lbl_df, 3, 2)
        _sg.addWidget(self._lbl_stat_speed, 4, 0)
        _sg.addWidget(self._lbl_stat_eta, 4, 1)
        _sg.addWidget(self._lbl_stat_dest_free, 4, 2)

        self._lbl_sync_elapsed = QLabel("Session elapsed: —")
        self._lbl_sync_elapsed.setStyleSheet(_TRANSFER_INFO_MONO)
        self._lbl_sync_elapsed.setToolTip(
            "Wall time since Start sync was pressed (this run). Shows “processing…” until the first "
            "rsync progress line arrives (large trees can take minutes over SSH while rsync prepares). "
            "With multiple sources, the current list entry appears after the time; the progress bar "
            "runs 0–100% for each source separately."
        )
        self._lbl_sync_elapsed.setContentsMargins(0, 0, 0, 0)

        self._lbl_sync_detail = QLabel()
        self._lbl_sync_detail.setWordWrap(False)
        self._lbl_sync_detail.setStyleSheet(_TRANSFER_INFO_HDR)
        self._lbl_sync_detail.setContentsMargins(0, 2, 0, 0)
        _detail_fm = QFontMetrics(self._lbl_sync_detail.font())
        self._lbl_sync_detail.setMinimumHeight(_detail_fm.lineSpacing() + 2)

        self._lbl_sync_path_header = QLabel("CURRENT PATH")
        self._lbl_sync_path_header.setStyleSheet(_TRANSFER_INFO_HDR)
        self._lbl_sync_path = QLabel()
        self._lbl_sync_path.setWordWrap(True)
        self._lbl_sync_path.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._lbl_sync_path.setStyleSheet(_TRANSFER_INFO_MONO)
        self._lbl_sync_path.setContentsMargins(0, 2, 0, 0)
        self._lbl_sync_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        _path_fm = QFontMetrics(self._lbl_sync_path.font())
        self._lbl_sync_path.setMinimumHeight(_path_fm.lineSpacing() * 5)

        self._sync_transfer_text_wrap = QWidget()
        _stw = QVBoxLayout(self._sync_transfer_text_wrap)
        _stw.setContentsMargins(0, 4, 0, 0)
        _stw.setSpacing(0)
        _stw.addWidget(self._lbl_sync_detail)
        _stw.addSpacing(10)
        _stw.addWidget(self._lbl_sync_path_header)
        _stw.addWidget(self._lbl_sync_path)

        tlay.addWidget(self._lbl_sync_elapsed)
        tlay.addWidget(self._progress)
        tlay.addWidget(self._stats_grid)
        tlay.addSpacing(8)
        tlay.addWidget(self._sync_transfer_text_wrap)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(8000)
        log_font = QFont("monospace")
        log_font.setStyleHint(QFont.Monospace)
        self._log.setFont(log_font)

        paths = QGroupBox("Paths")
        paths.setObjectName("PathsBox")
        fl = QFormLayout(paths)
        self._compact_form(fl)
        fl.setVerticalSpacing(10)
        fl.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        fl.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        _src_row = QHBoxLayout()
        _src_row.setSpacing(8)
        _src_row.setContentsMargins(0, 0, 0, 0)
        self._source_list.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._source_list.setFixedHeight(self._source_list.maximumHeight())
        _src_row.addWidget(self._source_list, 1)
        _src_btns_col = QVBoxLayout()
        _src_btns_col.setSpacing(7)
        _src_btns_col.setContentsMargins(0, 0, 0, 0)
        _src_btns_col.addWidget(self._btn_add_source, 0, Qt.AlignmentFlag.AlignTop)
        _src_btns_col.addWidget(self._btn_edit_source, 0, Qt.AlignmentFlag.AlignTop)
        _src_btns_col.addWidget(self._btn_remove_source, 0, Qt.AlignmentFlag.AlignTop)
        _src_btns_col.addStretch(1)
        _src_btns_wrap = QWidget()
        _src_btns_wrap.setLayout(_src_btns_col)
        _src_row.addWidget(
            _src_btns_wrap, 0, Qt.AlignmentFlag.AlignVCenter
        )
        _src_wrap = QWidget()
        _src_wrap.setLayout(_src_row)
        _src_wrap.setFixedHeight(_PATHS_SOURCE_FIELD_TOTAL_H)

        self._lbl_source_title = QLabel("Source(s)")
        self._lbl_source_title.setAlignment(Qt.AlignmentFlag.AlignRight)
        _src_label_col = QWidget()
        _src_label_col.setFixedHeight(_PATHS_SOURCE_FIELD_TOTAL_H)
        _slc = QVBoxLayout(_src_label_col)
        _slc.setContentsMargins(0, 0, 0, 0)
        _slc.setSpacing(0)
        _slc.addStretch(1)
        _slc.addWidget(self._lbl_source_title, 0, Qt.AlignmentFlag.AlignRight)
        _slc.addStretch(1)
        fl.addRow(_src_label_col, _src_wrap)

        row_dest_line = QHBoxLayout()
        row_dest_line.setSpacing(8)
        row_dest_line.setContentsMargins(0, 0, 0, 0)
        row_dest_line.addWidget(self._dest, 1, Qt.AlignmentFlag.AlignVCenter)
        row_dest_line.addWidget(self._ssh_pw_wrap, 0, Qt.AlignmentFlag.AlignVCenter)
        w_dest_left = QWidget()
        w_dest_left.setLayout(row_dest_line)
        _dest_btns_col = QVBoxLayout()
        _dest_btns_col.setSpacing(7)
        _dest_btns_col.setContentsMargins(0, 0, 0, 0)
        _dest_btns_col.addWidget(self._btn_edit_dest, 0, Qt.AlignmentFlag.AlignTop)
        _dest_btns_col.addWidget(self._btn_test_ssh_dest, 0, Qt.AlignmentFlag.AlignTop)
        _dest_btns_col.addStretch(1)
        _dest_btns_wrap = QWidget()
        _dest_btns_wrap.setLayout(_dest_btns_col)
        row_dest_outer = QHBoxLayout()
        row_dest_outer.setSpacing(8)
        row_dest_outer.setContentsMargins(0, 0, 0, 0)
        row_dest_outer.addWidget(w_dest_left, 1)
        row_dest_outer.addWidget(
            _dest_btns_wrap, 0, Qt.AlignmentFlag.AlignVCenter
        )
        w_dest = QWidget()
        w_dest.setLayout(row_dest_outer)
        w_dest.setFixedHeight(_PATHS_SOURCE_FIELD_TOTAL_H)

        self._lbl_dest_title = QLabel("Destination")
        self._lbl_dest_title.setAlignment(Qt.AlignmentFlag.AlignRight)
        _dest_label_col = QWidget()
        _dest_label_col.setFixedHeight(_PATHS_SOURCE_FIELD_TOTAL_H)
        _dlc = QVBoxLayout(_dest_label_col)
        _dlc.setContentsMargins(0, 0, 0, 0)
        _dlc.setSpacing(0)
        _dlc.addStretch(1)
        _dlc.addWidget(self._lbl_dest_title, 0, Qt.AlignmentFlag.AlignRight)
        _dlc.addStretch(1)
        fl.addRow(_dest_label_col, w_dest)

        self._apply_paths_side_button_metrics()

        self._combo_existing_files = QComboBox()
        for label, mode in EXISTING_FILES_MODE_CHOICES:
            self._combo_existing_files.addItem(label, mode)
        _ef_idx = self._combo_existing_files.findData(EXISTING_FILES_MODE_DEFAULT)
        self._combo_existing_files.setCurrentIndex(0 if _ef_idx < 0 else _ef_idx)
        self._combo_existing_files.setToolTip(
            "Overwrite: normal rsync (replace when the file differs). "
            "Skip (filename+size): --size-only. Skip (name only): --ignore-existing. "
            "Inserted before Extra args — avoid duplicating those flags there."
        )
        self._combo_existing_files.setMinimumWidth(240)
        self._combo_existing_files.setMaximumWidth(400)
        self._combo_existing_files.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        self._bwlimit = QSpinBox()
        self._bwlimit.setRange(0, 999_999)
        self._bwlimit.setValue(0)
        self._bwlimit.setSpecialValueText("off")
        self._bwlimit.setToolTip("rsync --bwlimit in KiB/s. 0 disables throttling.")
        self._bwlimit.setMaximumWidth(120)

        self._extra_rsync = QLineEdit()
        self._extra_rsync.setPlaceholderText('e.g. --delete --exclude=.git')
        self._extra_rsync.setToolTip(
            "Extra rsync flags, POSIX shell–split (quoted groups allowed). "
            "Appended after built-in options; use with care."
        )
        self._extra_rsync.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        opts = QGroupBox("Rsync")
        og = QGridLayout(opts)
        og.setContentsMargins(8, 8, 8, 8)
        og.setHorizontalSpacing(12)
        og.setVerticalSpacing(5)

        self._timeout.setMaximumWidth(110)
        self._retry.setMaximumWidth(110)
        delays_row = QWidget()
        dr = QHBoxLayout(delays_row)
        dr.setContentsMargins(0, 0, 0, 0)
        dr.setSpacing(6)
        dr.addWidget(QLabel("I/O timeout"))
        dr.addWidget(self._timeout)
        dr.addSpacing(14)
        dr.addWidget(QLabel("Retry delay"))
        dr.addWidget(self._retry)
        dr.addStretch(1)
        og.addWidget(delays_row, 0, 0, 1, 2)

        chk_row = QWidget()
        cr = QHBoxLayout(chk_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.setSpacing(18)
        cr.addWidget(self._dry_run)
        cr.addWidget(self._recursive_subdirs)
        cr.addStretch(1)
        og.addWidget(chk_row, 1, 0, 1, 2)

        partial_row = QWidget()
        pr = QHBoxLayout(partial_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(14)
        pr.addWidget(QLabel("Partial files"))
        pr.addWidget(self._radio_resume_partial)
        pr.addWidget(self._radio_redo_partial)
        pr.addStretch(1)
        og.addWidget(partial_row, 2, 0, 1, 2)

        existing_files_row = QWidget()
        _ef = QHBoxLayout(existing_files_row)
        _ef.setContentsMargins(0, 0, 0, 0)
        _ef.setSpacing(10)
        lbl_if = QLabel("If file exists")
        lbl_if.setStyleSheet("color: #bac2de;")
        _ef.addWidget(lbl_if, 0, Qt.AlignmentFlag.AlignVCenter)
        _ef.addWidget(self._combo_existing_files, 0, Qt.AlignmentFlag.AlignVCenter)
        _ef.addStretch(1)
        og.addWidget(existing_files_row, 3, 0, 1, 2)

        bw_extra_row = QWidget()
        ber = QHBoxLayout(bw_extra_row)
        ber.setContentsMargins(0, 0, 0, 0)
        ber.setSpacing(8)
        lbl_bw = QLabel("BW limit (KiB/s)")
        lbl_bw.setStyleSheet("color: #bac2de;")
        ber.addWidget(lbl_bw)
        ber.addWidget(self._bwlimit)
        ber.addSpacing(16)
        lbl_x = QLabel("Extra args")
        lbl_x.setStyleSheet("color: #bac2de;")
        ber.addWidget(lbl_x)
        ber.addWidget(self._extra_rsync, 1)
        og.addWidget(bw_extra_row, 4, 0, 1, 2)

        cmd_box = QGroupBox("Command preview")
        cbl = QVBoxLayout(cmd_box)
        cbl.setContentsMargins(5, 5, 5, 5)
        self._rsync_preview = QPlainTextEdit()
        self._rsync_preview.setReadOnly(True)
        self._rsync_preview.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
        )
        self._rsync_preview.setMinimumHeight(72)
        self._rsync_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._rsync_preview.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        pf = QFont("monospace")
        pf.setStyleHint(QFont.Monospace)
        self._rsync_preview.setFont(pf)
        cbl.addWidget(self._rsync_preview)

        actions = QHBoxLayout()
        actions.setSpacing(5)
        actions.setContentsMargins(0, 2, 0, 0)
        for b in (
            self._btn_start,
            self._btn_pause,
            self._btn_stop,
        ):
            b.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        actions.addStretch(1)
        actions.addWidget(self._btn_start, 0)
        actions.addWidget(self._btn_pause, 0)
        actions.addWidget(self._btn_stop, 0)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(3)
        root.addWidget(paths)
        root.addWidget(opts)
        root.addWidget(cmd_box, stretch=1)
        root.addWidget(transfer)
        root.addLayout(actions)

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

        root.addWidget(self._log, stretch=4)

        self._apply_style()

        self._wire_settings_persistence()
        self._load_settings_from_disk()
        self._refresh_rsync_preview()

        # Queued: rsync can emit thousands of stderr lines before the first --info=progress2 line;
        # a direct connection would block the GUI thread and freeze the session elapsed timer.
        self._rsync.log_line.connect(self._append_log, Qt.QueuedConnection)
        self._rsync.progress.connect(self._on_rsync_progress, Qt.QueuedConnection)
        self._rsync.attempt_changed.connect(self._on_attempt)
        self._rsync.transfer_pause_state_changed.connect(
            self._on_rsync_pause_state_changed, Qt.QueuedConnection
        )
        self._rsync.sync_finished.connect(self._on_sync_finished)
        self._rsync.stopped_by_user.connect(self._on_stopped)
        self._rsync.source_run_changed.connect(self._on_rsync_source_run_changed)

        self._reset_sync_transfer_panel()
        self._sync_guide_pulse()
        debug_log("UI", "main_window_ready")

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
            QGroupBox#PathsBox {
                padding: 4px 6px 3px 6px;
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
                min-height: 22px;
                max-height: 28px;
                padding: 2px 8px;
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

    def _qthread_ref_running(self, attr: str) -> bool:
        """True if the stored ``QThread`` exists and is running; clear the ref if libshiboken says it is deleted."""
        t = getattr(self, attr)
        if t is None:
            return False
        try:
            return t.isRunning()
        except RuntimeError:
            setattr(self, attr, None)
            return False

    def _qprocess_ref_busy(self, attr: str) -> bool:
        """True if the stored ``QProcess`` is non-null and not ``NotRunning``; clear stale refs on ``RuntimeError``."""
        p = getattr(self, attr)
        if p is None:
            return False
        try:
            return p.state() != QProcess.ProcessState.NotRunning
        except RuntimeError:
            setattr(self, attr, None)
            return False

    def _safe_qthread_quit(self, attr: str) -> None:
        t = getattr(self, attr)
        if t is None:
            return
        try:
            t.quit()
        except RuntimeError:
            setattr(self, attr, None)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._persist_settings()
        if self._rsync.is_syncing():
            QMessageBox.warning(
                self,
                "Quit",
                "Stop the sync with the Stop button, then close again.",
            )
            event.ignore()
            return
        if self._dest_space_busy():
            QMessageBox.warning(
                self,
                "Quit",
                "Wait for the destination space check to finish, then close again.",
            )
            event.ignore()
            return
        if self._qthread_ref_running("_update_check_thread"):
            QMessageBox.warning(
                self,
                "Quit",
                "Wait for the update check to finish, then close again.",
            )
            event.ignore()
            return
        debug_log("UI", "close_event_accepted")
        super().closeEvent(event)

    def _wire_settings_persistence(self) -> None:
        for w in (self._dest, self._extra_rsync):
            w.textChanged.connect(self._debounce_settings_and_preview)
        self._dest.textChanged.connect(self._on_paths_or_ssh_context_changed)
        self._dest.textChanged.connect(self._update_ssh_password_visibility)
        self._source_list.itemDoubleClicked.connect(
            lambda _it: self._prompt_edit_source()
        )
        self._timeout.valueChanged.connect(self._debounce_settings_and_preview)
        self._retry.valueChanged.connect(self._debounce_settings_and_preview)
        self._bwlimit.valueChanged.connect(self._debounce_settings_and_preview)
        self._dry_run.toggled.connect(self._debounce_settings_and_preview)
        self._recursive_subdirs.toggled.connect(self._debounce_settings_and_preview)
        self._radio_resume_partial.toggled.connect(self._debounce_settings_and_preview)
        self._radio_redo_partial.toggled.connect(self._debounce_settings_and_preview)
        self._combo_existing_files.currentIndexChanged.connect(
            self._debounce_settings_and_preview
        )

    @Slot()
    def _on_paths_or_ssh_context_changed(self) -> None:
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
        plist: List[str] = []
        for i in range(self._source_list.count()):
            it = self._source_list.item(i)
            if it is not None:
                t = it.text().strip()
                if t:
                    plist.append(t)
        # Paths, rsync options, and UI policy — never SSH passwords (source rows or destination).
        s.setValue("sources", plist[:_MAX_SOURCE_FOLDERS])
        s.setValue("dest", self._dest.text())
        s.setValue("io_timeout", self._timeout.value())
        s.setValue("retry_delay", self._retry.value())
        s.setValue("dry_run", self._dry_run.isChecked())
        s.setValue("recursive_subdirs", self._recursive_subdirs.isChecked())
        s.setValue("partial_resume", self._radio_resume_partial.isChecked())
        raw_m = self._combo_existing_files.currentData()
        s.setValue(
            "existing_files_mode",
            normalize_existing_files_mode(raw_m if isinstance(raw_m, str) else ""),
        )
        s.setValue("extra_rsync", self._extra_rsync.text())
        s.setValue("bwlimit", self._bwlimit.value())
        s.sync()
        debug_log("SETTINGS", "persisted")
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

    def _source_paths_list(self) -> List[str]:
        out: List[str] = []
        for i in range(self._source_list.count()):
            it = self._source_list.item(i)
            if it is not None:
                t = it.text().strip()
                if t:
                    out.append(t)
        return out

    def _source_password_for_row(self, row: int) -> str:
        it = self._source_list.item(row)
        if it is None:
            return ""
        raw = it.data(_SOURCE_ITEM_PW_ROLE)
        return str(raw).strip() if raw else ""

    def _append_source_list_item(self, path: str, password_plain: str = "") -> None:
        it = QListWidgetItem(path)
        it.setData(_SOURCE_ITEM_PW_ROLE, password_plain or "")
        self._source_list.addItem(it)
        self._rebase_source_ssh_hint_row(self._source_list.count() - 1)

    def _rebase_source_ssh_hint_row(self, row: int) -> None:
        """Reset list icon to path/password baseline (before a new Test SSH run or after edits)."""
        it = self._source_list.item(row)
        if it is None:
            return
        path = it.text().strip()
        if not path:
            it.setIcon(QIcon())
            return
        rmt, _ = parse_rsync_destination(path)
        if rmt is None:
            it.setIcon(_ssh_hint_icon_blank())
            it.setData(_SOURCE_ITEM_SSH_HINT_ROLE, _SSH_HINT_LOCAL)
            return
        if self._source_password_for_row(row):
            it.setIcon(_ssh_hint_icon_pending())
            it.setData(_SOURCE_ITEM_SSH_HINT_ROLE, _SSH_HINT_PENDING)
        else:
            it.setIcon(_ssh_hint_icon_no_password())
            it.setData(_SOURCE_ITEM_SSH_HINT_ROLE, _SSH_HINT_NONE_PW)

    def _apply_source_row_ssh_test_result(self, row: int, ok: bool) -> None:
        it = self._source_list.item(row)
        if it is None:
            return
        if ok:
            it.setIcon(_ssh_hint_icon_ok())
            it.setData(_SOURCE_ITEM_SSH_HINT_ROLE, _SSH_HINT_OK)
        else:
            it.setIcon(_ssh_hint_icon_fail())
            it.setData(_SOURCE_ITEM_SSH_HINT_ROLE, _SSH_HINT_FAIL)

    def _source_for_sync(self, raw_path: str, *, multi_source: bool = False) -> str:
        """
        Normalize a source path for transfer argv.

        Local sources use "copy this folder into the destination by name" semantics (trailing
        slash stripped except for filesystem root).

        Remote: single-source keeps the user's trailing slash (contents vs directory — rsync
        rules). Multiple sources strip trailing slashes on the remote path so each directory
        appears under the destination by name instead of merging contents into the same tree.
        """
        s = raw_path.strip()
        if not s:
            return s
        remote, _ = parse_rsync_destination(s)
        if remote is not None:
            p = canonical_rsync_path(s)
            if multi_source:
                p = remote_rsync_uri_strip_trailing_slashes(p)
            return p
        stripped = s.rstrip("/")
        if not stripped:
            stripped = "/"
        return canonical_rsync_path(stripped)

    @staticmethod
    def _source_path_dedup_key(path: str) -> str:
        """Stable key to detect duplicate sources (resolved locals; normalized remote strings)."""
        s = path.strip()
        if not s:
            return ""
        remote, _ = parse_rsync_destination(s)
        if remote is not None:
            return s.rstrip("/") + "/"
        try:
            return str(Path(s).expanduser().resolve(strict=False)) + "/"
        except OSError:
            return s if s.endswith("/") else s + "/"

    def _apply_paths_side_button_metrics(self) -> None:
        """Equal size for source/destination side column buttons."""
        side_btns = (
            self._btn_add_source,
            self._btn_edit_source,
            self._btn_remove_source,
            self._btn_edit_dest,
            self._btn_test_ssh_dest,
        )
        h = _PATHS_SIDE_BTN_MIN_H
        w = 0
        for b in side_btns:
            sh = b.sizeHint()
            h = max(h, sh.height())
            w = max(w, sh.width())
        w += 8
        for b in side_btns:
            b.setFixedSize(w, h)

    def _dialog_test_remote_ssh(
        self,
        parent: QWidget,
        raw_path: str,
        password_plain: str,
        *,
        hint_row: Optional[int],
        empty_path_message: str = "Enter a remote path first.",
        not_remote_message: str = (
            "That entry is not remote. SSH test applies to user@host:/path sources."
        ),
    ) -> None:
        raw = raw_path.strip()
        if not raw:
            QMessageBox.warning(parent, "SSH test", empty_path_message)
            return
        norm = canonical_rsync_path(raw)
        rmt, _ = parse_rsync_destination(norm)
        if rmt is None:
            QMessageBox.information(parent, "SSH test", not_remote_message)
            return
        pw_plain = password_plain.strip()
        pw_ssh = pw_plain if pw_plain and shutil.which("sshpass") else None
        extra_env: Optional[Dict[str, str]] = None
        if pw_ssh is None:
            w = ensure_ssh_askpass_wrapper()
            extra_env = {
                "SSH_ASKPASS": str(w),
                "SSH_ASKPASS_REQUIRE": "force",
            }
        # Derive batch_mode from the dialog's own context: if a password was
        # supplied we need password/kbd-interactive auth (batch_mode=False);
        # otherwise allow key-based batch mode.
        batch = not bool(pw_plain)
        try:
            cp = run_ssh_command(
                rmt,
                "echo ok",
                connect_timeout=SSH_TEST_CONNECT_TIMEOUT_SEC,
                batch_mode=batch,
                extra_env=extra_env,
                password_for_sshpass=pw_ssh,
            )
        except FileNotFoundError as e:
            QMessageBox.critical(parent, "SSH test", str(e))
            if hint_row is not None:
                self._apply_source_row_ssh_test_result(hint_row, False)
            return
        except subprocess.TimeoutExpired:
            QMessageBox.warning(
                parent,
                "SSH test",
                "Connection timed out. Check host, network, and firewall.",
            )
            if hint_row is not None:
                self._apply_source_row_ssh_test_result(hint_row, False)
            return
        out = (cp.stdout or "").strip().lower()
        ok = cp.returncode == 0 and "ok" in out
        if ok:
            debug_log("SSH", "dialog_test_ok", host=rmt.ssh_spec())
            QMessageBox.information(parent, "SSH test", "SSH connection succeeded.")
            if hint_row is not None:
                self._apply_source_row_ssh_test_result(hint_row, True)
        else:
            err = (cp.stderr or "").strip() or f"exit {cp.returncode}"
            debug_log(
                "SSH",
                "dialog_test_fail",
                host=rmt.ssh_spec(),
                returncode=cp.returncode,
            )
            QMessageBox.warning(parent, "SSH test", f"SSH test failed:\n{err}")
            if hint_row is not None:
                self._apply_source_row_ssh_test_result(hint_row, False)

    def _run_source_path_dialog(
        self,
        *,
        title: str,
        initial_path: str,
        initial_pw: str,
        ssh_hint_row: Optional[int] = None,
    ) -> Optional[Tuple[str, str]]:
        """
        Add/Edit source dialog. Returns ``(normalized_path, password_plain)`` or ``None``
        if cancelled or validation failed after OK.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(420)
        v = QVBoxLayout(dlg)
        rb_local = QRadioButton("Local folder")
        rb_remote = QRadioButton("Remote (SSH / SFTP)")
        bg = QButtonGroup(dlg)
        bg.addButton(rb_local)
        bg.addButton(rb_remote)
        init = initial_path.strip()
        remote_initial, _ = parse_rsync_destination(init) if init else (None, "")
        if init and remote_initial is not None:
            rb_remote.setChecked(True)
        else:
            rb_local.setChecked(True)
        row_kind = QHBoxLayout()
        row_kind.addWidget(rb_local)
        row_kind.addWidget(rb_remote)
        row_kind.addStretch(1)
        v.addLayout(row_kind)
        le_path = QLineEdit()
        le_path.setMinimumHeight(28)
        le_path.setPlaceholderText("e.g. /home/you/Documents/ or user@host:/data/")
        le_pw = QLineEdit()
        le_pw.setEchoMode(QLineEdit.EchoMode.Password)
        le_pw.setPlaceholderText("SSH password if needed (not saved)")
        le_pw.setMinimumHeight(28)
        le_pw.setText(initial_pw)
        btn_test_ssh = QPushButton("Test SSH connection…")
        btn_test_ssh.setMinimumHeight(28)
        btn_test_ssh.setToolTip(
            "Run echo ok on the remote host using the path and password above "
            "(same as sync: keys, sshpass, or SSH_ASKPASS)."
        )
        pw_wrap = QWidget()
        pw_h = QHBoxLayout(pw_wrap)
        pw_h.setContentsMargins(0, 0, 0, 0)
        pw_h.setSpacing(8)
        pw_h.addWidget(le_pw, 1)
        pw_h.addWidget(btn_test_ssh, 0)
        btn_browse = QPushButton("Browse…")
        btn_browse.setMinimumHeight(28)
        path_row = QHBoxLayout()
        path_row.addWidget(le_path, 1)
        path_row.addWidget(btn_browse, 0)
        v.addLayout(path_row)
        v.addWidget(pw_wrap)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        v.addWidget(bb)
        le_path.setText(init)

        def sync_mode() -> None:
            loc = rb_local.isChecked()
            btn_browse.setVisible(loc)
            pw_wrap.setVisible(not loc)
            if loc:
                le_path.setPlaceholderText("e.g. /home/you/Backups/MyFolder/")
            else:
                le_path.setPlaceholderText("e.g. user@host:/mnt/share/ or paste sftp://…")

        def browse_local() -> None:
            start = le_path.text().strip()
            remote, _ = parse_rsync_destination(start)
            if remote is not None:
                start = str(Path.home())
            pick = start or str(Path.home())
            d = QFileDialog.getExistingDirectory(dlg, "Select source directory", pick)
            if d:
                le_path.setText(d if d.endswith("/") else d + "/")

        rb_local.toggled.connect(lambda _c: sync_mode())
        rb_remote.toggled.connect(lambda _c: sync_mode())
        btn_browse.clicked.connect(browse_local)
        btn_test_ssh.clicked.connect(
            lambda: self._dialog_test_remote_ssh(
                dlg,
                le_path.text(),
                le_pw.text(),
                hint_row=ssh_hint_row,
            )
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        sync_mode()

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        raw = le_path.text().strip()
        if not raw:
            QMessageBox.warning(self, title, "Enter a source path.")
            return None

        if rb_local.isChecked():
            remote, _ = parse_rsync_destination(raw)
            if remote is not None:
                QMessageBox.warning(
                    self,
                    title,
                    "That looks like a remote path. Choose “Remote (SSH / SFTP)” instead.",
                )
                return None
            norm = raw if raw.endswith("/") else raw + "/"
            return (norm, "")
        norm = canonical_rsync_path(raw)
        remote, _ = parse_rsync_destination(norm)
        if remote is None:
            QMessageBox.warning(
                self,
                title,
                "Remote path not recognized. Use user@host:/path or sftp://user@host/path.",
            )
            return None
        pw = le_pw.text().strip()
        return (norm, pw)

    def _load_source_list_from_settings(self, s: QSettings) -> None:
        self._source_list.clear()
        raw = s.value("sources")
        paths: List[str] = []
        if isinstance(raw, list) and raw:
            paths = [str(x).strip() for x in raw if str(x).strip()]
        if not paths:
            legacy = s.value("source", "", type=str).strip()
            if legacy:
                paths = [legacy]
            else:
                paths = ["/mnt/nas/Archive/"]
        if len(paths) > _MAX_SOURCE_FOLDERS:
            paths = paths[:_MAX_SOURCE_FOLDERS]
        for p in paths:
            self._append_source_list_item(p, "")

    def _on_sources_mutation(self) -> None:
        self._on_paths_or_ssh_context_changed()
        self._update_ssh_password_visibility()
        self._debounce_settings_and_preview()

    def _load_settings_from_disk(self) -> None:
        self._settings_loading = True
        try:
            s = QSettings("SafeCopi", "SafeCopi")
            self._load_source_list_from_settings(s)
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
            stored_mode = normalize_existing_files_mode(
                s.value("existing_files_mode", EXISTING_FILES_MODE_DEFAULT, type=str)
            )
            idx = self._combo_existing_files.findData(stored_mode)
            if idx < 0:
                idx = self._combo_existing_files.findData(EXISTING_FILES_MODE_DEFAULT)
            self._combo_existing_files.setCurrentIndex(0 if idx < 0 else idx)
            self._extra_rsync.setText(s.value("extra_rsync", "", type=str))
            self._bwlimit.setValue(self._settings_int(s, "bwlimit", 0, 0, 999_999))
        finally:
            self._settings_loading = False
            self._update_ssh_password_visibility()
            debug_log("SETTINGS", "loaded_from_disk")

    def _refresh_rsync_preview(self) -> None:
        try:
            self._parsed_user_extra_args()
        except ValueError as e:
            debug_log("RSYNC", "preview_invalid_extra_args", error=str(e))
            self._rsync_preview.setPlainText(f"(invalid extra args: {e})")
            return
        paths = self._source_paths_list()
        dest = canonical_rsync_path(self._dest.text())
        if not paths:
            self._rsync_preview.setPlainText("(add at least one source)")
            return
        if not dest:
            self._rsync_preview.setPlainText("(set destination)")
            return
        rec = self._recursive_subdirs.isChecked()
        to = self._timeout.value()
        multi = len(paths) > 1
        if len(paths) == 1:
            mod = self._collect_rsync_modifiers_for_source(0)
            argv = build_rsync_command_argv(
                self._source_for_sync(paths[0], multi_source=multi),
                dest,
                to,
                mod,
                recursive=rec,
            )
            self._rsync_preview.setPlainText(shlex.join(argv))
            return
        lines = [
            f"# {len(paths)} rsync runs — each folder is created under the destination:",
            *(
                shlex.join(
                    build_rsync_command_argv(
                        self._source_for_sync(p, multi_source=multi),
                        dest,
                        to,
                        self._collect_rsync_modifiers_for_source(i),
                        recursive=rec,
                    )
                )
                for i, p in enumerate(paths)
            ),
        ]
        self._rsync_preview.setPlainText("\n".join(lines))

    def _parsed_user_extra_args(self) -> List[str]:
        return parse_extra_rsync_args(self._extra_rsync.text())

    def _collect_rsync_modifiers_for_source(self, source_index: int) -> List[str]:
        args: List[str] = []
        if self._radio_resume_partial.isChecked():
            args.append("--partial")
        if self._dry_run.isChecked():
            args.append("--dry-run")
        raw_m = self._combo_existing_files.currentData()
        args.extend(
            existing_files_mode_rsync_argv(
                raw_m if isinstance(raw_m, str) else EXISTING_FILES_MODE_DEFAULT
            )
        )
        bw = self._bwlimit.value()
        if bw > 0:
            args.append(f"--bwlimit={bw}")
        args.extend(self._parsed_user_extra_args())
        paths = self._source_paths_list()
        dst_remote, _rpath = self._parsed_destination()
        src_remote_this = False
        if paths and 0 <= source_index < len(paths):
            src_remote_this = (
                parse_rsync_destination(paths[source_index])[0] is not None
            )
        if src_remote_this or dst_remote is not None:
            args.extend(
                [
                    "-e",
                    rsync_ssh_e_shell(
                        self._timeout.value(),
                        self._ssh_batch_mode(),
                        password_for_sshpass=self._rsync_sshpass_password_for_source_index(
                            source_index
                        ),
                    ),
                ]
            )
        return args

    def _apply_idle_control_state_after_preflight_async(self) -> None:
        """Re-enable path/sync widgets after a background destination space (df) task completes."""
        if self._rsync.is_syncing() or self._pending_sync_launch:
            self._set_path_and_rsync_controls_enabled(False)
        else:
            self._set_path_and_rsync_controls_enabled(True)

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

    def _reset_stat_labels_idle(self) -> None:
        self._lbl_stat_size.setText("—")
        self._lbl_stat_files.setText("—")
        self._lbl_stat_data_rem.setText("—")
        self._lbl_stat_speed.setText("—")
        self._lbl_stat_eta.setText("—")
        self._lbl_stat_dest_free.setText("—")

    def _reset_sync_transfer_panel(self) -> None:
        self._sync_progress_timer.stop()
        self._awaiting_first_rsync_progress = False
        self._stop_sync_session_wall_clock(reset_label=True)
        self._btn_pause.setText("Pause")
        self._btn_pause.setEnabled(False)
        self._pending_sync_snap = None
        self._sync_attempt_shown = 1
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")
        self._sync_bar_peak = 0
        self._sync_total_source_runs = 1
        self._sync_left_monotone_tb = None
        self._sync_left_monotone_display = None
        self._sync_carry_tb = None
        self._reset_stat_labels_idle()
        self._lbl_sync_detail.setText("")
        self._lbl_sync_path.setText("")

    def _sync_panel_starting(self) -> None:
        self._sync_progress_timer.stop()
        self._pending_sync_snap = None
        self._progress.setRange(0, 10_000)
        self._progress.setValue(0)
        self._progress.setFormat("0.00\u0025")
        self._sync_attempt_shown = 1
        self._sync_bar_peak = 0
        self._sync_rsync_source_step = 0
        self._sync_total_source_runs = max(1, len(self._source_paths_list()))
        self._sync_left_monotone_tb = None
        self._sync_left_monotone_display = None
        self._sync_carry_tb = None
        self._reset_stat_labels_idle()
        self._awaiting_first_rsync_progress = True
        self._sync_dot_phase = 0
        self._lbl_sync_detail.setText(f"ATTEMPT {self._sync_attempt_shown}")
        self._lbl_sync_path.setText("")
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

    @staticmethod
    def _queue_based_bar_hundredths(snap: RsyncProgressSnapshot) -> int:
        """When rsync reports 0%% overall but to-chk advances, drive the bar from queue completion."""
        q = parse_rsync_queue_remaining_total(snap.stats_raw)
        if q is None:
            return 0
        rem, tot = q
        if tot <= 0 or rem < 0 or rem > tot:
            return 0
        done = tot - rem
        return min(10_000, max(0, int(10_000 * done / tot)))

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
        elapsed = self._format_wall_elapsed_ms(self._session_elapsed.elapsed())
        suffix = ""
        if self._awaiting_first_rsync_progress:
            self._sync_dot_phase = (self._sync_dot_phase + 1) % 3
            suffix = "  processing" + ("." * (self._sync_dot_phase + 1))
        src_sfx = self._elapsed_label_current_source_suffix()
        self._lbl_sync_elapsed.setText(
            "Session elapsed: " + elapsed + suffix + src_sfx
        )

    def _sync_transfer_bar_units(self, snap: RsyncProgressSnapshot) -> int:
        """
        Map transfer state to 0..10000 for the bar (hundredths of a percent).

        Each rsync process reports 0–100% for its own run. With multiple sources, the bar is reset
        to 0 when switching sources so each segment is visible; a monotonic peak applies only within
        the current source.
        """
        pct_u = min(10_000, max(0, snap.percent * 100))
        q_u = self._queue_based_bar_hundredths(snap)
        return min(10_000, max(pct_u, q_u))

    @staticmethod
    def _compact_path_for_elapsed_label(raw: str, max_len: int = 76) -> str:
        s = raw.strip()
        if len(s) <= max_len:
            return s
        left = max(12, max_len // 2 - 2)
        right = max(12, max_len - left - 3)
        if left + right + 3 > len(s):
            return s[:max_len - 1] + "…"
        return s[:left] + "…" + s[-right:]

    def _elapsed_label_current_source_suffix(self) -> str:
        """Tail for Session elapsed: `` — <source>`` (empty if idle or no paths)."""
        if not self._sync_session_active:
            return ""
        paths = self._source_paths_list()
        if not paths:
            return ""
        step = self._sync_rsync_source_step
        if step < 1:
            step = 1
        step = min(step, len(paths))
        raw = paths[step - 1].strip()
        if not raw:
            return ""
        disp = self._compact_path_for_elapsed_label(raw)
        return f" — {disp}"

    @Slot()
    def _flush_sync_transfer_ui(self) -> None:
        # Progress uses QueuedConnection; the last progress update can be delivered after
        # sync_finished (process exit) has already set the bar to 100 %. Ignore stale flushes.
        if not self._rsync.is_syncing():
            self._pending_sync_snap = None
            return
        snap = self._pending_sync_snap
        if snap is None:
            return
        if self._progress.maximum() != 10_000:
            self._progress.setRange(0, 10_000)
        tb_snap = snap.transferred_bytes
        if tb_snap is not None:
            self._sync_carry_tb = tb_snap
        tb = tb_snap if tb_snap is not None else self._sync_carry_tb
        total_from_pct = estimate_rsync_total_bytes_from_progress(tb, snap.percent)
        qpair = parse_rsync_queue_remaining_total(snap.stats_raw)
        speed_bps = parse_rsync_speed_to_bytes_per_sec(snap.speed)

        total_from_queue: Optional[int] = None
        if tb is not None and tb > 0 and qpair is not None:
            rem_q, tot_q = qpair
            done_n = tot_q - rem_q
            # Need enough completed queue slots before bytes/file ratio is meaningful.
            if tot_q > 0 and done_n >= max(8, tot_q // 50_000):
                total_guess = (tb * tot_q + done_n // 2) // done_n
                if tb <= total_guess <= _MAX_INFERRED_TRANSFER_BYTES + tb:
                    total_from_queue = total_guess

        left_b: Optional[int] = None
        if total_from_pct is not None and tb is not None:
            left_b = max(0, total_from_pct - tb)
        if left_b is None and total_from_queue is not None and tb is not None:
            left_b = max(0, min(total_from_queue - tb, _MAX_INFERRED_TRANSFER_BYTES))
        if left_b is None and speed_bps is not None and speed_bps > 1e-9:
            eta_sec = parse_rsync_eta_token_to_seconds(snap.eta)
            if eta_sec is not None:
                cand = int(speed_bps * eta_sec)
                if 0 < cand <= _MAX_INFERRED_TRANSFER_BYTES:
                    left_b = cand

        left_b, self._sync_left_monotone_tb, self._sync_left_monotone_display = (
            clamp_monotonic_data_left_bytes(
                left_b,
                tb,
                self._sync_left_monotone_tb,
                self._sync_left_monotone_display,
            )
        )

        # Denominator matches displayed "data left" when present (tb + left_b); else percent/queue.
        total_for_size: Optional[int] = None
        if left_b is not None and tb is not None:
            total_for_size = min(tb + left_b, tb + _MAX_INFERRED_TRANSFER_BYTES)
        elif total_from_pct is not None:
            total_for_size = total_from_pct
        elif total_from_queue is not None:
            total_for_size = total_from_queue
        if total_for_size is not None and tb is not None and total_for_size < tb:
            total_for_size = tb
        use_bytes_eta = (
            left_b is not None
            and speed_bps is not None
            and speed_bps > 1e-9
        )
        if use_bytes_eta:
            eta_disp = format_seconds_as_hms_display(left_b / speed_bps)
        else:
            eta_disp = format_rsync_hms_for_display(snap.eta)

        rsync_eta_norm = format_rsync_hms_for_display(snap.eta)
        if snap.percent >= 99 and (
            (use_bytes_eta and eta_disp == "00:00:00")
            or (not use_bytes_eta and rsync_eta_norm == "00:00:00")
            or (left_b is not None and left_b == 0)
        ):
            eta_disp = "finishing…"

        raw_u = self._sync_transfer_bar_units(snap)
        self._sync_bar_peak = max(self._sync_bar_peak, raw_u)
        self._progress.setValue(self._sync_bar_peak)
        pct_bar = min(100.0, self._sync_bar_peak / 100.0)
        self._progress.setFormat(f"{pct_bar:.2f}\u0025")

        if total_for_size is not None and tb is not None:
            self._lbl_stat_size.setText(f"{human_bytes(tb)} / {human_bytes(total_for_size)}")
        elif tb is not None:
            self._lbl_stat_size.setText(human_bytes(tb))
        else:
            self._lbl_stat_size.setText("—")

        if qpair is not None:
            rem, tot = qpair
            self._lbl_stat_files.setText(f"{rem:,} / {tot:,}")
        else:
            self._lbl_stat_files.setText("—")

        if left_b is not None:
            self._lbl_stat_data_rem.setText(human_bytes(left_b))
        else:
            self._lbl_stat_data_rem.setText("—")

        self._lbl_stat_speed.setText(snap.speed)
        self._lbl_stat_eta.setText(eta_disp)

        df = self._dest_free_bytes
        self._lbl_stat_dest_free.setText(
            human_bytes(df) if df is not None else "—"
        )

        self._lbl_sync_detail.setText(f"ATTEMPT {self._sync_attempt_shown}")
        cp = (snap.current_path or "").strip()
        self._lbl_sync_path.setText(cp)
        self._lbl_sync_path.setToolTip(cp if len(cp) > 120 else "")
        self._lbl_sync_detail.setToolTip("")

    @Slot(object)
    def _on_rsync_progress(self, snap: object) -> None:
        if not isinstance(snap, RsyncProgressSnapshot):
            return
        if not self._rsync.is_syncing():
            return
        if self._awaiting_first_rsync_progress:
            self._awaiting_first_rsync_progress = False
            self._update_sync_session_elapsed_label()
        self._pending_sync_snap = snap
        self._sync_progress_timer.start(80)

    @Slot(int, int)
    def _on_rsync_source_run_changed(self, step: int, total: int) -> None:
        self._sync_total_source_runs = max(1, total)
        if total <= 1:
            return
        if step != self._sync_rsync_source_step:
            self._sync_rsync_source_step = step
            self._pending_sync_snap = None
            self._sync_left_monotone_tb = None
            self._sync_left_monotone_display = None
            self._sync_carry_tb = None
            self._sync_bar_peak = 0
            if self._progress.maximum() == 10_000:
                self._progress.setValue(0)
                self._progress.setFormat("0.00\u0025")
            debug_log(
                "UI",
                "sync_progress_source_segment",
                step=step,
                total_sources=total,
            )
            self._update_sync_session_elapsed_label()

    def _append_log(self, line: str) -> None:
        sb = self._log.verticalScrollBar()
        for segment in line.splitlines():
            if len(segment) > _LOG_LINE_MAX_CHARS:
                segment = segment[:_LOG_LINE_MAX_CHARS] + " … [truncated]"
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._log.appendPlainText(f"[{ts}] {segment}")
        sb.setValue(sb.maximum())

    @Slot()
    def _prompt_add_source(self) -> None:
        if self._source_list.count() >= _MAX_SOURCE_FOLDERS:
            QMessageBox.information(
                self,
                "Source list",
                f"At most {_MAX_SOURCE_FOLDERS} sources are supported.",
            )
            return
        res = self._run_source_path_dialog(
            title="Add source",
            initial_path="",
            initial_pw="",
        )
        if res is None:
            return
        norm, pw = res
        paths = self._source_paths_list()
        keys = {self._source_path_dedup_key(p) for p in paths}
        nk = self._source_path_dedup_key(norm)
        if nk in keys:
            return
        self._append_source_list_item(norm, pw)
        self._on_sources_mutation()

    @Slot()
    def _prompt_edit_source(self) -> None:
        row = self._source_list.currentRow()
        if row < 0:
            QMessageBox.information(
                self,
                "Edit source",
                "Select a source to edit.",
            )
            return
        it = self._source_list.item(row)
        if it is None:
            return
        initial = it.text().strip()
        pw0 = self._source_password_for_row(row)
        res = self._run_source_path_dialog(
            title="Edit source",
            initial_path=initial,
            initial_pw=pw0,
            ssh_hint_row=row,
        )
        if res is None:
            return
        norm, pw = res
        paths = self._source_paths_list()
        if row < 0 or row >= len(paths):
            return
        keys = {
            self._source_path_dedup_key(p)
            for i, p in enumerate(paths)
            if i != row
        }
        nk = self._source_path_dedup_key(norm)
        if nk in keys:
            QMessageBox.warning(
                self,
                "Edit source",
                "Another row already uses that path.",
            )
            return
        it.setText(norm)
        it.setData(_SOURCE_ITEM_PW_ROLE, pw or "")
        self._rebase_source_ssh_hint_row(row)
        self._on_sources_mutation()

    @Slot()
    def _remove_source_folder(self) -> None:
        row = self._source_list.currentRow()
        if row < 0:
            return
        self._source_list.takeItem(row)
        self._on_sources_mutation()

    @Slot()
    def _prompt_edit_dest(self) -> None:
        """Open a dialog (matching the source dialog) to set the destination path + password."""
        init_path = self._dest.text().strip()
        init_pw = self._ssh_password.text().strip()
        res = self._run_dest_path_dialog(
            title="Edit destination",
            initial_path=init_path,
            initial_pw=init_pw,
        )
        if res is None:
            return
        norm, pw = res
        self._dest.setText(norm)
        self._ssh_password.setText(pw)

    def _run_dest_path_dialog(
        self,
        *,
        title: str,
        initial_path: str,
        initial_pw: str,
    ) -> Optional[Tuple[str, str]]:
        """
        Destination path dialog (local or remote). Returns ``(normalized_path, password_plain)``
        or ``None`` if cancelled or validation failed after OK.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(420)
        v = QVBoxLayout(dlg)
        rb_local = QRadioButton("Local folder")
        rb_remote = QRadioButton("Remote (SSH / SFTP)")
        bg = QButtonGroup(dlg)
        bg.addButton(rb_local)
        bg.addButton(rb_remote)
        init = initial_path.strip()
        remote_initial, _ = parse_rsync_destination(init) if init else (None, "")
        if init and remote_initial is not None:
            rb_remote.setChecked(True)
        else:
            rb_local.setChecked(True)
        row_kind = QHBoxLayout()
        row_kind.addWidget(rb_local)
        row_kind.addWidget(rb_remote)
        row_kind.addStretch(1)
        v.addLayout(row_kind)
        le_path = QLineEdit()
        le_path.setMinimumHeight(28)
        le_path.setPlaceholderText("e.g. /mnt/backups/Archive/ or user@host:/mnt/backup/")
        le_pw = QLineEdit()
        le_pw.setEchoMode(QLineEdit.EchoMode.Password)
        le_pw.setPlaceholderText("SSH password if needed (not saved)")
        le_pw.setMinimumHeight(28)
        le_pw.setText(initial_pw)
        btn_test_ssh = QPushButton("Test SSH connection…")
        btn_test_ssh.setMinimumHeight(28)
        btn_test_ssh.setToolTip(
            "Run echo ok on the remote host using the path and password above "
            "(same as sync: keys, sshpass, or SSH_ASKPASS)."
        )
        pw_wrap = QWidget()
        pw_h = QHBoxLayout(pw_wrap)
        pw_h.setContentsMargins(0, 0, 0, 0)
        pw_h.setSpacing(8)
        pw_h.addWidget(le_pw, 1)
        pw_h.addWidget(btn_test_ssh, 0)
        btn_browse = QPushButton("Browse…")
        btn_browse.setMinimumHeight(28)
        path_row = QHBoxLayout()
        path_row.addWidget(le_path, 1)
        path_row.addWidget(btn_browse, 0)
        v.addLayout(path_row)
        v.addWidget(pw_wrap)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        v.addWidget(bb)
        le_path.setText(init)

        def sync_mode() -> None:
            loc = rb_local.isChecked()
            btn_browse.setVisible(loc)
            pw_wrap.setVisible(not loc)
            if loc:
                le_path.setPlaceholderText("e.g. /mnt/backups/Archive/")
            else:
                le_path.setPlaceholderText("e.g. user@host:/mnt/backup/ or paste sftp://…")

        def browse_local() -> None:
            start = le_path.text().strip()
            remote, _ = parse_rsync_destination(start)
            if remote is not None:
                start = str(Path.home())
            pick = start or str(Path.home())
            d = QFileDialog.getExistingDirectory(dlg, "Select destination directory", pick)
            if d:
                le_path.setText(d if d.endswith("/") else d + "/")

        rb_local.toggled.connect(lambda _c: sync_mode())
        rb_remote.toggled.connect(lambda _c: sync_mode())
        btn_browse.clicked.connect(browse_local)
        btn_test_ssh.clicked.connect(
            lambda: self._dialog_test_remote_ssh(
                dlg,
                le_path.text(),
                le_pw.text(),
                hint_row=None,
            )
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        sync_mode()

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        raw = le_path.text().strip()
        if not raw:
            QMessageBox.warning(self, title, "Enter a destination path.")
            return None

        if rb_local.isChecked():
            remote, _ = parse_rsync_destination(raw)
            if remote is not None:
                QMessageBox.warning(
                    self,
                    title,
                    "That looks like a remote path. Choose \"Remote (SSH / SFTP)\" instead.",
                )
                return None
            norm = raw if raw.endswith("/") else raw + "/"
            return (norm, "")
        norm = canonical_rsync_path(raw)
        remote, _ = parse_rsync_destination(norm)
        if remote is None:
            QMessageBox.warning(
                self,
                title,
                "Remote path not recognized. Use user@host:/path or sftp://user@host/path.",
            )
            return None
        pw = le_pw.text().strip()
        return (norm, pw)

    @Slot()
    def _test_ssh_destination(self) -> None:
        self._dialog_test_remote_ssh(
            self,
            self._dest.text(),
            self._ssh_password.text(),
            hint_row=None,
            empty_path_message="Enter a destination path first.",
            not_remote_message=(
                "SSH test applies when the destination is user@host:/path."
            ),
        )

    @Slot()
    def _update_ssh_password_visibility(self) -> None:
        dr = self._parsed_destination()[0] is not None
        self._ssh_pw_wrap.setVisible(dr)
        self._btn_test_ssh_dest.setVisible(dr)

    def _parsed_destination(self) -> Tuple[Optional[RemoteTarget], str]:
        return parse_rsync_destination(self._dest.text().strip())

    def _remote_for_dest(self) -> Optional[RemoteTarget]:
        return self._parsed_destination()[0]

    def _ssh_source_is_remote(self) -> bool:
        for p in self._source_paths_list():
            if parse_rsync_destination(p)[0] is not None:
                return True
        return False

    def _ssh_destination_is_remote(self) -> bool:
        return self._parsed_destination()[0] is not None

    def _ssh_either_remote(self) -> bool:
        return self._ssh_source_is_remote() or self._ssh_destination_is_remote()

    def _ssh_batch_mode(self) -> bool:
        # Rsync -e: allow password / kbd-interactive when either side is remote.
        return not self._ssh_either_remote()

    def _ssh_password_dest_plain(self) -> str:
        return self._ssh_password.text().strip()

    def _dest_sshpass_password(self) -> Optional[str]:
        if not self._ssh_destination_is_remote():
            return None
        pw = self._ssh_password_dest_plain()
        if pw and shutil.which("sshpass"):
            return pw
        return None

    def _source_sshpass_password_for_row(self, row: int) -> Optional[str]:
        paths = self._source_paths_list()
        if row < 0 or row >= len(paths):
            return None
        if parse_rsync_destination(paths[row])[0] is None:
            return None
        pw = self._source_password_for_row(row)
        if pw and shutil.which("sshpass"):
            return pw
        return None

    def _rsync_sshpass_password_for_source_index(self, source_index: int) -> Optional[str]:
        """
        sshpass secret for the rsync process when syncing ``source_index`` against the
        current destination (per-row source password when that row is remote).
        """
        if not shutil.which("sshpass"):
            return None
        paths = self._source_paths_list()
        if not paths or source_index < 0 or source_index >= len(paths):
            return None
        src_r = parse_rsync_destination(paths[source_index])[0] is not None
        dst_r = self._ssh_destination_is_remote()
        dp = self._dest_sshpass_password()
        sp = self._source_sshpass_password_for_row(source_index)
        if dst_r and src_r:
            return dp or sp
        if dst_r:
            return dp
        if src_r:
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

    def _ssh_qprocess_env_for_source_index(self, source_index: int) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        paths = self._source_paths_list()
        if paths and 0 <= source_index < len(paths):
            src_r = parse_rsync_destination(paths[source_index])[0] is not None
        else:
            src_r = False
        dst_r = self._ssh_destination_is_remote()
        secret = self._rsync_sshpass_password_for_source_index(source_index)
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

    def _dest_space_busy(self) -> bool:
        if self._qthread_ref_running("_space_thread"):
            return True
        return self._qprocess_ref_busy("_space_process")

    def _abort_deferred_sync_start_after_space_error(self, msg: str) -> None:
        self._sync_waiting_for_dest_space = False
        self._pending_sync_launch = False
        self._append_log(f"Sync cancelled: destination space check failed: {msg}")
        self._set_path_and_rsync_controls_enabled(True)
        self._btn_start.setEnabled(True)
        self._btn_pause.setText("Pause")
        self._btn_pause.setEnabled(False)
        self._btn_stop.setEnabled(False)
        self._reset_sync_transfer_panel()
        self._sync_guide_pulse()
        QMessageBox.warning(
            self,
            "Start sync",
            f"Could not verify destination free space:\n{msg}",
        )

    def _finalize_dest_space_ui(
        self,
        free_b: Optional[int],
        err_s: Optional[str],
        was_remote: bool,
    ) -> None:
        sync_wait = self._sync_waiting_for_dest_space

        if err_s is not None:
            debug_log(
                "SPACE",
                "check_error",
                error=err_s[:800],
                remote=was_remote,
            )
            if sync_wait:
                self._sync_waiting_for_dest_space = False
                self._abort_deferred_sync_start_after_space_error(err_s)
            else:
                QMessageBox.critical(self, "Space check", err_s)
                self._append_log(f"Space check error: {err_s}")
            return

        self._dest_free_bytes = free_b
        if not was_remote:
            debug_log(
                "SPACE",
                "check_done",
                remote=False,
                transport="thread_local",
                readable=free_b is not None,
            )
        if was_remote:
            self._lbl_stat_dest_free.setText(
                human_bytes(free_b) if free_b is not None else "—"
            )
            if free_b is None:
                if sync_wait:
                    self._sync_waiting_for_dest_space = False
                    self._abort_deferred_sync_start_after_space_error(
                        "Could not read remote free space. Verify SSH and the path."
                    )
                    return
                QMessageBox.warning(
                    self,
                    "Space check",
                    "Could not read remote free space. Verify SSH and the path.",
                )
            else:
                self._append_log(f"Remote free (df): {human_bytes(free_b)}")
        else:
            self._lbl_stat_dest_free.setText(
                human_bytes(free_b) if free_b is not None else "— (unreadable)"
            )
            self._append_log(f"Local destination free: {human_bytes(free_b)}")

        if sync_wait:
            self._pending_sync_launch = True
            self._sync_waiting_for_dest_space = False
            QTimer.singleShot(0, self._run_rsync_after_ui_tick)

    @Slot()
    def _check_dest_space(self) -> None:
        if self._dest_space_busy():
            return
        dest = self._dest.text().strip()
        remote, rpath = self._parsed_destination()
        if remote is None and not dest:
            debug_log("SPACE", "ui_check_skipped_empty_dest", remote=False)
            return
        debug_log("SPACE", "ui_check_start", remote=remote is not None)
        self._sync_guide_pulse()
        if remote is not None:
            self._append_log(f"Querying free space on {remote.ssh_spec()}:{rpath} …")
            self._start_remote_dest_space_qprocess(remote, rpath)
            return
        self._start_local_dest_space_thread(dest)

    def _start_local_dest_space_thread(self, dest: str) -> None:
        io_sec = min(max(10, self._timeout.value()), 120)
        thread = QThread(self)
        worker = DestSpaceWorker()
        worker.moveToThread(thread)
        worker.prepare_local(dest, query_timeout_sec=float(min(io_sec + 60, 180)))
        # Ensure worker.run is queued onto the worker thread event loop on all Qt/PySide builds.
        thread.started.connect(
            lambda: QMetaObject.invokeMethod(worker, "run", Qt.QueuedConnection)
        )
        worker.finished.connect(self._on_dest_space_worker_finished, Qt.QueuedConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_dest_space_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._space_thread = thread
        thread.start()

    def _start_remote_dest_space_qprocess(
        self, remote: RemoteTarget, rpath: str
    ) -> None:
        ct = min(max(10, self._timeout.value()), 120)
        self._space_connect_timeout_sec = ct
        debug_log(
            "SPACE",
            "check_start",
            remote=True,
            transport="qprocess_ssh",
            connect_timeout_sec=ct,
            host=remote.ssh_spec(),
        )
        try:
            remote_cmd = build_remote_df_shell_command(rpath)
            argv = build_ssh_command_argv(
                remote,
                remote_cmd,
                connect_timeout=ct,
                batch_mode=self._ssh_batch_mode(),
                password_for_sshpass=self._dest_sshpass_password(),
            )
        except FileNotFoundError as e:
            debug_log("SPACE", "qprocess_configure_failed", error=str(e))
            if self._sync_waiting_for_dest_space:
                self._abort_deferred_sync_start_after_space_error(str(e))
            else:
                QMessageBox.critical(self, "Space check", str(e))
                self._append_log(f"Space check error: {e}")
            self._apply_idle_control_state_after_preflight_async()
            self._sync_guide_pulse()
            return

        qenv = _qprocess_environment_from_environ_dict(
            ssh_command_environment(self._ssh_extra_env(), self._dest_sshpass_password())
        )
        self._space_timed_out = False
        proc = QProcess(self)
        self._space_process = proc
        proc.setProcessEnvironment(qenv)
        proc.setStandardInputFile(QProcess.nullDevice())
        proc.finished.connect(self._on_space_qprocess_finished)
        proc.errorOccurred.connect(self._on_space_process_error)
        timeout_ms = (ct + SSH_DF_SUBPROCESS_OVERHEAD_SEC) * 1000
        self._space_timeout_timer.start(timeout_ms)
        program, args = argv[0], argv[1:]
        proc.start(program, args)
        if not proc.waitForStarted(5000):
            self._space_timeout_timer.stop()
            err = proc.errorString()
            debug_log(
                "SPACE",
                "qprocess_start_failed",
                error=err,
                host=remote.ssh_spec(),
            )
            proc.deleteLater()
            self._space_process = None
            if self._sync_waiting_for_dest_space:
                self._abort_deferred_sync_start_after_space_error(
                    f"Could not start ssh (or sshpass): {err}"
                )
            else:
                QMessageBox.critical(
                    self,
                    "Space check",
                    f"Could not start ssh (or sshpass):\n{err}",
                )
                self._append_log(f"Space check start failed: {err}")
            self._apply_idle_control_state_after_preflight_async()
            self._sync_guide_pulse()
            return
        pid = proc.processId()
        debug_log(
            "SPACE",
            "qprocess_ssh_started",
            host=remote.ssh_spec(),
            pid=int(pid) if pid else None,
        )

    @Slot()
    def _on_space_process_timeout(self) -> None:
        proc = self._space_process
        if proc is None or proc.state() == QProcess.ProcessState.NotRunning:
            return
        self._space_timed_out = True
        debug_log(
            "SPACE",
            "qprocess_kill_timeout",
            connect_timeout_sec=self._space_connect_timeout_sec,
            overhead_sec=SSH_DF_SUBPROCESS_OVERHEAD_SEC,
        )
        proc.kill()

    @Slot(QProcess.ProcessError)
    def _on_space_process_error(self, error: QProcess.ProcessError) -> None:
        proc = self._space_process
        msg = (proc.errorString() if proc is not None else "")[:800]
        debug_log("SPACE", "qprocess_process_error", kind=int(error), message=msg)

    @Slot(int, int)
    def _on_space_qprocess_finished(self, exit_code: int, _exit_status: int) -> None:
        self._space_timeout_timer.stop()
        proc = self._space_process
        if proc is None:
            return
        self._space_process = None
        timed_out = self._space_timed_out
        self._space_timed_out = False
        out = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        err_b = bytes(proc.readAllStandardError()).decode("utf-8", errors="replace")
        proc.deleteLater()
        excerpt = (err_b or "").strip()[:1200]
        out_strip = (out or "").strip()
        if timed_out:
            debug_log(
                "SPACE",
                "qprocess_finished_timed_out",
                stderr_excerpt=excerpt[:600],
                stdout_excerpt=out_strip[:400],
            )
            self._finalize_dest_space_ui(
                None,
                "The space check timed out (network slow or server not responding).",
                True,
            )
        elif exit_code != 0:
            debug_log(
                "SPACE",
                "qprocess_finished_nonzero",
                exit_code=exit_code,
                stderr_excerpt=excerpt[:600],
                stdout_excerpt=out_strip[:400],
            )
            hint = excerpt or f"exit {exit_code}"
            self._finalize_dest_space_ui(
                None,
                (f"Remote df failed:\n{hint}" if hint else f"Remote df failed (exit {exit_code}).")[
                    :900
                ],
                True,
            )
        else:
            free_b = parse_remote_df_stdout(out)
            debug_log(
                "SPACE",
                "check_done",
                remote=True,
                transport="qprocess_ssh",
                readable=free_b is not None,
                exit_code=exit_code,
            )
            self._finalize_dest_space_ui(free_b, None, True)
        self._apply_idle_control_state_after_preflight_async()
        self._sync_guide_pulse()

    @Slot(object, object, bool)
    def _on_dest_space_worker_finished(
        self, free: object, err: object, was_remote: bool
    ) -> None:
        free_b: Optional[int] = free if isinstance(free, int) or free is None else None
        err_s = err if isinstance(err, str) else (str(err) if err else None)
        try:
            self._finalize_dest_space_ui(free_b, err_s, was_remote)
        finally:
            self._apply_idle_control_state_after_preflight_async()
            self._sync_guide_pulse()
            self._safe_qthread_quit("_space_thread")

    @Slot()
    def _on_dest_space_thread_finished(self) -> None:
        self._space_thread = None
        self._sync_guide_pulse()

    def _set_path_and_rsync_controls_enabled(self, enabled: bool) -> None:
        """Paths, rsync options, and SSH/space actions — disabled for the duration of a sync."""
        for w in (
            self._source_list,
            self._btn_add_source,
            self._btn_edit_source,
            self._btn_remove_source,
            self._dest,
            self._ssh_password,
            self._btn_edit_dest,
            self._btn_test_ssh_dest,
            self._timeout,
            self._retry,
            self._dry_run,
            self._recursive_subdirs,
            self._radio_resume_partial,
            self._radio_redo_partial,
            self._combo_existing_files,
            self._bwlimit,
            self._extra_rsync,
        ):
            w.setEnabled(enabled)

    def _get_guide_target(self) -> Optional[QPushButton]:
        """
        Linear checklist: sources → destination → start.
        """
        if self._rsync.is_syncing():
            return None
        if self._dest_space_busy():
            return None
        paths = self._source_paths_list()
        dst = self._dest.text().strip()

        if not paths:
            return self._btn_add_source

        for p in paths:
            if parse_rsync_destination(p)[0] is None:
                if not Path(p).expanduser().is_dir():
                    return self._btn_add_source

        if not dst:
            return self._btn_edit_dest

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
        busy = (
            self._rsync.is_syncing()
            or self._pending_sync_launch
            or self._sync_waiting_for_dest_space
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
        paths = self._source_paths_list()
        if not paths:
            QMessageBox.warning(self, "Sync", "Add at least one source (folder or path).")
            return False
        if not self._dest.text().strip():
            QMessageBox.warning(self, "Sync", "Set a destination path.")
            return False
        for p in paths:
            if parse_rsync_destination(p)[0] is None:
                if not Path(p).expanduser().is_dir():
                    QMessageBox.warning(
                        self,
                        "Sync",
                        f"Source directory does not exist:\n{p}",
                    )
                    return False

        if self._dry_run.isChecked():
            return True

        return True

    @Slot()
    def _start_sync(self) -> None:
        try:
            self._parsed_user_extra_args()
        except ValueError as e:
            QMessageBox.warning(self, "Extra rsync args", str(e))
            return
        if self._dest_space_busy():
            QMessageBox.information(
                self,
                "Start sync",
                "Wait for the destination space check to finish, then try again.",
            )
            return
        if not self._preflight_warnings():
            return
        if not self._dry_run.isChecked():
            dest = self._dest.text().strip()
            nsrc = len(self._source_paths_list())
            extra = (
                f"\n\n{nsrc} source folders will each appear under this destination by name."
                if nsrc > 1
                else ""
            )
            r = QMessageBox.question(
                self,
                "Start sync",
                f"Start copying to:\n{dest}{extra}\n\nThe destination will be modified (not a dry run).",
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
        self._sync_waiting_for_dest_space = True
        self._append_log("Checking destination free space before rsync…")
        self._check_dest_space()

    def _run_rsync_after_ui_tick(self) -> None:
        if not self._pending_sync_launch:
            return
        self._pending_sync_launch = False
        try:
            debug_log(
                "SYNC",
                "start_requested",
                dry_run=self._last_sync_was_dry_run,
                source_count=len(self._source_paths_list()),
            )
            paths = self._source_paths_list()
            multi = len(paths) > 1
            mods = [self._collect_rsync_modifiers_for_source(i) for i in range(len(paths))]
            self._rsync.set_environment_for_source_index(
                self._ssh_qprocess_env_for_source_index
            )
            self._rsync.configure(
                [self._source_for_sync(p, multi_source=multi) for p in paths],
                canonical_rsync_path(self._dest.text()),
                self._timeout.value(),
                self._retry.value(),
                [],
                extra_args_per_source=mods,
                recursive=self._recursive_subdirs.isChecked(),
            )
            self._rsync.start_sync_loop()
        except Exception as e:  # noqa: BLE001
            self._sync_waiting_for_dest_space = False
            self._pending_sync_launch = False
            self._set_path_and_rsync_controls_enabled(True)
            self._btn_start.setEnabled(True)
            self._btn_pause.setText("Pause")
            self._btn_pause.setEnabled(False)
            self._btn_stop.setEnabled(False)
            self._reset_sync_transfer_panel()
            self._sync_guide_pulse()
            QMessageBox.critical(self, "Sync", str(e))
            self._append_log(f"Sync setup error: {e}")
            debug_log("SYNC", "configure_failed", error=str(e))

    @Slot()
    def _stop_sync(self) -> None:
        if not self._rsync.is_syncing():
            return
        r = QMessageBox.question(
            self,
            "Stop sync",
            "Stop the running sync? The current rsync attempt will be aborted; "
            "partial files depend on your “Partial files” option and retries.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if r != QMessageBox.Yes:
            return
        debug_log("SYNC", "user_stop_clicked")
        self._rsync.stop()

    @Slot()
    def _toggle_sync_pause(self) -> None:
        if self._btn_pause.text() == "Pause":
            if self._rsync.pause_transfer():
                debug_log("SYNC", "user_pause_clicked")
                self._btn_pause.setText("Resume")
        else:
            if self._rsync.resume_transfer():
                debug_log("SYNC", "user_resume_clicked")
                self._btn_pause.setText("Pause")

    @Slot(int)
    def _on_attempt(self, n: int) -> None:
        self._sync_attempt_shown = n
        if n > 1:
            self._sync_carry_tb = None
            self._sync_left_monotone_tb = None
            self._sync_left_monotone_display = None
        debug_log("SYNC", "ui_attempt_changed", attempt=n)
        self._append_log(f"--- Attempt {n} ---")
        self._btn_pause.setText("Pause")
        if self._rsync.is_syncing():
            self._lbl_sync_detail.setText(f"ATTEMPT {n}")

    @Slot(bool)
    def _on_rsync_pause_state_changed(self, paused: bool) -> None:
        debug_log("SYNC", "pause_state_changed", paused=paused)
        self._btn_pause.setText("Resume" if paused else "Pause")

    @Slot(int, bool)
    def _on_sync_finished(self, code: int, ok: bool) -> None:
        debug_log("SYNC", "finished", exit_code=code, success=ok)
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
            self._pending_sync_snap = None
            self._sync_bar_peak = 10_000
            self._stop_sync_session_wall_clock(reset_label=False)
            self._progress.setRange(0, 10_000)
            self._progress.setValue(10_000)
            self._progress.setFormat("100.00\u0025")
            self._reset_stat_labels_idle()
            self._lbl_sync_detail.setText("")
            self._lbl_sync_path.setText("")
            self._lbl_sync_path.setToolTip("")
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
            self._reset_stat_labels_idle()
            self._lbl_sync_detail.setText("")
            self._lbl_sync_path.setText("")
            self._lbl_sync_path.setToolTip("")
            if code != 0:
                QMessageBox.warning(self, "Sync", f"Sync did not complete successfully (code {code}).")

    @Slot()
    def _on_stopped(self) -> None:
        debug_log("SYNC", "stopped_by_user_signal")
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
        self._reset_stat_labels_idle()
        self._lbl_sync_detail.setText("")
        self._lbl_sync_path.setText("")
        self._lbl_sync_path.setToolTip("")

    @Slot()
    def _check_for_update(self) -> None:
        if self._qthread_ref_running("_update_check_thread"):
            return
        debug_log("UPDATE", "ui_check_clicked")
        self._btn_check_update.setEnabled(False)
        thread = QThread(self)
        worker = GitHubUpdateCheckWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run, Qt.QueuedConnection)
        worker.finished.connect(self._on_update_check_finished, Qt.QueuedConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_update_check_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._update_check_thread = thread
        thread.start()

    @Slot(object)
    def _on_update_check_finished(self, latest: object) -> None:
        latest_s = latest if isinstance(latest, str) or latest is None else None
        if latest_s is None:
            QMessageBox.information(
                self,
                "Check for update",
                "Could not contact GitHub or read the latest release. "
                "Check your network connection and try again.",
            )
        else:
            cur = __version__
            if not is_remote_version_newer(cur, latest_s):
                QMessageBox.information(
                    self,
                    "Check for update",
                    f"You are running SafeCopi {cur}.\n\n"
                    f"The latest GitHub release is {latest_s}.\n\n"
                    "No newer version is available.",
                )
            else:
                QMessageBox.information(
                    self,
                    "Update available",
                    f"You are running SafeCopi {cur}.\n\n"
                    f"A newer GitHub release is available: {latest_s}.\n\n"
                    "Visit the project page to download the latest version:\n"
                    "https://github.com/UnDadFeated/SafeCopi",
                )
        if latest_s is None:
            debug_log("UPDATE", "result", ok=False)
        else:
            debug_log(
                "UPDATE",
                "result",
                ok=True,
                latest=latest_s,
                newer=is_remote_version_newer(__version__, latest_s),
            )
        self._safe_qthread_quit("_update_check_thread")

    @Slot()
    def _on_update_check_thread_finished(self) -> None:
        self._update_check_thread = None
        self._btn_check_update.setEnabled(True)


def run_app() -> int:
    app = QApplication([])
    app.setApplicationName("SafeCopi")
    try:
        init_debug_log()
        register_shutdown_debug_hooks()
        debug_log("APP", "startup", version=__version__)
        app.aboutToQuit.connect(lambda: debug_log("APP", "about_to_quit"))
    except Exception:
        pass
    w = MainWindow()
    w.show()
    return app.exec()
