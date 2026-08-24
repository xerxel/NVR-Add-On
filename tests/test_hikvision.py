from datetime import datetime, timezone

import pytest
from app.hikvision import TimestampMode, default_tracks, hik_time, playback_url, render_path, stream_url


def test_all_default_tracks():
    assert [default_tracks(i) for i in range(1, 9)] == [(f"{i}01", f"{i}02") for i in range(1, 9)]


@pytest.mark.parametrize("channel", [0, 9])
def test_invalid_channel(channel):
    with pytest.raises(ValueError): default_tracks(channel)


def test_utc_format():
    assert hik_time(datetime(2026, 8, 23, 20, 53, 37, tzinfo=timezone.utc), TimestampMode.UTC, "Europe/London") == "20260823T205337Z"


def test_london_summer_and_winter():
    assert hik_time(datetime(2026, 8, 23, 20, tzinfo=timezone.utc), TimestampMode.NVR_LOCAL, "Europe/London") == "20260823T210000Z"
    assert hik_time(datetime(2026, 1, 23, 20, tzinfo=timezone.utc), TimestampMode.NVR_LOCAL, "Europe/London") == "20260123T200000Z"


def test_dst_boundaries_are_unambiguous_from_utc():
    assert hik_time(datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc), TimestampMode.NVR_LOCAL, "Europe/London") == "20260329T005900Z"
    assert hik_time(datetime(2026, 3, 29, 1, 1, tzinfo=timezone.utc), TimestampMode.NVR_LOCAL, "Europe/London") == "20260329T020100Z"
    assert hik_time(datetime(2026, 10, 25, 1, 1, tzinfo=timezone.utc), TimestampMode.NVR_LOCAL, "Europe/London") == "20261025T010100Z"


def test_manual_offset():
    assert hik_time(datetime(2026, 1, 1, tzinfo=timezone.utc), TimestampMode.MANUAL_OFFSET, "UTC", 90) == "20260101T013000Z"


def test_url_encoding_redaction_and_bounding():
    req = playback_url(host="nvr.test", port=554, username="u@ser", password="p:a/ss", track="601",
                       start="2026-08-23T20:00:00Z", end="2026-08-23T21:00:00Z", mode=TimestampMode.UTC,
                       nvr_timezone="Europe/London", pre_roll=5, post_roll=10, max_seconds=180)
    assert "u%40ser:p%3Aa%2Fss@" in req.url
    assert "u@ser" not in req.redacted_url and "p:a/ss" not in req.redacted_url
    assert (req.end_utc - req.start_utc).total_seconds() == 180


def test_invalid_range_and_host():
    with pytest.raises(ValueError):
        playback_url(host="bad/host", port=554, username="", password="", track="601", start="2026-01-01T01:00:00Z",
                     end="2026-01-01T00:00:00Z", mode=TimestampMode.UTC, nvr_timezone="UTC")


def test_configurable_path_placeholders():
    assert render_path("/custom/{channel}/{stream}/{track}", track="602", channel=6, stream="sub") == "/custom/6/sub/602"
    req = playback_url(host="nvr.test", port=554, username="", password="", track="602",
                       start="2026-08-23T20:00:00Z", end="2026-08-23T20:00:15Z", mode=TimestampMode.UTC,
                       nvr_timezone="Europe/London", path_template="/custom/{channel}/{stream}/{track}",
                       channel=6, stream="sub")
    assert req.redacted_url.startswith("rtsp://***:***@nvr.test:554/custom/6/sub/602?")


@pytest.mark.parametrize("template", ["rtsp://other/track", "/track/{unknown}", "/track/../secret", "/track/{track}?x=1"])
def test_unsafe_path_templates_are_rejected(template):
    with pytest.raises(ValueError):
        render_path(template, track="101", channel=1, stream="main")


def test_live_stream_url_has_no_playback_query():
    request = stream_url(host="nvr.test", port=554, username="user", password="secret", track="102",
                         path_template="/Streaming/channels/{track}", channel=1, stream="sub")
    assert request.redacted_url == "rtsp://***:***@nvr.test:554/Streaming/channels/102"
    assert "starttime" not in request.url and "endtime" not in request.url
