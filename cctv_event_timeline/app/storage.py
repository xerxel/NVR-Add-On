import os
import shutil
import time
from pathlib import Path

CATEGORIES = (
    ("system", "System files"),
    ("thumbnails", "Thumbnails"),
    ("videos", "Videos"),
    ("database", "Database"),
    ("logs", "Logs"),
    ("temporary", "Temporary files"),
    ("other", "Other data"),
)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _files(root: Path):
    if not root.exists():
        return
    for directory, names, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        names[:] = [name for name in names if not (current / name).is_symlink()]
        for name in filenames:
            path = current / name
            if not path.is_symlink():
                yield path


def _add(totals: dict, category: str, path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    totals[category]["bytes"] += size
    totals[category]["file_count"] += 1


def storage_report(*, system_root: Path, data_root: Path, thumbnails: Path, videos: Path,
                   temporary: Path, database: Path, cache_limit_mb: int) -> dict:
    roots = {name: path.resolve() for name, path in {
        "system": system_root, "data": data_root, "thumbnails": thumbnails,
        "videos": videos, "temporary": temporary, "database": database,
    }.items()}
    totals = {key: {"key": key, "label": label, "bytes": 0, "file_count": 0}
              for key, label in CATEGORIES}

    for path in _files(roots["system"]):
        resolved = path.resolve()
        if not _within(resolved, roots["data"]):
            _add(totals, "system", path)

    database_names = {roots["database"].name, roots["database"].name + "-wal", roots["database"].name + "-shm"}
    for path in _files(roots["data"]):
        resolved = path.resolve()
        if _within(resolved, roots["thumbnails"]):
            category = "thumbnails"
        elif _within(resolved, roots["videos"]):
            category = "videos"
        elif path.name in database_names and path.parent.resolve() == roots["database"].parent:
            category = "database"
        elif _within(resolved, roots["temporary"]):
            category = "temporary"
        elif path.suffix.lower() == ".log" or "logs" in {part.lower() for part in path.parts}:
            category = "logs"
        else:
            category = "other"
        _add(totals, category, path)

    total_bytes = sum(item["bytes"] for item in totals.values())
    categories = []
    for key, _ in CATEGORIES:
        item = totals[key]
        item["percent"] = round(item["bytes"] * 100 / total_bytes, 2) if total_bytes else 0.0
        categories.append(item)

    try:
        disk = shutil.disk_usage(roots["data"])
        filesystem = {"capacity_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free}
    except OSError:
        filesystem = None
    return {
        "total_bytes": total_bytes,
        "cache_limit_bytes": max(0, cache_limit_mb) * 1024 * 1024,
        "categories": categories,
        "filesystem": filesystem,
    }


def _system_cpu_times() -> tuple[int, int] | None:
    try:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        values = [int(value) for value in fields]
        return sum(values), values[3] + (values[4] if len(values) > 4 else 0)
    except (OSError, ValueError, IndexError):
        return None


def cpu_report(sample_seconds: float = 0.2) -> dict:
    """Return short-sample process and whole-system CPU percentages."""
    logical_cpus = max(1, os.cpu_count() or 1)
    system_before = _system_cpu_times()
    process_before = time.process_time()
    wall_before = time.monotonic()
    time.sleep(max(0.05, min(sample_seconds, 1.0)))
    elapsed = max(time.monotonic() - wall_before, 0.001)
    process_percent = min(100.0, max(0.0, (time.process_time() - process_before) * 100 / elapsed / logical_cpus))

    system_after = _system_cpu_times()
    system_percent = None
    if system_before and system_after:
        total_delta = system_after[0] - system_before[0]
        idle_delta = system_after[1] - system_before[1]
        if total_delta > 0:
            system_percent = min(100.0, max(0.0, (total_delta - idle_delta) * 100 / total_delta))
    try:
        load_average = os.getloadavg()[0]
    except (AttributeError, OSError):
        load_average = None
    return {
        "addon_percent": round(process_percent, 1),
        "system_percent": round(system_percent, 1) if system_percent is not None else None,
        "logical_cpus": logical_cpus,
        "load_average_1m": round(load_average, 2) if load_average is not None else None,
        "sample_seconds": round(elapsed, 3),
    }
