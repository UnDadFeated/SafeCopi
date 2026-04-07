# Changelog

## [1.4.1] - 2026-04-06

### Changed

- **Rsync stderr**: pass `--info=name0` so rsync does not emit one line per transferred path; overall `progress2` and important messages remain.
- **Transfer progress parsing**: `parse_rsync_transfer_progress_line()` accepts both standard `progress2` lines (elapsed-first) and the common `SIZE  PCT  SPEED  ETA` lines that follow a filename when `-v` is used.
- **Activity log**: `RsyncWorker` emits log lines only when `should_log_rsync_stderr_line()` allows—paths and progress-looking noise are dropped after driving the progress bar.

## [1.4.0] - 2026-04-06

### Added

- **Include subdirectories (recursive)** checkbox under Rsync options (default on). When enabled, behavior matches the previous `-ah` full-tree copy. When disabled, rsync uses `-hlptgoD` (archive-style preservation without `-r`) so only the top directory level is transferred. Setting is persisted in `QSettings` as `recursive_subdirs`.

## [1.3.4] - 2026-04-06

### Fixed

- **RsyncWorker**: avoid blocking the GUI thread for up to five seconds after `kill()`; clear `QProcess` reference, block signals, disconnect slots, then `deleteLater()` when replacing or finishing a process.
- **Settings load**: `io_timeout`, `retry_delay`, and `bwlimit` from `QSettings` are parsed safely and clamped to spinbox ranges so corrupt values do not raise at startup.
- **Save log**: `Path.write_text` failures surface as a warning dialog instead of an unhandled exception.
- **Source scan UI**: when a scan finishes or fails, the coalesced progress timer is stopped and pending file/size labels are flushed once so the last in-progress values are not stuck.
- **Sync start**: failures while building SSH/`QProcess` environment or starting the rsync loop restore Start/Stop controls and show an error dialog.

## [1.3.3] - 2026-04-06

### Fixed

- Source scan worker teardown: `QThread.finished` + `worker.deleteLater` queued deletion on the worker thread after its event loop had stopped, producing `QObject: shared QObject was deleted directly` and crashes (e.g. SIGBUS). `SourceScanWorker.run()` now moves the worker back to the GUI thread in a `finally` block before the thread exits, so `deleteLater` runs on a live event loop.

## [1.3.2] - 2026-04-06

### Changed

- **Paths**: removed the redundant **Remote SSH** checkbox; remote `user@host:/path` destinations always use password-capable SSH (GUI/`sshpass` or keys when the password field is empty). Updated SSH password placeholder and tooltips.
- **Preflight / Scan** row shows a **—** placeholder beside the scan bar when idle; during a scan the bar replaces it so the column aligns with other preflight values.
- **Rsync delays**: `QSpinBox` uses dedicated styling (min height/width, visible up/down areas) so controls read as spin boxes, not thin sliders.
- **Density**: slightly tighter root and group-box margins; **Command preview** height reduced with scrollbars only when needed.

## [1.3.1] - 2026-04-06

### Added

- **File transfer** panel during sync: larger progress bar with percent label, headline line (**% complete · throughput · ETA**), and detail line (**elapsed time**, human-readable **verify/scan counters** from rsync’s parenthetical stats, **attempt** number). Parses full `rsync --info=progress2` lines via `RsyncProgressSnapshot` / `parse_rsync_progress2_line()` in `utils`.

### Changed

- UI updates for live transfer metrics are **coalesced** (~80 ms) to avoid label flicker while keeping the bar responsive.

## [1.3.0] - 2026-04-06

### Added

