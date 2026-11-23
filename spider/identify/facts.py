"""Store and read identification clues."""

import json

from ..db import connect, now


def add(mac: str, source: str, key: str, value) -> None:
    """Remember one clue. Lists and dicts are stored as JSON."""
    if value in (None, "", [], {}):
        return
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True)
    with connect() as conn:
        conn.execute(
            "INSERT INTO device_facts (mac, source, key, value, seen) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(mac, source, key) DO UPDATE SET value = excluded.value, seen = excluded.seen",
            (mac.lower(), source, key, value[:2000], now()),
        )


def add_many(mac: str, source: str, values: dict) -> None:
    for k, v in values.items():
        add(mac, source, k, v)


def for_device(mac: str) -> dict[str, dict[str, str]]:
    """{source: {key: value}} for one device."""
    out: dict[str, dict[str, str]] = {}
    with connect() as conn:
        for r in conn.execute("SELECT source, key, value FROM device_facts WHERE mac = ?", (mac.lower(),)):
            out.setdefault(r["source"], {})[r["key"]] = r["value"]
    return out


def mac_for_ip(ip: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT mac FROM devices WHERE ip = ?", (ip,)).fetchone()
    return row["mac"] if row else None
