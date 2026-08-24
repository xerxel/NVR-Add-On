from pathlib import Path

import app.storage as storage
from app.storage import cpu_report, storage_report


def sized(path: Path, size: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_storage_report_categorises_addon_files_without_double_counting(tmp_path):
    system = tmp_path / "app"
    data = system / "data"
    sized(system / "app" / "main.py", 100)
    sized(data / "media" / "thumbs" / "event.jpg", 20)
    sized(data / "media" / "videos" / "event.mp4", 30)
    sized(data / "events.db", 40)
    sized(data / "events.db-wal", 5)
    sized(data / "logs" / "addon.log", 10)
    sized(data / "tmp" / "working.tmp", 7)
    sized(data / "channels.json", 2)

    result = storage_report(
        system_root=system, data_root=data, thumbnails=data / "media" / "thumbs",
        videos=data / "media" / "videos", temporary=data / "tmp", database=data / "events.db",
        cache_limit_mb=2,
    )
    categories = {item["key"]: item for item in result["categories"]}

    assert result["total_bytes"] == 214
    assert result["cache_limit_bytes"] == 2 * 1024 * 1024
    assert categories["system"]["bytes"] == 100
    assert categories["thumbnails"]["bytes"] == 20
    assert categories["videos"]["bytes"] == 30
    assert categories["database"]["bytes"] == 45
    assert categories["logs"]["bytes"] == 10
    assert categories["temporary"]["bytes"] == 7
    assert categories["other"]["bytes"] == 2
    assert round(sum(item["percent"] for item in result["categories"]), 1) == 100.0


def test_cpu_report_separates_addon_and_system_usage(monkeypatch):
    system_samples = iter(((1000, 400), (1200, 450)))
    process_samples = iter((10.0, 10.2))
    wall_samples = iter((100.0, 101.0))
    monkeypatch.setattr(storage, "_system_cpu_times", lambda: next(system_samples))
    monkeypatch.setattr(storage.time, "process_time", lambda: next(process_samples))
    monkeypatch.setattr(storage.time, "monotonic", lambda: next(wall_samples))
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)
    monkeypatch.setattr(storage.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(storage.os, "getloadavg", lambda: (1.25, 1.0, 0.75), raising=False)

    result = cpu_report()

    assert result["addon_percent"] == 5.0
    assert result["system_percent"] == 75.0
    assert result["logical_cpus"] == 4
    assert result["load_average_1m"] == 1.25
