"""Unit tests for helpers (no Qt display required)."""

from safecopi.utils import (
    build_rsync_command_argv,
    format_rsync_hms_for_display,
    human_bytes,
    humanize_rsync_progress_stats,
    parse_extra_rsync_args,
    parse_rsync_progress2_line,
    parse_rsync_transferred_amount_token,
    parse_rsync_transfer_progress_line,
    parse_rsync_xfr_count,
    should_log_rsync_stderr_line,
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
    assert "--info=name0" in argv


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


def test_parse_rsync_xfr_count() -> None:
    assert parse_rsync_xfr_count("xfr#42, to-chk=1/10") == 42
    assert parse_rsync_xfr_count("") is None
    assert parse_rsync_xfr_count("to-chk=1/10") is None


def test_parse_rsync_transferred_amount_token() -> None:
    assert parse_rsync_transferred_amount_token("32.77K") == int(32.77 * 1024)
    assert parse_rsync_transferred_amount_token("9.97M") == int(9.97 * 1024**2)
    assert parse_rsync_transferred_amount_token("12345") == 12345


def test_parse_rsync_transfer_progress_line_size_first() -> None:
    line = (
        "        206.50K   0%  165.68MB/s    0:00:00 "
        "(xfr#1, to-chk=295659/295662)"
    )
    s = parse_rsync_transfer_progress_line(line)
    assert s is not None
    assert s.percent == 0
    assert s.elapsed == "—"
    assert s.speed == "165.68MB/s"
    assert s.eta == "0:00:00"
    assert s.transferred_display == "206.50K"
    assert s.transferred_bytes == int(206.50 * 1024)
    assert "295,659" in s.stats_human or "295659" in s.stats_raw


def test_parse_rsync_transfer_progress_line_prefers_progress2() -> None:
    line = "  0:00:01   3%  1.00MiB/s   0:05:00"
    s = parse_rsync_transfer_progress_line(line)
    assert s is not None
    assert s.percent == 3
    assert s.elapsed == "0:00:01"
    assert s.transferred_bytes is None
    assert s.transferred_display is None


def test_format_rsync_hms_for_display() -> None:
    assert format_rsync_hms_for_display("0:08:28") == "00:08:28"
    assert format_rsync_hms_for_display("71:36:26") == "71:36:26"
    assert format_rsync_hms_for_display("102:05:03") == "102:05:03"
    assert format_rsync_hms_for_display("—") == "—"


def test_human_bytes_si() -> None:
    assert human_bytes(500) == "500 B"
    assert human_bytes(1500).startswith("1.50 KB")
    assert "GB" in human_bytes(5_747_000_000)
    assert "TB" in human_bytes(3 * 10**12)


def test_should_log_rsync_stderr_line() -> None:
    assert should_log_rsync_stderr_line("building file list ...")
    assert should_log_rsync_stderr_line("created 1 directory for /tmp/foo/bar")
    assert should_log_rsync_stderr_line("rsync: connection reset")
    assert not should_log_rsync_stderr_line("Photos/JC1.jpg")
    assert not should_log_rsync_stderr_line("Photos/subdir/")
    assert not should_log_rsync_stderr_line(
        "         32.77K   0%    0.00kB/s    0:00:00"
    )
