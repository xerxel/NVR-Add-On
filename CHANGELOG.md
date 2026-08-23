# Changelog

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
