# Changelog

## 0.1.16 - 2026-08-24

- Added event-specific **Open in VLC** links that launch without credentials by
  default and use the operating system's registered RTSP handler.
- Added optional VLC credentials protected by an authenticated-encryption key
  stored under `/data` and an encrypted, HttpOnly, SameSite browser cookie.
- Added a copyable credential-redacted historical RTSP address to event popups.
- Saving channel mappings now clears and immediately revalidates the main-stream
  codec for every enabled camera.
- Colour-coded codec pills now distinguish H.264, H.264+, H.265, H.265+, and
  detection states, with H.265 shown in dark red.

## 0.1.15 - 2026-08-24

- Added a Diagnostics storage report with total consumption, file counts, free
  filesystem space, and a proportional category bar for system files,
  thumbnails, videos, database files, logs, temporary files, and other data.
- Added live add-on and whole-system CPU usage bars that update only while the
  Diagnostics page is open.
- Expanded the Timeline to use the available screen width and add responsive
  columns whenever they fit.
- Reduced thumbnail display height by half without distorting source images.

## 0.1.14 - 2026-08-24

- H.264 historical recordings now start playing as a fragmented MP4 while the
  same request continues to build the cached clip in the background.
- Camera codec probes are persisted and reused, and timeline thumbnails show a
  codec badge for H.264, H.264+, H.265, or H.265+ when detectable.
- Failed historical clips now retain a credential-safe request and processing
  report in the event popup, including the failed phase and error reason.
- Clip timeouts now account for the requested recording duration, and
  user-requested clips continue to pause and resume background thumbnail work.

## 0.1.13 - 2026-08-24

- Moved the changelog beside the app configuration so Home Assistant can show
  release notes in its update dialog.
- Added the documented Home Assistant version, app-type, and architecture image
  labels to locally built images.
- Added a release-metadata contract test that keeps `config.yaml`, the API
  version, image labels, and changelog aligned for future releases.

## 0.1.12 - 2026-08-24

- User-requested historical clips now take priority over background thumbnail
  recovery instead of waiting behind the thumbnail queue.
- An active background thumbnail subprocess is stopped safely when a clip is
  requested, then the same thumbnail resumes after all queued clips finish.
- Runtime health now reports whether background thumbnails are paused for a
  historical clip.

## 0.1.11 - 2026-08-24

- Media subprocess timeouts now escalate from graceful termination to a forced
  kill, preventing unresponsive FFmpeg processes from leaving jobs hung.
- The configured media concurrency limit now covers thumbnail, video, FFmpeg,
  and FFprobe operations.
- Pending thumbnails are processed from the most recent event backwards, with
  deterministic ordering when event timestamps match.

## 0.1.10 - 2026-08-24

- Diagnostics now shows all currently running background operations, their
  current phase, start time, safe context, and elapsed duration.
- Optional live updates refresh that activity once per second only while the
  Diagnostics page is visible and the toggle is enabled.
- Added a bounded, credential-sanitised in-memory application log view and a
  button to truncate that view without deleting Supervisor logs, events, or
  cached media.
- Historical video tracking distinguishes worker wait, source probing, MP4
  generation, and validation so a stalled operation can be located precisely.

## 0.1.9 - 2026-08-24

- Pending historical thumbnails are recovered after restart and processed
  sequentially instead of remaining permanently in `finalising`.
- Interrupted `generating` video records are marked failed with a retryable
  explanation during startup.
- H.264 main-stream video is remuxed without re-encoding; only incompatible
  audio is converted to AAC. Non-H.264 video still uses the safe transcode path.

## 0.1.8 - 2026-08-24

- New channel configurations now enable all eight camera slots by default.
  Previously saved channel settings remain unchanged during upgrades.

## 0.1.7 - 2026-08-24

- Historical-thumbnail diagnostics no longer wait behind 4K video encoding or
  background/backfill thumbnail queues.
- Diagnostic frame extraction now has a 30-second maximum and persists its
  pass/fail result with sanitised detail in the diagnostic report.
- Health output reports whether diagnostic or background thumbnail extraction
  is busy.
- History reconciliation now sorts transitions, ignores exact duplicates,
  rejects negative merge gaps, and cannot close an event before it starts.
- Existing events with an invalid stored end time use a bounded 30-second
  playback fallback instead of returning an unhandled server error.

## 0.1.6 - 2026-08-24

- All historical playback operations now always use the configured main track:
  event thumbnails, event videos, URL generation, historical diagnostics, and
  timestamp calibration.
- The subtrack remains available only for the explicitly labelled live probe.

## 0.1.5 - 2026-08-24

- Fixed SQLite connections accumulating until the container exhausted its file
  descriptor limit. Every transaction now closes its connection in `finally`.
- Cached the small frontend entry document at application startup instead of
  reopening it on every navigation request.

## 0.1.4 - 2026-08-24

- Separated RTSP live and historical playback paths. Live probing defaults to
  `/Streaming/channels/{track}`; historical thumbnails and videos default to
  `/Streaming/tracks/{track}`.
- Legacy `rtsp_path_template` values migrate to the live path only, preventing
  a live Hikvision endpoint from silently ignoring historical timestamps.

## 0.1.3 - 2026-08-24

- Historical thumbnails now skip initial decoder frames and reject uniform grey
  output instead of serving it as a successful image.
- FFmpeg clips have an explicit bounded duration, so Hikvision streams that do
  not close at `endtime` cannot leave the diagnostic waiting until timeout.
- Historical video diagnostics return the sanitised FFmpeg failure detail.
- Saving channel mappings immediately queues history reconciliation; completed
  backfilled events also queue historical thumbnails.
- Timeline dates now use `Europe/London` local-day boundaries rather than UTC
  midnight boundaries.

## 0.1.2 - 2026-08-24

- Added the configurable `rtsp_path_template` option. It defaults to
  `/Streaming/tracks/{track}` and supports `{track}`, `{channel}`, and
  `{stream}` placeholders.
- New channel mappings are prefilled with
  `binary_sensor.network_video_recorder_channel_N_motion` and
  `camera.network_video_recorder_channel_N` for channels 1 through 8.

## 0.1.1 - 2026-08-24

- Fixed startup under the least-privileged `timeline` account when Home
  Assistant creates `/data/options.json` with root-only permissions.
- The root-owned options file is copied to a mode `0600`, runtime-only file;
  the Supervisor-managed source permissions remain unchanged.

## 0.1.0 - 2026-08-23

- Initial release: eight-channel event timeline, Home Assistant event ingestion,
  Hikvision historical thumbnails and MP4 clips, retention, settings, and diagnostics.
