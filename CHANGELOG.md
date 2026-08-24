# Changelog

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
