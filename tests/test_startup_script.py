from pathlib import Path


def test_startup_uses_private_runtime_options_copy():
    script = Path("cctv_event_timeline/run.sh").read_text(encoding="utf-8")
    assert 'cp /data/options.json "${runtime_options}"' in script
    assert 'chmod 0600 "${runtime_options}"' in script
    assert "umask 077" in script
    assert 'env OPTIONS_FILE="${runtime_options}"' in script
    assert "su-exec timeline:timeline" in script
