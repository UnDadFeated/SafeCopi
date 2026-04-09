# Changelog

## [1.8.3] - 2026-04-09

### Changed

- **Layout**: **Command preview** sits above **File transfer**. In **File transfer**, **Session elapsed** is directly under **TRANSFER STATS**; **ATTEMPT** / **CURRENT PATH** follow below.

## [1.8.2] - 2026-04-09

### Changed

- **Data remaining**: When percent- and queue-based estimates are unavailable, derive a bounded remainder from rsync **speed × ETA** (`parse_rsync_eta_token_to_seconds`). Queue extrapolation now waits for enough completed `to-chk` slots and caps implied totals to avoid wild figures.

### Removed

- **Dead code**: Unused `bytes_from_du_path` import; redundant `_extra_rsync_args()` wrapper (call sites use `_collect_rsync_modifiers()` directly).

### Fixed

- **Space check**: Skip local free-space probe when the destination path is empty (avoids pointless worker work).

## [1.8.1] - 2026-04-09

### Changed

- **File transfer / path area**: Removed the rsync elapsed line (often `—` or misleading on size-first progress). The block above **CURRENT PATH** now shows only **`ATTEMPT n`**, styled like the path header; the label updates immediately when the retry counter changes.

### Fixed

- **Removed `Dest. space` button** (leftover references): Internal space checks no longer touch a missing widget; setup guide no longer points at that control.

## [1.8.0] - 2026-04-09

### Changed

- **Preflight source scan removed**: The main window no longer walks local trees before sync. Transfer totals, queue depth, and remaining work are derived from rsync progress (`--info=progress2`): estimated total size from bytes + percent, file queue from `to-chk` / `ir-chk`, and data remaining from that estimate.
- **Start sync / destination space**: Starting a sync (including dry run) runs a destination free-space check first, then launches rsync on success. Manual **Dest. space** still updates the same on-screen free-space value.
- **File transfer layout**: The former Preflight column is removed; **File transfer** spans the full width. The progress bar shows **percent only**; a grid under the bar shows total size (estimate), files in queue, data remaining, speed, ETA, and destination free space.
- **Transfer detail line**: Shows rsync elapsed time and attempt, with three blank lines before the current path (when present), without duplicating stats already shown under the bar.

### Removed

- **`SourceScanWorker`** and **Scan source** / **Stop scan** UI; `scan_source_tree_stats` remains in `utils` for non-GUI use.

## [1.7.18] - 2026-04-09

### Fixed

- **Transfer detail byte display**: When a preflight scan total exists, the detail line’s byte figure is capped to that total even on the fallback path (so rsync’s counter cannot appear above the scan if `done_b` were ever unset).

## [1.7.17] - 2026-04-09

### Fixed

- **Transfer bytes vs preflight scan**: When a source scan total exists, effective progress bytes are capped at that total for the bar, “sent” text, and detail line. rsync’s cumulative counter can legitimately exceed the scan (e.g. files that grew after the scan); the UI no longer shows a higher “attempt” byte total than the scan without explanation. A tooltip on the detail line appears when rsync’s raw counter is above the scan.

## [1.7.16] - 2026-04-09

### Changed

- **Preflight / File transfer layout**: Preflight group box minimum height set to **132** to match File transfer.

## [1.7.15] - 2026-04-09

### Changed

- **Preflight / File transfer layout**: Removed the in-Preflight trailing-slash note. Tightened vertical spacing so **Session elapsed** sits closer to the progress bar and the transfer detail line sits flush under it; the detail area keeps a minimum height for three wrapped lines. Slightly reduced minimum heights of the Preflight and File transfer group boxes.

## [1.7.14] - 2026-04-09

### Fixed

- **Single-source destination layout**: Single local-source sync now follows the same destination behavior as multi-source runs by copying the selected parent folder into the destination (instead of copying only folder contents when the source path has a trailing slash).

## [1.7.13] - 2026-04-09

### Changed

- **README (end-user clarity)**: Documentation was rewritten in plain language for non-technical users, with a simpler "what it does / quick start / helpful notes" structure and reduced protocol-level detail.

## [1.7.12] - 2026-04-09

### Changed

- **Project positioning/documentation**: Repository-facing description and README were rewritten to prioritize SafeCopi's core purpose: resilient, safe rsync transfers for unstable or unreliable network connections (dropouts, intermittent SSH routes, and long-running backup sessions).

## [1.7.11] - 2026-04-09

### Fixed

