"""Qt smoke tests (requires pytest-qt and a display or offscreen platform)."""

import os

import pytest

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
