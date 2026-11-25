"""JSON helpers: dataclasses, datetimes and paths to plain values."""
import dataclasses
from datetime import date, datetime
from pathlib import Path


def jsonable(v):
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return jsonable(dataclasses.asdict(v))
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Path):
        return str(v)
    return v


def steam_url(steam_id: str) -> str:
    return f"https://steamcommunity.com/profiles/{steam_id}"