- **Dest. space (local)**: Local destination free-space checks now schedule the worker `run` slot through an explicit queued `QMetaObject.invokeMethod(...)` call at thread start, addressing environments where `QThread.started` wiring did not execute the worker slot (observed as `SPACE ui_check_start` without `SPACE check_start` in `debug.log`).
- **Local free-space timeout behavior**: `local_free_bytes(..., timeout_sec=...)` no longer blocks on executor shutdown after timeout; timed-out probes return promptly without waiting on a potentially stuck filesystem call.

## [1.7.10] - 2026-04-09

### Fixed

- **Dest. space (local)**: Local free-space checks now start the worker slot using a queued thread-start connection, ensuring the local destination query runs reliably and the **Destination free** field is populated for local paths on all supported platforms.

## [1.7.9] - 2026-04-09

### Fixed

- **Multi-source transfer progress**: The File transfer progress bar no longer resets between sequential rsync runs when several source folders are selected. Progress now advances cumulatively from 0% to 100% for the full session, carrying completed work forward across source boundaries while preserving scan-based ETA/remaining calculations.

## [1.7.8] - 2026-04-08

### Changed

- **Main window layout**: Preflight/File transfer row adjusted to a **30% / 70%** horizontal split while preserving matched minimum heights.

## [1.7.7] - 2026-04-08

### Changed

- **Main window layout**: Preflight/File transfer row reverted to a **25% / 75%** horizontal split while preserving matched minimum heights. The trailing-slash note is again shown inside **Preflight** (beneath Source scan).

## [1.7.6] - 2026-04-08

### Fixed

- **Transfer progress with skip modes**: When **If file exists** is set to a skip policy (`--size-only` or `--ignore-existing`), progress now treats skipped files as completed work against the preflight scan total. The bar and remaining-bytes/ETA estimate no longer stall while rsync checks and skips unchanged destination files.
- **Qt disconnect warnings**: Cleanup paths no longer issue blanket `QObject.disconnect(...)` calls on short-lived scan/process objects, preventing repeated terminal warnings such as `QObject::disconnect: Unexpected nullptr parameter`.

## [1.7.5] - 2026-04-08

### Changed

- **Main window layout**: Preflight and File transfer share equal horizontal stretch with matched minimum heights. The trailing-slash hint was removed from the Preflight box (folded into the Source list tooltip). The scan row uses a **Source scan** label and tooltip explaining the idle dash versus the live progress bar. **Rsync** controls use a denser grid-style layout (delays, options, partial policy, if-exists, then BW limit and extra args on one horizontal row). Command preview minimum height is reduced; the activity log receives greater vertical stretch relative to the preview.
- **Transfer detail line**: Detail text order is **Elapsed → Attempt → bytes/stats → current file last** so the attempt counter does not shift when the filename changes.

## [1.7.4] - 2026-04-08

### Fixed

- **Source scan / guide pulse**: After a scan finished, ``QThread.deleteLater()`` could destroy the C++ thread while ``MainWindow._scan_thread`` still pointed at the Python wrapper, causing ``RuntimeError: Internal C++ object (PySide6.QtCore.QThread) already deleted`` on ``isRunning()`` (e.g. when adding folders or pulsing the guide). The scan thread now connects ``finished → _on_scan_thread_finished`` to clear ``_scan_thread``. All ``QThread`` / ``QProcess`` busy checks use safe helpers that catch ``RuntimeError`` and clear stale refs; ``quit()`` uses the same pattern.

## [1.7.3] - 2026-04-08

### Changed

- **Activity log**: Rsync stderr is no longer treated as “log unless it looks like a path.” Only **``rsync:``** diagnostic lines and lines matching **error / warning / failure / network / disk / I/O** heuristics are appended; routine status (``building file list``, ``sent``/``total``/``speedup``, ``created`` directory, ``done``, etc.) is suppressed. SafeCopi’s own messages (attempt banners, pause/resume, ``[SafeCopi]`` notices) are unchanged.

## [1.7.2] - 2026-04-08

### Fixed

- **Dest. space (remote)**: Regressed after **1.5.9** when **1.6.x** moved the remote ``df`` call off the GUI thread onto ``DestSpaceWorker`` / ``QThread`` (see **1.6.0** changelog: “GUI freeze during preflight”). The worker’s ``run`` slot did not reliably run on some setups (``debug.log`` showed ``SPACE ui_check_start`` but never ``check_start``). **Remote** free space again uses the same **``QProcess`` + watchdog timer** pattern as **Test SSH** (GUI thread, ``SSH_ASKPASS``/``sshpass`` env, explicit kill on timeout), so the queue completes and dialogs appear. **Local** destinations still use a short **``QThread``** with **``Qt.DirectConnection``** for ``started → run`` and a bounded ``local_free_bytes`` timeout.

