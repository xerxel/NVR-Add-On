import asyncio
import json
import os
import random
from datetime import datetime, timedelta, timezone

import httpx
import websockets


class HomeAssistantClient:
    def __init__(self):
        self.token = os.getenv("SUPERVISOR_TOKEN", "")
        self.base = os.getenv("HA_API_URL", "http://supervisor/core/api")
        self.ws_url = os.getenv("HA_WS_URL", "ws://supervisor/core/websocket")
        self.last_connected: str | None = None

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def info(self):
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self.base}/config", headers=self.headers); r.raise_for_status()
            data = r.json()
            return {"version": data.get("version"), "time_zone": data.get("time_zone"), "location_name": data.get("location_name")}

    async def entities(self):
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self.base}/states", headers=self.headers); r.raise_for_status()
            states = r.json()
        return {"motion": [{"entity_id": x["entity_id"], "state": x["state"], "name": x.get("attributes", {}).get("friendly_name")}
                           for x in states if x["entity_id"].startswith("binary_sensor.") and
                           (x.get("attributes", {}).get("device_class") in {"motion", "occupancy"} or "motion" in x["entity_id"])],
                "cameras": [{"entity_id": x["entity_id"], "state": x["state"], "name": x.get("attributes", {}).get("friendly_name")}
                            for x in states if x["entity_id"].startswith("camera.")]}

    async def snapshot(self, entity_id: str) -> tuple[bytes, str]:
        if not entity_id.startswith("camera."):
            raise ValueError("Invalid camera entity")
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{self.base}/camera_proxy/{entity_id}", headers=self.headers); r.raise_for_status()
            return r.content, r.headers.get("content-type", "image/jpeg")

    async def history(self, entity_id: str, hours: int):
        since = (datetime.now(timezone.utc) - timedelta(hours=min(hours, 168))).isoformat()
        params = {"filter_entity_id": entity_id, "minimal_response": "1", "no_attributes": "1"}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{self.base}/history/period/{since}", params=params, headers=self.headers); r.raise_for_status()
            return r.json()[0] if r.json() else []

    async def subscribe(self, callback, stop: asyncio.Event):
        attempt = 0
        while not stop.is_set():
            try:
                async with websockets.connect(self.ws_url, open_timeout=15) as ws:
                    await ws.recv()
                    await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
                    auth = json.loads(await ws.recv())
                    if auth.get("type") != "auth_ok": raise RuntimeError("Home Assistant WebSocket authentication failed")
                    await ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
                    self.last_connected = datetime.now(timezone.utc).isoformat(); attempt = 0
                    while not stop.is_set():
                        message = json.loads(await asyncio.wait_for(ws.recv(), 45))
                        if message.get("type") == "event": await callback(message["event"])
            except asyncio.CancelledError: raise
            except Exception:
                attempt += 1
                await asyncio.sleep(min(60, 2 ** min(attempt, 5)) + random.random())

