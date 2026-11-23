"""Turns raw scan results into a device inventory + event history."""

import logging
from datetime import datetime, timedelta, timezone

from .. import alerts, settings_store
from ..config import get_settings
from ..db import add_event, connect, now
from . import scanner, vendors

log = logging.getLogger("spider.watchtower")

last_scan: dict = {"at": None, "method": None, "subnet": None, "found": 0, "error": None}


def run_scan() -> dict:
    s = settings_store.load()
    if get_settings().demo:
        # Demo mode: don't touch the real network, just report what's in the database.
        with connect() as conn:
            online = conn.execute("SELECT COUNT(*) FROM devices WHERE online = 1").fetchone()[0]
        last_scan.update(at=now(), method="arp", subnet=s.scan_subnet or "192.168.1.0/24", found=online, error=None)
        return last_scan
    subnet = s.scan_subnet or scanner.default_subnet()
    try:
        hosts, method = scanner.scan(subnet, s.scan_method)
    except Exception as exc:
        log.exception("Scan failed")
        last_scan.update(at=now(), error=str(exc), subnet=subnet)
        return last_scan

    ts = now()
    new_devices = []

    with connect() as conn:
        for h in hosts:
            row = conn.execute("SELECT * FROM devices WHERE mac = ?", (h.mac,)).fetchone()
            if row is None:
                vendor = vendors.lookup(h.mac)
                conn.execute(
                    "INSERT INTO devices (mac, ip, hostname, vendor, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (h.mac, h.ip, h.hostname, vendor, ts, ts),
                )
                add_event(conn, "new_device", h.mac, f"{h.ip} {h.hostname or ''} {vendor or ''}".strip())
                new_devices.append((h, vendor))
                continue

            if row["ip"] != h.ip:
                add_event(conn, "ip_changed", h.mac, f"{row['ip']} -> {h.ip}")
            if not row["online"]:
                add_event(conn, "online", h.mac, h.ip)
            conn.execute(
                "UPDATE devices SET ip = ?, hostname = COALESCE(?, hostname), online = 1, last_seen = ? "
                "WHERE mac = ?",
                (h.ip, h.hostname, ts, h.mac),
            )

        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=s.offline_after_minutes)).isoformat(
            timespec="seconds"
        )
        gone = conn.execute(
            "SELECT mac, ip FROM devices WHERE online = 1 AND last_seen < ?", (cutoff,)
        ).fetchall()
        for row in gone:
            conn.execute("UPDATE devices SET online = 0 WHERE mac = ?", (row["mac"],))
            add_event(conn, "offline", row["mac"], row["ip"])

    for h, vendor in new_devices if s.alert_new_device else []:
        alerts.send(
            "New device on your network",
            f"{h.ip}  {h.mac}\n{vendor or 'Unknown vendor'}  {h.hostname or ''}".strip(),
            tags="warning,bird",
        )

    last_scan.update(at=ts, method=method, subnet=subnet, found=len(hosts), error=None)
    log.info("Scan via %s found %d hosts (%d new)", method, len(hosts), len(new_devices))
    return last_scan
