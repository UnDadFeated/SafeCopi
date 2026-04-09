"""Tests for append-only ``debug.log`` diagnostics."""

from pathlib import Path

import pytest

import safecopi.debug_log as dl


@pytest.fixture
def isolated_debug_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect debug log to a temp directory (no real ``~/.config`` writes)."""
    log_dir = tmp_path / "SafeCopi"
    log_dir.mkdir(parents=True)

    def _fake_resolve() -> Path:
        return log_dir

    monkeypatch.setattr(dl, "_log_path", None)
    monkeypatch.setattr(dl, "_session_id", None)
    monkeypatch.setattr(dl, "_resolve_log_dir", _fake_resolve)
    dl.init_debug_log()
    return log_dir / "debug.log"


def test_debug_log_file_is_lowercase(isolated_debug_log: Path) -> None:
    assert isolated_debug_log.name == "debug.log"


def test_debug_log_session_and_event(isolated_debug_log: Path) -> None:
    dl.debug_log("TEST", "ping", n=1)
    text = isolated_debug_log.read_text(encoding="utf-8")
    assert "SESSION" in text and "start" in text
    assert "[TEST]" in text and "ping" in text and '"n": 1' in text


def test_debug_log_rotates_to_last_three_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_dir = tmp_path / "SafeCopi"
    log_dir.mkdir(parents=True)
    p = log_dir / "debug.log"
    blocks = []
    for i in range(4):
        blocks.append(
            f"2026-04-08T12:00:0{i}.000 [SESSION] start {{\"id\": \"run{i}\"}}\nrow{i}\n"
        )
    p.write_text("".join(blocks), encoding="utf-8")
    monkeypatch.setattr(dl, "_log_path", None)
    monkeypatch.setattr(dl, "_session_id", None)
    monkeypatch.setattr(dl, "_resolve_log_dir", lambda: log_dir)
    dl.init_debug_log()
    text = p.read_text(encoding="utf-8")
    assert "run0" not in text
    assert "run1" in text and "run2" in text and "run3" in text
    assert "[LOG] rotated" in text