### Changed

- **``debug.log``**: Millisecond timestamps; on each app launch, **only the last 3 sessions** (lines containing ``[SESSION] start``) are kept, with a ``[LOG] rotated`` line when older sessions are dropped; lines are **fsync**’d to improve survival on crash. **Shutdown hooks**: ``about_to_quit``, ``atexit``, and (POSIX) **``SIGTERM``** / **``SIGINT``** log ``APP`` events — **``SIGKILL``** cannot be caught. **Diagnostics**: SSH test failures and timeouts log **stderr/stdout excerpts**; remote space logs **``qprocess_*``** stages, nonzero exits, and timeouts; GitHub update check logs **structured network/HTTP errors** when the tag cannot be fetched.

## [1.7.1] - 2026-04-08

### Fixed

- **Dest. space (remote/local)**: ``debug.log`` showed ``SPACE ui_check_start`` with no ``check_start`` / ``check_done``, matching an indefinite hang. ``QThread.started`` → worker ``run`` now uses ``Qt.QueuedConnection`` so the slot runs on the worker thread’s event loop (consistent with reliable delivery). ``debug_log`` no longer holds the process-wide lock while appending to the file, so a slow or stuck config volume cannot block the GUI or the space worker. Remote ``df`` uses a dedicated subprocess overhead cap (``SSH_DF_SUBPROCESS_OVERHEAD_SEC``) and POSIX ``start_new_session`` so ``subprocess`` timeouts can tear down ``ssh``/``sshpass`` cleanly. Local free-space queries use a bounded wait (derived from **I/O timeout**, max 180s) via ``ThreadPoolExecutor`` so bad mounts cannot hang forever.

## [1.7.0] - 2026-04-07

### Added

- **Diagnostics**: Append-only **`debug.log`** (lowercase) under the app config directory (Qt ``AppConfigLocation``, typically ``~/.config/SafeCopi/debug.log`` on Linux). Logs session start, settings load/persist, UI lifecycle (window ready, close), scan/SSH/destination-space/update-check flows, sync start/stop/pause, and **rsync worker** state (**configure**, each **attempt**, **retry scheduling**, **pause/resume**, multi-source segment boundaries, abnormal exit, pipe anomalies) — **not** per-file rsync stderr/stdout or transfer progress lines. Module: ``safecopi.debug_log``.

## [1.6.6] - 2026-04-07

### Fixed

- **Test SSH**: Running the check on a background ``QThread`` broke ``SSH_ASKPASS`` / PySide password UI and prevented the success/failure ``QMessageBox`` from appearing reliably. **Test SSH** now uses ``QProcess`` on the GUI thread (with the same argv/env as ``run_ssh_command``) plus a watchdog timer, so the event loop stays responsive and dialogs match the pre–1.6.3 behavior. **Dest. space** remains on ``DestSpaceWorker``.

### Changed

- **If file exists**: Combo label **Skip (if filename and size is same)** (was “name”); default index is set explicitly to ``skip_name_size`` after populating the list. Tooltip wording aligned.
- **Internals**: ``SSH_TEST_CONNECT_TIMEOUT_SEC`` in ``utils`` matches **Test SSH** ``QProcess`` watchdog duration with ``run_ssh_command``; ``_qprocess_environment_from_environ_dict`` merges ``ssh_command_environment`` output for ``QProcess``.

## [1.6.5] - 2026-04-07

### Fixed

- **Guide pulse**: Reordered the idle checklist so it matches setup and preflight: **destination** is highlighted when empty **before** **Scan source**. Previously, all-local sources with no scan yet pulsed **Scan** even when the destination field was empty, which looked like a skipped step and caused confusing blinking. **Scan** is only suggested after the destination is set (still optional). Remote-only source flows skip **Scan** and continue to **Test SSH** as before.

### Changed

- **Documentation**: README guide-pulse row updated to describe the new order.

## [1.6.4] - 2026-04-07

### Fixed

- **Check for update**: GitHub API access ran on the GUI thread (up to the HTTP timeout), which could freeze the window briefly. The request now runs in a background ``QThread`` via ``GitHubUpdateCheckWorker``; **Check for update…** disables until the thread finishes, and quit is blocked while a check is in flight.

