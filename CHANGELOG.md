# Changelog

## 0.1.1 - 2026-08-24

- Fixed startup under the least-privileged `timeline` account when Home
  Assistant creates `/data/options.json` with root-only permissions.
- The root-owned options file is copied to a mode `0600`, runtime-only file;
  the Supervisor-managed source permissions remain unchanged.

## 0.1.0 - 2026-08-23

- Initial release: eight-channel event timeline, Home Assistant event ingestion,
  Hikvision historical thumbnails and MP4 clips, retention, settings, and diagnostics.
