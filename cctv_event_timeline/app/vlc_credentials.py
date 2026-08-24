import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

COOKIE_NAME = "cctv_timeline_vlc_credentials"


class VlcCredentialStore:
    def __init__(self, data_dir: Path):
        self.key_path = data_dir / "vlc-cookie.key"
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self.key_path.open("xb") as output:
                output.write(Fernet.generate_key())
        except FileExistsError:
            pass
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self.fernet = Fernet(self.key_path.read_bytes())

    def encrypt(self, username: str, password: str) -> str:
        payload = json.dumps({"username": username, "password": password}, separators=(",", ":")).encode()
        return self.fernet.encrypt(payload).decode()

    def decrypt(self, token: str | None) -> tuple[str, str] | None:
        if not token:
            return None
        try:
            payload = json.loads(self.fernet.decrypt(token.encode()))
            username, password = payload["username"], payload["password"]
            if not isinstance(username, str) or not isinstance(password, str):
                return None
            return username, password
        except (InvalidToken, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
            return None