## [1.6.3] - 2026-04-07

### Fixed

- **GUI freeze during preflight**: **Dest. space** and **Test SSH** ran blocking ``subprocess``/SSH calls on the Qt GUI thread (remote ``df`` can wait up to ``connect_timeout + 120`` seconds), which made the window appear stuck while the OS still allowed moving it. Both checks now run on a background ``QThread`` via ``DestSpaceWorker`` and ``SshTestWorker`` (same repatriation pattern as source scan); **Dest. space** / **Test SSH** disable only while their own job runs. Quit is blocked until these finish, matching scan/sync behavior.

## [1.6.2] - 2026-04-07

### Changed

- **Documentation**: README rewritten for GitHub with centered badge row (release, stars, last commit, Python, PySide6, platform, stack), table-driven capabilities and requirements, clearer structure (contents, installation, usage, development), and consolidated repository links.

## [1.6.1] - 2026-04-07

### Added

- **Safeguards**: refuse **Start sync** when the destination field is empty; cap the source list at **64** folders (add, load from settings, and persist). **Add folder** skips duplicates that resolve to the same local path (trailing slash variants).

### Changed

- **Guide pulse** highlights **Remove** when multiple sources mix local and remote paths (unsupported configuration).
- **Remove** tooltip notes that mixed multi-source lists are invalid.

### Fixed

- **RsyncWorker** aborts an attempt cleanly if a source path normalizes to empty instead of invoking rsync with invalid arguments.

## [1.6.0] - 2026-04-07

### Added

- **Multiple local source folders**: **Source** is a list with **Add folder…** / **Remove**. Each folder is synced into the destination under its own top-level name (``rsync`` without a trailing slash on the source path, plus ``--mkpath`` on the destination). Runs are sequential in one session; the progress bar resets between sources. **Scan source** sums all listed trees for preflight totals.

### Changed

- **If file exists** default is **Skip (if name and size is same)** (``--size-only``) for safer first-time behavior. Legacy settings key ``default`` now maps to that mode (previously matched overwrite-style behavior).
- Multiple sources require **all-local** paths; a single **user@host:/path** source remains supported alone.

## [1.5.18] - 2026-04-07

### Changed

- **If file exists** dropdown reduced to three options: **Skip (if name and size is same)** (``--size-only``), **Skip (if only name is same)** (``--ignore-existing``), and **Overwrite** (no extra flag). Older saved mode keys map to the closest new value.

## [1.5.17] - 2026-04-07

### Added

- **Rsync** form: **If file exists** dropdown (persisted) mapping to standard rsync policies: default update-when-different, ``--update`` / ``-u`` (keep newer destination files), ``--ignore-existing``, ``--inplace``, ``--backup``, and ``--existing``. Logic lives in ``utils`` with unit tests; flags are inserted before **Extra args** (tooltip warns against duplicates there).

## [1.5.16] - 2026-04-07

### Changed

- **Layout**: **Command preview** uses more vertical space (minimum height, expanding policy, word wrap, no block-count cap) and shares flexible height with the activity log at a 1∶2 ratio so long rsync argument lists stay visible; the log receives proportionally less of the remaining window height.

## [1.5.15] - 2026-04-07

### Fixed

- **Progress bar after successful sync**: Transfer ``progress`` slots use ``Qt.QueuedConnection``, so the final progress update could be delivered **after** ``sync_finished`` had already set the bar to 100 %, restoring a partial value (e.g. ~81 % when byte-based progress lagged the source scan total on incremental runs). Progress coalescing and pending snapshots are now ignored once the sync session is no longer active; the monotonic peak is set to full scale on success.

## [1.5.14] - 2026-04-07

### Added

- **Fail-safe guards**: bounded rsync stdout/stderr assembly (drop stuck buffers without newlines; skip overlong lines), clamped ``--timeout`` in ``build_rsync_command_argv``, sane limits and coercion for worker timeout/retry delays, validation caps on **Extra rsync arguments** length and token count, optional PID checks before **SIGSTOP** / **SIGCONT** / **kill**, log line truncation before appending, a confirmation dialog before **Stop** during sync, and broader exception handling during sync startup.

## [1.5.13] - 2026-04-07

### Fixed

- **Rsync stderr classification**: Treat lines containing ``/`` as probable paths **before** applying substring needles such as ``deleting``, ``error``, or ``auth``. Previously, legitimate paths like ``backup/deleting/photo.jpg`` were logged to the activity panel and were not used for the transfer detail line.

