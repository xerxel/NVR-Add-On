# CCTV Event Timeline add-on

The add-on turns Home Assistant Hikvision motion sensors into a local visual event timeline and generates browser-compatible historical clips from bounded NVR playback requests.

## First run

1. Configure the NVR host, RTSP port, dedicated playback username/password, timezone, and timestamp mode in the add-on Configuration tab.
2. Start the add-on and open its Web UI.
3. In **Settings**, enable and name each used channel. Map its motion `binary_sensor`, camera entity, and NVR tracks.
4. Run **Application health**, **Home Assistant API**, and **NVR network** in Diagnostics.
5. Trigger a known motion event, wait for it to clear, and calibrate playback timing if the returned footage is offset.

All processing is local. Credentials are server-side and returned diagnostic URLs are redacted. See the repository `README.md` for every option, testing, backup, upgrade, security, and troubleshooting detail.

