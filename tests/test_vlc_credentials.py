from datetime import datetime, timezone

from app.main import app, db
from app.vlc_credentials import COOKIE_NAME, VlcCredentialStore
from fastapi.testclient import TestClient


def test_vlc_cookie_ciphertext_does_not_contain_plaintext(tmp_path):
    store = VlcCredentialStore(tmp_path)

    token = store.encrypt("camera-user", "camera-password")

    assert "camera-user" not in token and "camera-password" not in token
    assert store.decrypt(token) == ("camera-user", "camera-password")
    replacement = "A" if token[-10] != "A" else "B"
    assert store.decrypt(token[:-10] + replacement + token[-9:]) is None


def test_vlc_launch_is_credential_free_by_default_and_uses_encrypted_cookie_when_saved():
    started = datetime.now(timezone.utc).isoformat()
    event_id = db.transition(1, "binary_sensor.vlc_test", "off", "on", started, {}, 5, 10, "utc", 0, 5)
    db.transition(1, "binary_sensor.vlc_test", "on", "off", datetime.now(timezone.utc).isoformat(), {}, 5, 10, "utc", 0, 5)

    with TestClient(app, base_url="https://testserver") as client:
        public = client.get(f"/api/events/{event_id}").json()
        anonymous = client.get(f"/api/events/{event_id}/vlc", follow_redirects=False)
        saved = client.post(
            "/api/vlc-credentials", json={"username": "u@ser", "password": "p:ass"},
        )
        configured = client.get("/api/vlc-credentials")
        authenticated = client.get(f"/api/events/{event_id}/vlc", follow_redirects=False)
        cleared = client.delete("/api/vlc-credentials")

    assert public["credential_free_rtsp_url"].startswith("rtsp://nvr.example.test:554/")
    assert public["redacted_rtsp_url"] == public["credential_free_rtsp_url"]
    assert "*" not in public["credential_free_rtsp_url"]
    assert "@" not in public["credential_free_rtsp_url"].split("?", 1)[0]
    assert "fixture-user" not in str(public) and "fixture-password" not in str(public)
    assert anonymous.status_code == 302
    assert anonymous.headers["location"].startswith("rtsp://nvr.example.test:554/")
    assert "@" not in anonymous.headers["location"].split("?", 1)[0]
    cookie = saved.headers["set-cookie"]
    assert saved.headers["cache-control"] == "no-store" and configured.headers["cache-control"] == "no-store"
    assert saved.status_code == 200 and "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" in cookie
    assert COOKIE_NAME in cookie and "u@ser" not in cookie and "p:ass" not in cookie
    assert configured.json() == {"configured": True}
    assert "u%40ser:p%3Aass@" in authenticated.headers["location"]
    assert cleared.json() == {"configured": False}
