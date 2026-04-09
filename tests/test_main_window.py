"""Qt smoke tests (requires pytest-qt and a display or offscreen platform)."""

import os
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QMessageBox

from safecopi.main_window import MainWindow
from safecopi.utils import build_rsync_command_argv


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
