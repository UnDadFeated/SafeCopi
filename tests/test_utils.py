"""Unit tests for helpers (no Qt display required)."""

import pytest

from safecopi.utils import (
    EXISTING_FILES_MODE_DEFAULT,
    EXTRA_RSYNC_ARG_COUNT_MAX,
    EXTRA_RSYNC_ARG_LINE_MAX_CHARS,
    RemoteTarget,
    RSYNC_RECEIVER_TEMP_SUBDIR,
    build_rsync_command_argv,
    build_ssh_command_argv,
    canonical_rsync_path,
    existing_files_mode_rsync_argv,
    format_rsync_hms_for_display,
    format_seconds_as_hms_display,
    human_bytes,
    humanize_rsync_progress_stats,
    is_rsync_filename_only_stderr_line,
    local_free_bytes,
    normalize_existing_files_mode,
    parse_extra_rsync_args,
    clamp_monotonic_data_left_bytes,
    estimate_rsync_total_bytes_from_progress,
    parse_rsync_destination,
    parse_rsync_eta_token_to_seconds,
    parse_rsync_progress2_line,
    parse_rsync_queue_remaining_total,
    parse_rsync_speed_to_bytes_per_sec,
    parse_rsync_transferred_amount_token,
    parse_rsync_transfer_progress_line,
    parse_rsync_xfr_count,
    remote_rsync_uri_strip_trailing_slashes,
    should_log_rsync_stderr_line,
    ssh_command_environment,
    ensure_local_rsync_receiver_temp_dir,
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


def test_normalize_existing_files_mode() -> None:
    assert EXISTING_FILES_MODE_DEFAULT == "skip_name_size"
    assert normalize_existing_files_mode(None) == "skip_name_size"
    assert normalize_existing_files_mode("") == "skip_name_size"
    assert normalize_existing_files_mode("nope") == "skip_name_size"
    assert normalize_existing_files_mode("skip_name_size") == "skip_name_size"
    assert normalize_existing_files_mode("ignore_existing") == "skip_name"
    assert normalize_existing_files_mode("default") == "skip_name_size"
    assert normalize_existing_files_mode("overwrite") == "overwrite"


def test_existing_files_mode_rsync_argv() -> None:
    assert existing_files_mode_rsync_argv("overwrite") == []
    assert existing_files_mode_rsync_argv("skip_name_size") == ["--size-only"]
    assert existing_files_mode_rsync_argv("skip_name") == ["--ignore-existing"]


def test_parse_rsync_destination_sftp_url() -> None:
    r, path = parse_rsync_destination("sftp://alice@files.example.com/var/www")
    assert r is not None
    assert r.user == "alice"
    assert r.host == "files.example.com"
    assert path == "/var/www"
    assert r.to_rsync_uri() == "alice@files.example.com:/var/www"


def test_parse_rsync_destination_sftp_url_no_user() -> None:
    r, path = parse_rsync_destination("sftp://backup.host/data")
    assert r is not None
    assert r.user is None
    assert r.host == "backup.host"
    assert path == "/data"


def test_canonical_rsync_path_sftp_matches_to_rsync_uri() -> None:
    u = "sftp://u@h.example/mnt/backup/"
    assert canonical_rsync_path(u) == "u@h.example:/mnt/backup/"


def test_canonical_rsync_path_local_unchanged() -> None:
    assert canonical_rsync_path("  /home/me/foo  ") == "/home/me/foo"


def test_remote_rsync_uri_strip_trailing_slashes() -> None:
    u = "alice@nas:/mnt/media_hdd/Backup/Macie Backup/"
    assert remote_rsync_uri_strip_trailing_slashes(u) == (
        "alice@nas:/mnt/media_hdd/Backup/Macie Backup"
    )
    assert remote_rsync_uri_strip_trailing_slashes(
        "alice@nas:/mnt/media_hdd/Backup/Macie Backup"
    ) == ("alice@nas:/mnt/media_hdd/Backup/Macie Backup")
    assert remote_rsync_uri_strip_trailing_slashes("alice@nas:/") == "alice@nas:/"


def test_build_ssh_command_argv_echo_ok() -> None:
    r = RemoteTarget(host="example.com", path="/data", user="me")
    argv = build_ssh_command_argv(
        r,
        "echo ok",
        connect_timeout=11,
        batch_mode=False,
        password_for_sshpass=None,
    )
    assert argv[0] == "ssh"
    assert "me@example.com" in argv
    assert argv[-1] == "echo ok"


def test_ssh_command_environment_merges_extra_without_askpass_when_sshpass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sshpass path must not apply GUI askpass keys from extra_env."""
    monkeypatch.delenv("SSH_ASKPASS", raising=False)
    monkeypatch.delenv("SSH_ASKPASS_REQUIRE", raising=False)
    env = ssh_command_environment(
        {"SSH_ASKPASS": "/fake/askpass", "SSH_ASKPASS_REQUIRE": "force", "FOO": "bar"},
        password_for_sshpass="secret",
    )
    assert env["SSHPASS"] == "secret"
    assert env["FOO"] == "bar"
    assert "SSH_ASKPASS" not in env
    assert "SSH_ASKPASS_REQUIRE" not in env


def test_ssh_command_environment_updates_extra_when_no_sshpass() -> None:
    env = ssh_command_environment(
        {"SSH_ASKPASS": "/x", "SSH_ASKPASS_REQUIRE": "force"},
        password_for_sshpass=None,
    )
    assert env["SSH_ASKPASS"] == "/x"
    assert env["SSH_ASKPASS_REQUIRE"] == "force"


def test_parse_extra_rsync_args_line_too_long() -> None:
    with pytest.raises(ValueError, match="exceed"):
        parse_extra_rsync_args("x" * (EXTRA_RSYNC_ARG_LINE_MAX_CHARS + 1))


def test_parse_extra_rsync_args_too_many_tokens() -> None:
    with pytest.raises(ValueError, match="exceed"):
        parse_extra_rsync_args(" ".join("x" for _ in range(EXTRA_RSYNC_ARG_COUNT_MAX + 1)))


def test_ensure_local_rsync_receiver_temp_dir_creates_subdir(tmp_path) -> None:
    dest = str(tmp_path / "backup") + "/"
    ensure_local_rsync_receiver_temp_dir(dest)
    assert (tmp_path / "backup" / RSYNC_RECEIVER_TEMP_SUBDIR).is_dir()


def test_build_rsync_command_argv_order() -> None:
    argv = build_rsync_command_argv("/a", "b:/c", 30, ["--dry-run", "-e", "ssh -S none"])
    assert argv[0] == "rsync"
    assert "-ah" in argv
    assert "--no-inc-recursive" in argv
    assert "--temp-dir=/tmp" in argv
    assert f"--temp-dir={RSYNC_RECEIVER_TEMP_SUBDIR}" not in argv
    assert "--timeout=30" in argv
    assert "--dry-run" in argv
    assert argv[-2] == "/a"
    assert argv[-1] == "b:/c"
    assert argv[-3] == "-v"
    assert "--info=name0" not in argv


def test_build_rsync_command_argv_caps_timeout() -> None:
    argv = build_rsync_command_argv("/a", "b:/c", 999_999, [])
    assert "--timeout=86400" in argv


def test_build_rsync_command_argv_bad_timeout_defaults() -> None:
    argv = build_rsync_command_argv("/a", "b:/c", "not-int", [])
    assert "--timeout=60" in argv


def test_build_rsync_command_argv_non_recursive() -> None:
    argv = build_rsync_command_argv("/src/", "dest/", 60, [], recursive=False)
    assert argv[0] == "rsync"
    assert "-hlptgoD" in argv
    assert "-ah" not in argv
    assert "--no-inc-recursive" not in argv
    assert f"--temp-dir={RSYNC_RECEIVER_TEMP_SUBDIR}" in argv
    assert f"--filter=protect {RSYNC_RECEIVER_TEMP_SUBDIR}/" in argv


def test_build_rsync_local_dest_uses_receiver_subdir_not_tmp() -> None:
    argv = build_rsync_command_argv("/src/", "/mnt/nas/", 60, [])
    assert f"--temp-dir={RSYNC_RECEIVER_TEMP_SUBDIR}" in argv
    assert "--temp-dir=/tmp" not in argv


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


def test_parse_rsync_queue_remaining_total() -> None:
    assert parse_rsync_queue_remaining_total("xfr#1, to-chk=295659/295662") == (
        295_659,
        295_662,
    )
    assert parse_rsync_queue_remaining_total("IR-CHK=10/200") == (10, 200)
    assert parse_rsync_queue_remaining_total("") is None


def test_parse_rsync_eta_token_to_seconds() -> None:
    assert parse_rsync_eta_token_to_seconds("0:03:30") == pytest.approx(210.0)
    assert parse_rsync_eta_token_to_seconds("34:08:53") == pytest.approx(34 * 3600 + 8 * 60 + 53)
    assert parse_rsync_eta_token_to_seconds("—") is None
    assert parse_rsync_eta_token_to_seconds("") is None
    assert parse_rsync_eta_token_to_seconds("not a time") is None


def test_estimate_rsync_total_bytes_from_progress() -> None:
    assert estimate_rsync_total_bytes_from_progress(50, 50) == 100
    assert estimate_rsync_total_bytes_from_progress(100, 100) == 100
    assert estimate_rsync_total_bytes_from_progress(100, 0) is None
    assert estimate_rsync_total_bytes_from_progress(None, 50) is None


def test_clamp_monotonic_data_left_bytes() -> None:
    assert clamp_monotonic_data_left_bytes(None, 100, 0, 50) == (None, None, None)
    assert clamp_monotonic_data_left_bytes(900, 100, None, None) == (900, 100, 900)
    assert clamp_monotonic_data_left_bytes(500, 200, 100, 900) == (500, 200, 500)
    assert clamp_monotonic_data_left_bytes(800, 200, 100, 900) == (800, 200, 800)
    assert clamp_monotonic_data_left_bytes(950, 200, 100, 900) == (800, 200, 800)
    assert clamp_monotonic_data_left_bytes(700, 50, 200, 400) == (700, 50, 700)


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


def test_local_free_bytes_existing_path_returns_value(tmp_path) -> None:
    d = tmp_path / "free-check"
    d.mkdir()
    free = local_free_bytes(str(d), timeout_sec=5)
    assert isinstance(free, int)
    assert free > 0


def test_format_rsync_hms_for_display() -> None:
    assert format_rsync_hms_for_display("0:08:28") == "00:08:28"
    assert format_rsync_hms_for_display("71:36:26") == "71:36:26"
    assert format_rsync_hms_for_display("102:05:03") == "102:05:03"
    assert format_rsync_hms_for_display("—") == "—"


def test_format_seconds_as_hms_display() -> None:
    assert format_seconds_as_hms_display(8 * 60 + 28) == "00:08:28"
    assert format_seconds_as_hms_display(71 * 3600 + 36 * 60 + 26) == "71:36:26"


def test_parse_rsync_speed_to_bytes_per_sec() -> None:
    assert parse_rsync_speed_to_bytes_per_sec("12.50MiB/s") == pytest.approx(12.50 * 1024**2)
    assert parse_rsync_speed_to_bytes_per_sec("165.68MB/s") == pytest.approx(165.68 * 1024**2)
    assert parse_rsync_speed_to_bytes_per_sec("0.00kB/s") == 0.0
    assert parse_rsync_speed_to_bytes_per_sec("1.00MiB/s") == pytest.approx(1024**2)


def test_human_bytes_si() -> None:
    assert human_bytes(500) == "500 B"
    assert human_bytes(1500).startswith("1.50 KB")
    assert "GB" in human_bytes(5_747_000_000)
    assert "TB" in human_bytes(3 * 10**12)


def test_is_rsync_filename_only_stderr_line() -> None:
    assert is_rsync_filename_only_stderr_line("Photos/JC1.jpg")
    assert is_rsync_filename_only_stderr_line("Makefile")
    assert is_rsync_filename_only_stderr_line(">f+++++++++ deep/file.xyz")
    assert is_rsync_filename_only_stderr_line("dir/with space/name.txt")
    # Path segments must not be confused with substring needles (deleting, error, auth, …).
    assert is_rsync_filename_only_stderr_line("archive/deleting/old/img.jpg")
    assert is_rsync_filename_only_stderr_line("logs/error/2024/debug.txt")
    assert is_rsync_filename_only_stderr_line("etc/auth/tokens/secret.db")
    assert not is_rsync_filename_only_stderr_line("rsync: foo")
    assert not is_rsync_filename_only_stderr_line(
        "        206.50K   0%  165.68MB/s    0:00:00 (xfr#1, to-chk=1/2)"
    )
    assert not is_rsync_filename_only_stderr_line("plain english sentence here")


def test_should_log_rsync_stderr_line() -> None:
    assert not should_log_rsync_stderr_line("building file list ...")
    assert not should_log_rsync_stderr_line("created 1 directory for /tmp/foo/bar")
    assert should_log_rsync_stderr_line("rsync: connection reset")
    assert should_log_rsync_stderr_line("rsync: warning: skipping symlink")
    assert should_log_rsync_stderr_line("Permission denied (publickey).")
    assert not should_log_rsync_stderr_line("Photos/JC1.jpg")
    assert not should_log_rsync_stderr_line("Photos/subdir/")
    assert not should_log_rsync_stderr_line("archive/deleting/old/img.jpg")
    assert not should_log_rsync_stderr_line(
        "         32.77K   0%    0.00kB/s    0:00:00"
    )
    assert not should_log_rsync_stderr_line("Some innocuous status line without markers.")
