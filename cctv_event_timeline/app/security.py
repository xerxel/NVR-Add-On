import re
from urllib.parse import quote

_URL_USERINFO = re.compile(r"(?i)(rtsp|https?)://[^/@\s]+@")
_BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+\-/]+=*")


def redact(value: object, secrets: tuple[str, ...] = ()) -> str:
    text = str(value)
    text = _URL_USERINFO.sub(lambda m: f"{m.group(1)}://***:***@", text)
    text = _BEARER.sub("Bearer ***", text)
    for secret in secrets:
        if not secret:
            continue
        for candidate in {secret, quote(secret, safe=""), quote(secret, safe="@") }:
            if candidate:
                text = text.replace(candidate, "***")
    return text


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
    if not cleaned or cleaned != value or len(cleaned) > 80:
        raise ValueError("Unsafe identifier")
    return cleaned