- **Settings persistence** (`QSettings`): source, destination, timeouts, dry-run, partial mode, bandwidth limit, extra rsync args. SSH password is not stored.
- **Copy log** and **Save log…** actions above the log view.
- **Rsync command preview** (read-only) built from the same argv list as the worker (`build_rsync_command_argv` in `utils`).
- **Bandwidth limit** (`--bwlimit`, KiB/s; 0 = off) and **Extra args** line (POSIX `shlex.split`; validated before sync).
- **Confirmation dialog** before a non-dry-run sync; **dry-run completion** shows exit code and the last log lines.
- **Quit guard**: close is blocked while a source scan or rsync run is active (finish or stop first).
- **requirements-dev.txt** with `pytest` / `pytest-qt` and **tests/** for utils and a main-window smoke test.

### Changed

- **Rsync argv construction** deduplicated: `RsyncWorker` and the UI preview both use `build_rsync_command_argv()`; `parse_extra_rsync_args()` centralizes user flag parsing.
- Destination parsing consolidated to **`_parsed_destination()`**; **`_ssh_password_plain()`** replaces repeated password reads.

### Fixed

- Source scan worker signals are **explicitly disconnected** from the main window when a scan finishes or fails, reducing risk of stray deliveries during teardown.

## [1.2.5] - 2026-04-06

### Changed

- Tighter, more compact main window layout: reduced group box padding and margins, smaller base font, denser form spacing with right-aligned labels, combined I/O and retry spinboxes on one row, partial-transfer radios on one row, shorter control labels, and `min-width: 0` on buttons so labels like **Dest. space** are not clipped.
- Window size **720×760** (was 780×830). Scan phase text moved to the scan bar **tooltip** only; the scan row is a single progress bar.
- README: scan progress cadence text aligned with current defaults (250 files / 200 ms).

## [1.2.4] - 2026-04-06

### Added

- Destination field drives **Remote SSH** automatically: when the URI matches `user@host:/path`, the GUI/password SSH mode is checked; local paths turn it off. Password field unchanged; checkbox remains for key-only overrides.

### Changed

- **Scan progress** row no longer repeats file count and size beside the bar (those values stay in the Preflight rows). The optional label only shows the worker phase line while visible.

### Fixed

- Crash (`QObject::killTimer: Timers cannot be stopped from another thread`, possible SIGSEGV) after source scan: `finished` / `failed` used nested Python callables with `QueuedConnection`, which could run on the worker thread and call `QTimer.stop()` off the GUI thread. Completion handlers are now `MainWindow` slots so timer and widgets are touched only on the main thread.

## [1.2.3] - 2026-04-06

### Fixed

- Source scan: `QThread.started` was connected with a lambda calling `run()`, which can execute on the GUI thread and block the event loop so the phase label stayed at “Starting…” until the walk finished. The worker now exposes `prepare_source()` plus a `@Slot()` `run()` connected directly so the walk runs on the worker thread and progress updates apply live.

## [1.2.2] - 2026-04-06

### Added

- **Stop scan** control during source tree scan; walk cooperatively exits on request and logs partial file count and byte total.

### Fixed

- **Scan source** UI responsiveness: coalesce progress updates on a short timer and lower default progress emit frequency in `scan_source_tree_stats` so the main thread is not flooded with queued signal deliveries. Scan-related signals use explicit queued connections from the worker thread.

## [1.2.1] - 2026-04-06

### Fixed

- Source scan progress signal: `Signal(int, int)` used 32-bit Qt integers; summed bytes above ~2 GiB overflowed. Switched to `Signal(object, object)` for full Python `int` range.

## [1.2.0] - 2026-04-06

### Changed

- **Source scan**: single `os.walk` pass that counts files and **sums sizes** per file (`os.path.getsize`), with progress emitted every **~50 files** or **~80 ms** so network-mounted sources feel alive. Removed the blocking **`du`** phase that left the bar idle on slow mounts.
- New helper `scan_source_tree_stats()`; `count_files_local()` remains for other use.

## [1.1.3] - 2026-04-06

### Added

- Source scan: indeterminate **Scan progress** bar, phase labels (`du` vs counting files), live **file count** updates (every 2,500 files), and **size** as soon as `du` completes.
- `count_files_local(..., progress_every=..., on_progress=...)` for incremental counting.

## [1.1.2] - 2026-04-06

### Fixed

- Remote free space: run `df` on the destination path and each parent directory until one exists, so checks work before `rsync --mkpath` has created the remote folder. Parse `df` data lines without treating the header as disk stats.

## [1.1.1] - 2026-04-06

### Changed

- If `sshpass` is missing but the optional password field is filled, log a note and fall back to GUI prompts instead of blocking Test SSH / Sync.

## [1.1.0] - 2026-04-06

### Added

- **SSH password (optional)** field: when `sshpass` is installed, the password is supplied via `sshpass -e` (no popup). Leave empty to keep using `SSH_ASKPASS` dialogs.
- Clearer SSH test failure hints when the server returns “please try again” or `Permission denied (publickey,password)`.

### Changed

- Interactive `ssh` options: `GSSAPIAuthentication=no`, explicit `PasswordAuthentication` / `KbdInteractiveAuthentication`, `NumberOfPasswordPrompts=6`.
- `run_ssh_command` / `remote_df` / `rsync -e` support optional `password_for_sshpass` for `sshpass`.

## [1.0.8] - 2026-04-06

### Added

- Partial transfer policy: **Resume (--partial)** vs **Re-copy interrupted files (no --partial)** radio options.
- `rsync -e` uses `ssh_extra_argv(..., for_rsync=True)` so password mode matches preflight (`PubkeyAuthentication=no`, etc.) while **not** adding `BatchMode` on the rsync transport (avoids blocking key passphrases / agent quirks).

### Fixed

- Password GUI: direct `ssh` and `rsync`’s `ssh` no longer try public keys first when interactive mode is enabled, so authentication can reach password / keyboard-interactive and invoke `SSH_ASKPASS`.

## [1.0.7] - 2026-04-06

### Changed

- `bootstrap`: `pip install` runs with `-q` to reduce noise when dependencies are already satisfied.

## [1.0.6] - 2026-04-06

### Fixed

- SSH password GUI: `ssh` and `rsync` no longer inherit a controlling TTY on stdin (use `/dev/null`), so OpenSSH invokes `SSH_ASKPASS` instead of reading a password from an invisible shell when SafeCopi is started from a terminal.
- Set `SSH_ASKPASS_REQUIRE=force` for interactive mode so askpass is used consistently with recent OpenSSH.

## [1.0.5] - 2026-04-06

### Added

- **SSH password prompt** checkbox: optional GUI password entry via `SSH_ASKPASS` (`safecopi/ssh_askpass.py`) and disabling `BatchMode` for `ssh` subprocesses and `rsync` transport.
- `ensure_ssh_askpass_wrapper()` builds a small launcher script so the askpass helper uses the same Python environment as SafeCopi.

## [1.0.4] - 2026-04-06

### Added

- `bootstrap` script to create `.venv` and install dependencies without using shell activation.
- Comments in `requirements.txt` warning fish users not to `source` the bash `activate` script.

### Changed

- README: one-line quick start for fish-compatible setup.

## [1.0.3] - 2026-04-06

### Added

- `run-safecopi` launcher script that invokes `.venv/bin/python` without shell activation.

### Changed

- README: stronger Fish and PEP 668 guidance; install/run blocks avoid `source` / bare `pip`.

## [1.0.2] - 2026-04-06

### Fixed

- `RsyncWorker` now accepts an optional Qt parent so `RsyncWorker(self)` from the main window works.

### Changed

- Documentation: install and run examples use `.venv/bin/pip` and `.venv/bin/python` (no shell activation required); added fish shell note for `activate.fish`.

## [1.0.1] - 2026-04-06

### Added

- PySide6 main window with fixed dimensions, dark styling, and monospace log.
- Resilient `rsync` loop with configurable I/O timeout and retry delay; cancelable wait between retries.
- Preflight: SSH connectivity test (`BatchMode`), remote `df` free space, local `shutil`/`df` free space, source scan (`du` + file count).
- Options: `--dry-run`, `--partial`; progress bar driven by `--info=progress2` parsing.
- Package entry point: `python -m safecopi`.

### Fixed

- Sync activity tracked with `RsyncWorker.is_syncing()` instead of inspecting internal process state.
- Local free-space helper no longer creates missing destination directories when probing paths.
- Stop during the retry delay cancels the pending timer so the UI does not sit idle until the next attempt.
