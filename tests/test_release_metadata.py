import re
from pathlib import Path

APP_DIR = Path("cctv_event_timeline")


def test_home_assistant_release_metadata_is_complete_and_consistent():
    config = (APP_DIR / "config.yaml").read_text(encoding="utf-8")
    version_match = re.search(r'^version:\s*["\']([^"\']+)["\']\s*$', config, re.MULTILINE)
    assert version_match, "config.yaml must expose the add-on version as a quoted string"
    version = version_match.group(1)

    application = (APP_DIR / "app" / "main.py").read_text(encoding="utf-8")
    assert f'version="{version}"' in application
    assert f'"version": "{version}"' in application

    changelog = (APP_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {version} " in changelog

    dockerfile = (APP_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG BUILD_VERSION" in dockerfile
    assert 'io.hass.version="${BUILD_VERSION}"' in dockerfile
    assert 'io.hass.type="app"' in dockerfile
    assert 'io.hass.arch="${BUILD_ARCH}"' in dockerfile
