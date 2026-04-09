"""Qt smoke tests (requires pytest-qt and a display or offscreen platform)."""

import os
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QMessageBox

from safecopi.main_window import MainWindow
from safecopi.utils import RsyncProgressSnapshot, build_rsync_command_argv


@pytest.fixture
def offscreen_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


def test_main_window_construct_show_close(qtbot, offscreen_env) -> None:
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    assert w.windowTitle().startswith("SafeCopi")
    w.close()


def test_collect_rsync_modifiers_includes_bw(qtbot, offscreen_env) -> None:
    w = MainWindow()
    qtbot.addWidget(w)
    w._bwlimit.setValue(100)
    w._dry_run.setChecked(True)
    args = w._collect_rsync_modifiers()
    assert "--bwlimit=100" in args
    assert "--dry-run" in args


def test_source_path_dedup_key_local_trailing_slash(tmp_path, qtbot, offscreen_env) -> None:
    d = tmp_path / "src"
    d.mkdir()
    a = str(d)
    b = str(d) + "/"
    assert MainWindow._source_path_dedup_key(a) == MainWindow._source_path_dedup_key(b)


def test_source_for_sync_single_local_strips_trailing_slash(
    tmp_path, qtbot, offscreen_env
) -> None:
    d = tmp_path / "Sofie Backup"
    d.mkdir()
    w = MainWindow()
    qtbot.addWidget(w)
    out = w._source_for_sync(str(d) + "/")
    assert out.endswith("/Sofie Backup")
    assert not out.endswith("/Sofie Backup/")


def test_preflight_warnings_requires_destination(tmp_path, qtbot, offscreen_env) -> None:
    d = tmp_path / "src"
    d.mkdir()
    w = MainWindow()
    qtbot.addWidget(w)
    w._source_list.clear()
    w._source_list.addItem(str(d) + "/")
    w._dest.clear()
    with patch.object(QMessageBox, "warning", return_value=None) as mock_warn:
        assert w._preflight_warnings() is False
    mock_warn.assert_called_once()
    assert "destination" in mock_warn.call_args[0][2].lower()


def test_get_guide_target_browse_dest_when_dest_empty(
    tmp_path, qtbot, offscreen_env
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    w = MainWindow()
    qtbot.addWidget(w)
    w._source_list.clear()
    w._source_list.addItem(str(src) + "/")
    w._dest.clear()
    assert w._get_guide_target() is w._btn_browse_dest


def test_get_guide_target_remote_source_goes_to_ssh(
    qtbot, offscreen_env,
) -> None:
    w = MainWindow()
    qtbot.addWidget(w)
    w._source_list.clear()
    w._source_list.addItem("user@example.com:/data/")
    w._dest.setText("/mnt/backup/")
    w._ssh_ok_this_session = False
    assert w._get_guide_target() is w._btn_ssh


def test_get_guide_target_start_after_dest_when_local_ready(
    tmp_path, qtbot, offscreen_env
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    w = MainWindow()
    qtbot.addWidget(w)
    w._source_list.clear()
    w._source_list.addItem(str(src) + "/")
    w._dest.setText("/tmp/dest/")
    w._ssh_ok_this_session = True
    assert w._get_guide_target() is w._btn_start


def test_get_guide_target_mixed_local_remote_points_remove(
    tmp_path, qtbot, offscreen_env
) -> None:
    local = tmp_path / "a"
    local.mkdir()
    d = MainWindow()
    qtbot.addWidget(d)
    d._source_list.clear()
    d._source_list.addItem(str(local) + "/")
    d._source_list.addItem("user@example.com:/remote/")
    d._dest.setText("user@example.com:/dest/")
    assert d._get_guide_target() is d._btn_remove_source


def test_recursive_subdirs_default_on(qtbot, offscreen_env) -> None:
    w = MainWindow()
    qtbot.addWidget(w)
    assert w._recursive_subdirs.isChecked()
    argv = build_rsync_command_argv(
        "/a",
        "b:/c",
        30,
        w._collect_rsync_modifiers(),
        recursive=w._recursive_subdirs.isChecked(),
    )
    assert "-ah" in argv
    w._recursive_subdirs.setChecked(False)
    argv_flat = build_rsync_command_argv(
        "/a",
        "b:/c",
        30,
        w._collect_rsync_modifiers(),
        recursive=w._recursive_subdirs.isChecked(),
    )
    assert "-hlptgoD" in argv_flat


def test_sync_transfer_bar_units_follows_rsync_percent(
    qtbot, offscreen_env
) -> None:
    w = MainWindow()
    qtbot.addWidget(w)
    snap = RsyncProgressSnapshot(
        percent=60,
        elapsed="0:00:10",
        speed="10.00MiB/s",
        eta="0:00:04",
        stats_raw="",
        stats_human="",
        transferred_bytes=100,
    )
    assert w._sync_transfer_bar_units(snap) == 6000


def test_multi_source_progress_weighted_across_sources(qtbot, offscreen_env) -> None:
    w = MainWindow()
    qtbot.addWidget(w)
    w._sync_total_source_runs = 2
    w._on_rsync_source_run_changed(1, 2)

    snap1 = RsyncProgressSnapshot(
        percent=100,
        elapsed="0:00:10",
        speed="10.00MiB/s",
        eta="0:00:00",
        stats_raw="",
        stats_human="",
        transferred_bytes=100,
    )
    assert w._sync_transfer_bar_units(snap1) == 5000

    w._on_rsync_source_run_changed(2, 2)
    snap2 = RsyncProgressSnapshot(
        percent=50,
        elapsed="0:00:20",
        speed="10.00MiB/s",
        eta="0:00:10",
        stats_raw="",
        stats_human="",
        transferred_bytes=20,
    )
    assert w._sync_transfer_bar_units(snap2) == 7500


def test_check_dest_space_local_updates_label(tmp_path, qtbot, offscreen_env) -> None:
    d = tmp_path / "dest"
    d.mkdir()
    w = MainWindow()
    qtbot.addWidget(w)
    w._dest.setText(str(d) + "/")
    w._check_dest_space()
    qtbot.waitUntil(lambda: not w._dest_space_busy(), timeout=5000)
    t = w._lbl_stat_dest_free.text()
    assert t not in ("—", "— (unreadable)")


def test_preview_single_source_shows_parent_folder_copy(tmp_path, qtbot, offscreen_env) -> None:
    d = tmp_path / "Sofie Backup"
    d.mkdir()
    w = MainWindow()
    qtbot.addWidget(w)
    w._source_list.clear()
    w._source_list.addItem(str(d) + "/")
    w._dest.setText(str(tmp_path / "dest") + "/")
    w._refresh_rsync_preview()
    preview = w._rsync_preview.toPlainText()
    assert str(d) in preview
    assert (str(d) + "/") not in preview
