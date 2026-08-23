# CCTV Event Timeline

CCTV Event Timeline is a local-only Home Assistant add-on for Hikvision NVRs. It listens to the motion entities already created by Home Assistant, builds a filterable eight-camera timeline, retrieves bounded historical RTSP recordings, and converts them to seekable browser-compatible MP4 files. NVR and Supervisor credentials remain server-side.

> Screenshot placeholders: timeline, channel settings, player, and diagnostics screenshots should be captured from the installed add-on because the repository contains no real household imagery.

## Features

- Eight independently enabled and named NVR channels with editable motion entity, camera entity, main track, subtrack, and timestamp offset mappings.
- Persistent Home Assistant WebSocket subscription plus bounded Recorder history reconciliation.
- Immediate live camera-proxy snapshot for a new event and replacement by an authoritative historical NVR frame after the event closes.
- On-demand, bounded H.264/AAC MP4 generation with fast-start metadata, FFprobe validation, job deduplication, caching, and byte-range seeking.
- UTC, NVR-local, and manual-offset Hikvision playback timestamp modes with IANA timezone/DST conversion.
- SQLite metadata, atomic media writes, abandoned-job cleanup, age/size cache retention, responsive ingress UI, and sanitised diagnostics.
- No cloud service, analytics, external JavaScript, fonts, or telemetry.

## Architecture and security decisions

```text
Hikvision integration -> HA REST/WebSocket -> add-on event reconciler -> SQLite /data
                                                        |
Browser <- ingress <- FastAPI <- safe media routes <- FFmpeg/FFprobe <- bounded RTSP playback
```

Home Assistant supplies `SUPERVISOR_TOKEN` to the container; it is never returned to the browser. Hikvision credentials are read from protected add-on options and are only placed in the server-side FFmpeg argument array. Logs, API errors, reports, and generated URL displays pass through central redaction. The browser never receives RTSP URLs. There is no published port, privileged mode, host PID access, or general-purpose proxy. State-changing browser calls reject cross-site requests.

FFmpeg process arguments can be visible to an administrator with sufficient access to the Home Assistant host. Use a dedicated Hikvision user restricted to live-view and playback permissions. This is the principal residual credential risk.

## Requirements

- Home Assistant OS or Supervised installation with the add-on store and Supervisor API. Home Assistant Container/Core alone cannot install add-ons.
- Home Assistant Hikvision integration with camera and motion `binary_sensor` entities.
- Recorder/history data retained for the desired backfill window.
- Hikvision NVR reachable from the add-on network, recording the mapped channels continuously or on motion.
- Supported architectures: `amd64` and `aarch64`.

On the NVR, verify NTP, timezone, recording schedule, playback permissions, and channel numbering. Create a dedicated least-privilege account. Defaults map channel N to main track `N01` and subtrack `N02`, but every track is editable.

## Install from the add-on store

1. Put this repository on a Git hosting service reachable by Home Assistant and update the example URLs in `repository.yaml`, `config.yaml`, and `build.yaml`.
2. In Home Assistant open **Settings > Add-ons > Add-on store > ⋮ > Repositories**.
3. Add the repository HTTPS URL, open **CCTV Event Timeline**, and select **Install**.
4. In **Configuration**, set the NVR username/password and review the settings below. Save.
5. Enable **Show in sidebar**, start the add-on, and open its Web UI.
6. Open **Settings** in the add-on, map each enabled channel, and save.

The add-on intentionally starts with empty credentials and all channels disabled so diagnostics remain available during first-run setup.

## Local build and validation

