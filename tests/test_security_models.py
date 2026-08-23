import pytest
from app.models import Channel, ChannelList
from app.security import redact, safe_name


def camera(i=1):
    return Channel(id=i, enabled=True, name=f"Camera {i}", nvr_channel=i, motion_entity=f"binary_sensor.motion_{i}",
                   camera_entity=f"camera.channel_{i}", main_track=f"{i}01", sub_track=f"{i}02")


def test_channel_mapping_validation():
    assert ChannelList(channels=[camera(i) for i in range(1, 9)]).channels[7].main_track == "801"
    with pytest.raises(ValueError): Channel(**{**camera().model_dump(), "motion_entity": "sensor.bad"})
    with pytest.raises(ValueError): ChannelList(channels=[camera(1)] * 8)


def test_redaction_covers_url_bearer_plain_and_encoded():
    text = "rtsp://user:pass@nvr/x Bearer abc.def secret%2Fvalue secret/value"
    out = redact(text, ("secret/value",))
    assert "user:pass" not in out and "abc.def" not in out and "secret" not in out


@pytest.mark.parametrize("name", ["../x", "x/y", "", "x.jpg"])
def test_safe_filename_rejects_paths(name):
    with pytest.raises(ValueError): safe_name(name)