## [1.5.12] - 2026-04-07

### Changed

- **File transfer detail line**: The current transfer path (from rsync ``-v`` stderr) is shown before **Attempt**, so size-first progress lines without parenthetical stats still identify the active file. Default rsync argv no longer passes ``--info=name0`` (names were suppressed); use **Extra rsync arguments** with ``--info=name0`` to restore the previous stderr volume if needed.

## [1.5.11] - 2026-04-07

### Added

- **Pause** / **Resume** between **Start sync** and **Stop**: suspends the running rsync on POSIX via **SIGSTOP** / **SIGCONT**, or holds the next **retry** countdown until resumed. **Stop** still terminates the sync (SIGCONT before kill when needed).

## [1.5.10] - 2026-04-07

### Changed

- **Guide pulse**: use a uniform **2px** push-button border so the pulse only toggles border **color** (no layout shift). Guided order: source **Browse** (local path missing/invalid) → **Scan source** (local tree not yet scanned) → destination **Browse** → **Test SSH** when either path is remote (cleared when source or destination text changes) → **Dest. space** for **remote destinations** until free space is known → **Start sync**. Empty **remote** source no longer highlights source **Browse**.

## [1.5.9] - 2026-04-06

### Added

- **Guide pulse**: adopted the red blinking button guide pattern (from ChronoArchiver) to highlight the next action in idle state: **Browse** (missing local source), **Scan source** (local source unscanned), **Destination Browse** (empty destination), then **Start sync**. Pulse pauses during active scans/syncs and never forces disabled buttons to appear active.

## [1.5.8] - 2026-04-06

### Added

- **Check for update**: Button next to **Copy log** / **Save log** that queries the latest GitHub release and compares its tag against the running `__version__`, reporting when a newer version is available.

## [1.5.7] - 2026-04-06

### Changed

- **Layout**: **Preflight** and **File transfer** share one row (**~25% / ~75%** horizontal split), with action buttons on the full width below, to reduce empty vertical space. Preflight form spacing is slightly tighter for the narrow column.

## [1.5.6] - 2026-04-06

### Changed

- **Source scan**: While **Scan source** is running, **Start sync**, path fields, **Browse**, SSH passwords, **Rsync** options, **Test SSH**, and **Dest. space** are disabled (same idea as during sync). **Stop scan** remains available until the walk ends or cancel completes.
- **Layout**: **Scan source** and **Stop scan** sit in the **center** of the action row, with **Test SSH** / **Dest. space** on the left and **Start sync** / **Stop** on the right (equal stretch on both sides of the scan pair).

## [1.5.5] - 2026-04-06

### Changed

- **Transfer bar**: When a **preflight source scan** total is available, fill and percent follow **bytes sent ÷ scanned size** only (rsync’s internal % is not mixed in). Without a scan, the bar still follows rsync’s overall percent.
- **Bar label**: Shows **bytes remaining** vs the scan total (estimated from rsync % until cumulative **sent** appears on progress lines), current **speed**, and **ETA** computed as **remaining ÷ parsed speed** when speed parses; otherwise rsync’s ETA is shown. **finishing…** when the run is essentially done (including zero remaining vs scan). If rsync sends far less than the scanned total (e.g. incremental sync), the bar may stay below 100% until the process exits successfully.

### Added

- **`parse_rsync_speed_to_bytes_per_sec`**, **`format_seconds_as_hms_display`** in `utils` for ETA math and display.

## [1.5.4] - 2026-04-06

### Changed

- **Sync session**: while a transfer is active (including retry waits), **Source** / **Destination** fields, **Browse**, **Src.** / **Dest.** password, **Rsync** options (timeouts, dry run, recursion, partial mode, BW limit, extra args), **Test SSH**, **Dest. space**, and **Scan source** are disabled. **Stop scan** is unchanged if a scan was already running. Controls restore when the sync finishes, fails out, or is stopped.

## [1.5.3] - 2026-04-06

### Changed

- **Progress UI**: ETA and rsync **elapsed** times use stable **`HH:MM:SS`**-style labels (zero-padded minutes and seconds; hours padded to two digits below 100h, then natural width for longer runs). **Session elapsed** under the bar uses the same pattern instead of `M:SS` under one hour.
- **Sizes**: **`human_bytes`** and scan/space labels use **decimal SI** units (**KB**, **MB**, **GB**, **TB**) instead of binary KiB/MiB/GiB/TiB. The transfer bar prefers parsed byte totals so **sent** matches (**GB** with a **B**, not rsync’s bare `G`).