From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r cctv_event_timeline\requirements-dev.txt
.venv\Scripts\python -m ruff check cctv_event_timeline tests
.venv\Scripts\python -m pytest -q
docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.22 -t cctv-event-timeline:local cctv_event_timeline
```

For an ARM64 build, use `ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.22` and an ARM-capable Docker builder. For a mock local app run, set `TIMELINE_DATA`, `OPTIONS_FILE`, `HA_API_URL`, and `HA_WS_URL` to test services, then run `uvicorn app.main:app --app-dir cctv_event_timeline --port 8099`.

## Configuration reference

| Option | Default | Purpose |
|---|---:|---|
| `nvr_host` | `192.168.0.100` | Validated NVR host/IP; diagnostic endpoints cannot override it. |
| `rtsp_port` | `554` | NVR RTSP TCP port. |
| `nvr_username` / `nvr_password` | empty | Dedicated NVR credentials, stored as add-on options. |
| `nvr_timezone` | `Europe/London` | IANA zone used by `nvr_local`. |
| `timestamp_mode` | `utc` | `utc`, `nvr_local`, or `manual_offset`. |
| `manual_offset_minutes` | `0` | Applied only in manual mode unless overridden per camera. |
| `rtsp_transport` | `tcp` | `tcp` or `udp`; TCP is more reliable through typical LANs. |
| `pre_roll_seconds` / `post_roll_seconds` | `5` / `10` | Recording included around each event. |
| `max_clip_seconds` | `180` | Hard maximum generated event clip. |
| `merge_gap_seconds` | `5` | Clear-to-detected gap merged for the same camera. |
| `media_retention_days` | `14` | Age limit for cached thumbnails/videos. |
| `max_cache_mb` | `2048` | Total media cache ceiling; oldest files are removed first. |
| `ffmpeg_timeout_seconds` | `120` | Media process deadline. |
| `max_concurrent_jobs` | `1` | Conservative CPU/NVR job limit. |
| `history_backfill_hours` | `24` | Startup/gap Recorder reconciliation window, max 168. |
| `log_level` | `info` | `debug`, `info`, `warning`, or `error`; production debug is discouraged. |

The in-app channel editor has exactly eight slots. Each holds enabled state, display name, NVR channel number, motion entity, camera entity, main/sub track IDs, optional timestamp offset, and the thumbnail stream. Use **Diagnostics > Entity discovery** to find IDs, then copy them into the editor. Mappings are atomically stored in `/data/timeline/channels.json`.

## Using the timeline

Choose a date and optionally a camera. Previous/next controls navigate days. Cards show local browser time, duration, event status, thumbnail source state, and clip cache state. Select a thumbnail to open the historical player. On first use, **Generate historical clip** queues a bounded job; the UI polls until it can play the cached MP4. **Delete cached clip** permits regeneration.

For a live event, an HA camera-proxy image appears quickly and is labelled `ha_live`. After the clear transition and NVR finalisation delay, it is replaced with `nvr_historical`. Backfilled events do not use a current live snapshot; an unavailable historical frame remains unavailable rather than being misrepresented.

## Diagnostics and test calls

Open **Diagnostics / Test** in the add-on. Results show pass/fail, duration where applicable, sanitised details, and never tokens or credential-bearing URLs.

1. **Application health** checks SQLite/data writes and locates FFmpeg/FFprobe.
2. **Home Assistant API** verifies authenticated server-side access and returns HA version/timezone.
3. **Entity discovery** lists plausible motion/camera entities and mapping existence.
4. Recorder history is exercised automatically at startup; inspect timeline reconciliation using a short `history_backfill_hours` first.
5. Live snapshot retrieval occurs for new events through the camera proxy; its source is shown on the event.
6. **NVR network** makes only a five-second TCP connection to configured host/port.
7. The live probe endpoint uses the selected mapped track, a strict timeout, and sanitised stream metadata.
8. **Playback URL generator** returns only a redacted URL plus interpreted UTC/playback times.
9. Historical frames are exercised when a completed event finalises.
10. Historical MP4/player is exercised by generating a timeline clip.
11. For timestamp calibration, generate the same known 10–20 second event using each mode, compare the redacted request times, and select the mode that contains the event.
12. **Download sanitised report** exports versions, safe settings, mappings, health, and stored test results.

Ingress changes the URL prefix per add-on/session. Use the UI rather than hard-coding `/api`. For authenticated developer inspection from the browser console, a safe read-only example is `fetch('api/health').then(r => r.json())`; do not publish an ingress URL or paste Supervisor/NVR credentials into calls.

### UK timestamp example

At 21:53:37 BST on 23 August 2026, Home Assistant stores `2026-08-23T20:53:37Z`. `utc` produces `20260823T205337Z`. `nvr_local` produces the firmware-compatible local interpretation `20260823T215337Z`. In winter both match because London is UTC. Around DST, conversion starts from an unambiguous HA UTC timestamp and uses the IANA timezone database. If footage is exactly one hour early or late, run calibration instead of changing the system clock.

## Upgrades, backup, and restore

Before upgrading, take a Home Assistant backup including add-on data. Stop the add-on for a consistent manual copy of `/data/timeline`: `channels.json` contains mappings, `events.db` metadata, and `cache/` regenerable media. Restore the same directory and add-on options, then start the same or newer compatible version. Updates use the add-on store **Update** action; review `CHANGELOG.md`, back up, update, start, and rerun the first three diagnostics.

Database metadata is retained even when cached media is removed. Approximate storage by multiplying average generated clip bitrate by retained clip seconds; H.264 at 2 Mb/s is about 15 MB/minute. Set both retention days and a cache ceiling suitable for Home Assistant storage.

## Troubleshooting

| Symptom | Checks / action |
|---|---|
| No motion events | Enable the channel, verify exact motion entity, run Entity discovery, and confirm Recorder retention. |
| Camera entity unavailable | Check the Hikvision integration/entity state; event capture continues but the quick image may fail. |
| Thumbnail is current | `ha_live` is the documented provisional source; wait for event clear/NVR finalisation and `nvr_historical`. Backfilled events never use it. |
| RTSP 401/403 | Verify the dedicated username/password and playback permission; restart after option changes. |
| No recording at requested time | Check recording schedule, track mapping, NVR retention, pre-roll, and calibration. |
| Video one hour early/late | Compare `utc` and `nvr_local`; do not compensate twice with both NVR zone and manual offset. |
| DST mismatch | Verify NVR timezone/NTP and use the calibration workflow near a known event. |
| H.265 does not play | Generated clips transcode video to H.264 `yuv420p`; inspect FFmpeg result/report if conversion failed. |
| FFmpeg timeout | Confirm NVR reachability, prefer TCP, shorten clip, or cautiously increase timeout. |
| MP4 exists but does not play | Delete/regenerate it; FFprobe must validate before `ready`, and range requests should return 206. |
| Ingress blank/broken assets | Update/restart the add-on, hard refresh, and confirm no reverse proxy strips the ingress prefix. Assets/API links are relative. |
| Disk/cache full | Lower retention/cache size and restart to trigger cleanup; cached media is safe to regenerate. |
| NVR offline | The add-on stays running; use HA/API/entity diagnostics while restoring LAN/NVR service. |

When requesting help, download the sanitised report and inspect it before sharing. It excludes configured passwords, Supervisor tokens, credential-bearing URLs, process arguments, and internal media paths. Still treat household entity names and LAN addresses as private.

## Known limitations

- Real playback behaviour varies across Hikvision firmware; on-device timestamp calibration is required.
- The first release transcodes event video for predictable browser compatibility rather than attempting a codec-copy optimization.
- Live view is left to the existing Home Assistant camera entity; historical playback is the add-on's focus.
- Cache cleanup runs at startup; long-running installations should restart during normal update/maintenance cycles.
- A Home Assistant/NVR network is not bundled with tests; integration tests use fixtures/mocks and real-device checks must be performed after installation.

All processing and persistence remain local. No data is sent to a cloud service. Contributions are welcome through issues/pull requests that include tests and contain no real credentials, LAN details, or household imagery. Licensed under the [MIT License](LICENSE).

## Official references

Packaging follows the current Home Assistant developer documentation for [add-on configuration](https://developers.home-assistant.io/docs/add-ons/configuration/), [repository structure](https://developers.home-assistant.io/docs/add-ons/repository/), [presentation/ingress](https://developers.home-assistant.io/docs/add-ons/presentation/), and [Supervisor communication](https://developers.home-assistant.io/docs/add-ons/communication/). RTSP transport and process behaviour follow the [FFmpeg protocol documentation](https://ffmpeg.org/ffmpeg-protocols.html#rtsp).

