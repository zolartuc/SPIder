import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    mac         TEXT PRIMARY KEY,
    ip          TEXT,
    hostname    TEXT,
    vendor      TEXT,
    name        TEXT,
    known       INTEGER NOT NULL DEFAULT 0,
    online      INTEGER NOT NULL DEFAULT 1,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    type    TEXT NOT NULL,   -- new_device | online | offline | ip_changed
    mac     TEXT,
    detail  TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts DESC);

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash     TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'viewer',   -- admin | viewer
    theme       TEXT,
    created     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created     TEXT NOT NULL,
    expires     TEXT NOT NULL
);

-- Traffic: lookups per device, per site (base domain), per hour.
-- device_key is the device's MAC when SPIder knows it, otherwise "ip:<address>".
CREATE TABLE IF NOT EXISTS traffic (
    device_key  TEXT NOT NULL,
    site        TEXT NOT NULL,
    hour        TEXT NOT NULL,
    count       INTEGER NOT NULL,
    PRIMARY KEY (device_key, site, hour)
);
CREATE INDEX IF NOT EXISTS idx_traffic_hour ON traffic (hour);

-- First time each device was seen talking to each site (kept forever: this is "normal").
CREATE TABLE IF NOT EXISTS traffic_sites (
    device_key  TEXT NOT NULL,
    site        TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    PRIMARY KEY (device_key, site)
);

-- When SPIder first saw any traffic from a device (starts its learning period).
CREATE TABLE IF NOT EXISTS traffic_devices (
    device_key  TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL
);

-- Devices whose internet is paused (until a time) or blocked (until NULL = until someone unblocks).
CREATE TABLE IF NOT EXISTS blocks (
    mac         TEXT PRIMARY KEY,
    until       TEXT,
    created     TEXT NOT NULL,
    by_user     TEXT
);

-- Clues about what a device is, from every identification source (dhcp, mdns, ssdp, ports, bambu...).
CREATE TABLE IF NOT EXISTS device_facts (
    mac         TEXT NOT NULL,
    source      TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    seen        TEXT NOT NULL,
    PRIMARY KEY (mac, source, key)
);

CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL      -- JSON
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db() -> None:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Added later: whether someone has looked at an event (clears "unusual" flags on the web).
        if "ack" not in [r[1] for r in conn.execute("PRAGMA table_info(events)")]:
            conn.execute("ALTER TABLE events ADD COLUMN ack INTEGER NOT NULL DEFAULT 0")
        # Added later: what SPIder thinks each device is, and an admin's correction.
        have = [r[1] for r in conn.execute("PRAGMA table_info(devices)")]
        if "layout" not in [r[1] for r in conn.execute("PRAGMA table_info(users)")]:
            conn.execute("ALTER TABLE users ADD COLUMN layout TEXT")
        for col, kind in (("kind", "TEXT"), ("model", "TEXT"), ("confidence", "TEXT"),
                          ("identity", "TEXT"), ("kind_user", "TEXT"), ("identified_at", "TEXT")):
            if col not in have:
                conn.execute(f"ALTER TABLE devices ADD COLUMN {col} {kind}")


@contextmanager
def connect():
    conn = sqlite3.connect(get_settings().db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_event(conn: sqlite3.Connection, type_: str, mac: str | None, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO events (ts, type, mac, detail) VALUES (?, ?, ?, ?)",
        (now(), type_, mac, detail),
    )