## [1.5.2] - 2026-04-06

### Changed

- **Paths**: source and destination placeholders show `"user@ip:/path" or "/path"`.

## [1.5.1] - 2026-04-06

### Changed

- **Paths**: source and destination line edits use placeholder examples `user@ip:/mnt/` or `"/mnt"`.

## [1.5.0] - 2026-04-06

### Added

- **Remote source SSH**: **Src. password** inline on the source row when the source is `user@host:/path`, mirroring **Dest. password** for the destination. Rsync’s **`-e`** SSH transport is applied when **either** side is remote (not only the destination). `QProcess` env uses `sshpass` / `SSH_ASKPASS` from the relevant field; if **both** ends are remote, **destination password is preferred** for `SSHPASS` when set, otherwise source (one env var for both hops — use keys if accounts differ).

### Changed

- **Test SSH**: tests the **destination** host when it is remote; otherwise the **source** host when only the source is remote.
- **Preflight / scan**: local directory checks and **Scan source** apply only to **local** sources; remote source paths skip the local existence check and show an informative message instead of running a tree walk.

## [1.4.7] - 2026-04-06

### Fixed

- **Progress bar label**: use a Unicode percent sign (U+0025) in `setFormat` instead of `%%`, which was rendering as a **double percent** on some Qt styles.
- **Jumpy bar**: drop **`xfr#` / file-count** from the bar calculation (it advances every file); keep **rsync %** and **bytes / scanned size** only. Track a **monotonic peak** so the bar value never decreases between updates.

### Changed

- **File transfer**: removed the **bold duplicate line** under the bar (same data as the bar text). **Session elapsed** and the **detail line** (`Elapsed …`, bytes, verify/transfer counters, attempt) remain.

## [1.4.6] - 2026-04-06

### Added

- **Activity log**: each appended line is prefixed with a local timestamp `[YYYY-MM-DD HH:MM:SS]`. Multi-line messages receive one timestamp per line.
- **File transfer**: **Session elapsed** label under the progress bar—wall-clock time since **Start sync**, updated every 500 ms while a run is active; freezes on completion, stop, or failure (reset to `—` when the panel returns to idle).

## [1.4.5] - 2026-04-06

### Changed

- **Paths**: destination row matches source—**Browse…** opens a local folder (starting from `$HOME` when the field is a remote `user@host:/path`). **SSH password** sits after the destination browse control and is **shown only for remote rsync destinations** (`parse_rsync_destination`). Source and destination fields use **`PathLineEdit`** styling (monospace, taller row, focus ring, clear button).

## [1.4.4] - 2026-04-06

### Fixed

- **Activity log flood**: rsync **stdout** is filtered like stderr—transfer progress lines are parsed for the transfer panel only and are no longer appended to the log. Incomplete writes are accumulated in line buffers until a newline (with `\r` normalized) so progress lines are not misclassified as plain text.

### Changed

- **Transfer progress bar**: uses an internal **0..10000** scale so sub‑percent motion is visible on multi‑tebibyte trees; **bar label** (`setFormat`) shows **two decimal %**, bytes sent, scanned total (when a source scan exists), **speed**, and **ETA**. Progress uses the maximum of rsync’s %, **bytes sent / scanned size**, and **`xfr#` / scanned file count** when those numbers are available (`parse_rsync_xfr_count()` in `utils`).

## [1.4.3] - 2026-04-06

### Changed

- **Transfer panel**: size-first rsync progress lines now expose cumulative **bytes sent** (`transferred_bytes` / `transferred_display` on `RsyncProgressSnapshot`). The headline shows **`<token> sent · % · speed · ETA`**; the detail line includes canonical **KiB/MiB/…** via `human_bytes` when parsing succeeds.
- **Progress bar**: when a **source scan** total size is available, the bar uses `max(rsync %, bytes_sent / scanned_size)` so it advances during long jobs where rsync’s overall percentage stays at **0%** (multi‑tebibyte trees).

### Added

- **`parse_rsync_transferred_amount_token()`** in `utils` for rsync size tokens (`K`/`M`/`G`/`T`/`P` as 1024-based multiples).

## [1.4.2] - 2026-04-06

### Changed

- **README**: restructured for GitHub (overview, features table, requirements, install, run, usage, development). Added **GitHub repository metadata** section with copy-paste description text and suggested topics for the repository About field.

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
