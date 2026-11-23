"""Pull new lookups from Pi-hole into SPIder's own traffic history.

Runs every minute. For each answered lookup it works out which device asked
(by IP, then MAC from the device list), folds the name to its site
(googlevideo.com rather than rr3---sn-aigl6nek.googlevideo.com) and tallies it
per hour. After a device's learning period, a site it has never contacted
before is recorded as a "first contact".
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import alerts, settings_store
from ..config import get_settings
from ..db import connect
from ..pihole import client as pihole
from .domains import base_domain, category, is_noise

log = logging.getLogger("spider.traffic")

LEARNING = timedelta(hours=24)   # quiet period while SPIder learns a device's normal
RETENTION_DAYS = 30               # hourly tallies kept this long; first contacts kept forever
MAX_CATCH_UP = 7 * 86400          # never reach back further than a week
ALERT_LIMIT = 5                   # per run, so a burst doesn't spam your phone

last_run: dict = {"at": None, "status": None, "rows": 0, "new_contacts": 0, "error": None}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def hour_of(ts: float) -> str:
    return iso(int(ts) // 3600 * 3600)


def _meta(conn, key: str, value: str | None = None) -> str | None:
    if value is None:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))
    return value


def _adopt_ip_keys(conn) -> None:
    """Traffic first seen by IP moves onto the device's MAC once the scanner knows it."""
    for d in conn.execute("SELECT mac, ip FROM devices WHERE ip IS NOT NULL").fetchall():
        old = f"ip:{d['ip']}"
        if not conn.execute("SELECT 1 FROM traffic_devices WHERE device_key = ?", (old,)).fetchone():
            continue
        conn.execute("""INSERT INTO traffic (device_key, site, hour, count)
                        SELECT ?, site, hour, count FROM traffic WHERE device_key = ?
                        ON CONFLICT(device_key, site, hour) DO UPDATE SET count = count + excluded.count""", (d["mac"], old))
        conn.execute("""INSERT INTO traffic_sites (device_key, site, first_seen, last_seen)
                        SELECT ?, site, first_seen, last_seen FROM traffic_sites WHERE device_key = ?
                        ON CONFLICT(device_key, site) DO UPDATE SET
                          first_seen = min(first_seen, excluded.first_seen), last_seen = max(last_seen, excluded.last_seen)""",
                     (d["mac"], old))
        conn.execute("""INSERT INTO traffic_devices (device_key, first_seen)
                        SELECT ?, first_seen FROM traffic_devices WHERE device_key = ?
                        ON CONFLICT(device_key) DO UPDATE SET first_seen = min(first_seen, excluded.first_seen)""",
                     (d["mac"], old))
        for table in ("traffic", "traffic_sites", "traffic_devices"):
            conn.execute(f"DELETE FROM {table} WHERE device_key = ?", (old,))


def run() -> dict:
    s = settings_store.load()
    if not s.pihole_url:
        last_run.update(at=iso(time.time()), status="not_configured", rows=0, new_contacts=0, error=None)
        return last_run

    now = time.time()
    backfill_hours = 168 if get_settings().demo else 24
    with connect() as conn:
        stored = _meta(conn, "traffic_last_ts")
    first_ever = stored is None
    since = float(stored) if stored else now - backfill_hours * 3600
    since = max(since, now - MAX_CATCH_UP)

    res = pihole.queries(since, now)
    if res.get("status") != "ok":
        last_run.update(at=iso(now), status=res.get("status"), rows=0, new_contacts=0, error=res.get("error"))
        return last_run

    rows = sorted(r for r in res["rows"] if r[0] > since)
    with connect() as conn:
        _adopt_ip_keys(conn)
        mac_by_ip = {r["ip"]: r["mac"] for r in conn.execute("SELECT ip, mac FROM devices WHERE ip IS NOT NULL")}
        known_devices = {r["device_key"]: r["first_seen"] for r in conn.execute("SELECT * FROM traffic_devices")}

        counts: dict[tuple[str, str, str], int] = defaultdict(int)
        seen: dict[tuple[str, str], list[float]] = {}
        device_first: dict[str, float] = {}

        for ts, domain, ip in rows:
            if not ip or ip in ("127.0.0.1", "::1") or is_noise(domain):
                continue
            key = mac_by_ip.get(ip, f"ip:{ip}")
            site = base_domain(domain)
            counts[(key, site, hour_of(ts))] += 1
            span = seen.setdefault((key, site), [ts, ts])
            span[1] = ts
            device_first.setdefault(key, ts)

        for key, ts in device_first.items():
            if key not in known_devices:
                conn.execute("INSERT OR IGNORE INTO traffic_devices (device_key, first_seen) VALUES (?, ?)", (key, iso(ts)))
                known_devices[key] = iso(ts)

        new_contacts = []
        existing = {(r["device_key"], r["site"]) for r in conn.execute("SELECT device_key, site FROM traffic_sites")}
        for (key, site), (first, last) in sorted(seen.items(), key=lambda kv: kv[1][0]):
            if (key, site) in existing:
                conn.execute("UPDATE traffic_sites SET last_seen = max(last_seen, ?) WHERE device_key = ? AND site = ?",
                             (iso(last), key, site))
                continue
            conn.execute("INSERT INTO traffic_sites (device_key, site, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                         (key, site, iso(first), iso(last)))
            learned_since = datetime.fromisoformat(known_devices[key])
            if datetime.fromtimestamp(first, timezone.utc) - learned_since >= LEARNING:
                mac = None if key.startswith("ip:") else key
                ip = key[3:] if key.startswith("ip:") else next((i for i, m in mac_by_ip.items() if m == key), "")
                conn.execute("INSERT INTO events (ts, type, mac, detail) VALUES (?, 'new_contact', ?, ?)",
                             (iso(first), mac, f"{site} {ip} {category(site)}".strip()))
                new_contacts.append((key, site, ip))

        conn.executemany(
            """INSERT INTO traffic (device_key, site, hour, count) VALUES (?, ?, ?, ?)
               ON CONFLICT(device_key, site, hour) DO UPDATE SET count = count + excluded.count""",
            [(k, site, h, c) for (k, site, h), c in counts.items()],
        )
        if rows:
            _meta(conn, "traffic_last_ts", repr(rows[-1][0]))
        elif first_ever:
            _meta(conn, "traffic_last_ts", repr(now))
        conn.execute("DELETE FROM traffic WHERE hour < ?", (hour_of(now - RETENTION_DAYS * 86400),))

        names = {r["mac"]: r["name"] or r["hostname"] or r["vendor"] for r in conn.execute("SELECT * FROM devices")}

    # Alert only about genuinely live first contacts, not ones found while catching up.
    if s.alert_new_contact and not first_ever:
        for key, site, ip in new_contacts[:ALERT_LIMIT]:
            who = names.get(key) or f"A device at {ip}"
            alerts.send("First contact", f"{who} contacted {site} for the first time ({category(site)}).", tags="globe_with_meridians")
        if len(new_contacts) > ALERT_LIMIT:
            alerts.send("First contacts", f"…and {len(new_contacts) - ALERT_LIMIT} more. See Activity in SPIder.")

    last_run.update(at=iso(now), status="ok", rows=len(rows), new_contacts=len(new_contacts), error=None)
    if rows:
        log.info("Traffic: %d lookups, %d first contacts", len(rows), len(new_contacts))
    return last_run
