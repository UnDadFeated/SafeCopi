"""Unit tests for helpers (no Qt display required)."""

from safecopi.utils import (
    build_rsync_command_argv,
    humanize_rsync_progress_stats,
    parse_extra_rsync_args,
    parse_rsync_progress2_line,
)


def test_parse_extra_rsync_args_empty() -> None:
    assert parse_extra_rsync_args("") == []
    assert parse_extra_rsync_args("  ") == []


def test_parse_extra_rsync_args_tokens() -> None:
    assert parse_extra_rsync_args("--delete --exclude=.git") == ["--delete", "--exclude=.git"]


def test_parse_extra_rsync_args_quoted() -> None:
    assert parse_extra_rsync_args('--exclude="foo bar"') == ["--exclude=foo bar"]


def test_parse_extra_rsync_args_bad_quotes() -> None:
    try:
        parse_extra_rsync_args('--exclude="unclosed')
    except ValueError as e:
        assert "quotes" in str(e).lower() or "Unbalanced" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_build_rsync_command_argv_order() -> None:
    argv = build_rsync_command_argv("/a", "b:/c", 30, ["--dry-run", "-e", "ssh -S none"])
    assert argv[0] == "rsync"
    assert "-ah" in argv
    assert "--no-inc-recursive" in argv
    assert "--timeout=30" in argv
    assert "--dry-run" in argv
    assert argv[-2] == "/a"
    assert argv[-1] == "b:/c"
    assert argv[-3] == "-v"


def test_build_rsync_command_argv_non_recursive() -> None:
    argv = build_rsync_command_argv("/src/", "dest/", 60, [], recursive=False)
    assert argv[0] == "rsync"
    assert "-hlptgoD" in argv
    assert "-ah" not in argv
    assert "--no-inc-recursive" not in argv


def test_parse_rsync_progress2_line() -> None:
    line = (
        "    0:01:23   42%  12.50MiB/s   0:10:00 "
        "(xfr#1200, to-chk=9/5000)"
    )
    s = parse_rsync_progress2_line(line)
    assert s is not None
    assert s.percent == 42
    assert s.elapsed == "0:01:23"
    assert s.speed == "12.50MiB/s"
    assert s.eta == "0:10:00"
    assert "xfr#1200" in s.stats_raw or "1200" in s.stats_raw
    assert "9" in s.stats_human and "5,000" in s.stats_human


def test_parse_rsync_progress2_line_no_paren() -> None:
    line = "  0:00:01   0%    0.00kB/s    0:00:00"
    s = parse_rsync_progress2_line(line)
    assert s is not None
    assert s.percent == 0
    assert s.stats_human == ""


def test_humanize_ir_chk() -> None:
    h = humanize_rsync_progress_stats("xfr#0, ir-chk=1000/2855")
    assert "Transfer progress #0" in h
    assert "2,855" in h
