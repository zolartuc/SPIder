"""Turns raw scan results into a device inventory + event history."""

import logging
from datetime import datetime, timedelta, timezone

from .. import alerts, settings_store
from ..config import get_settings
from ..db import add_event, connect, now
from . import scanner, vendors

log = logging.getLogger("spider.watchtower")

last_scan: dict = {"at": None, "method": None, "subnet": None, "found": 0, "error": None}


def _forget(conn, mac: str) -> None:
    for table in ("devices", "device_facts", "blocks"):
        conn.execute(f"DELETE FROM {table} WHERE mac = ?", (mac,))
    conn.execute("DELETE FROM events WHERE mac = ?", (mac,))


def tidy_self() -> int:
    """Remove copies of this machine that older versions recorded by mistake.

    * anything at a 127.x address (this machine talking to itself, never a real device);
    * this machine under one of its other cards' hardware IDs (usually Docker's 02:42:...).
    A name or "known" mark on a copy is moved to the real entry if that has none.
    """
    me_ip = scanner.local_ip()
    real = scanner.own_mac(me_ip)
    others = {c["mac"] for c in scanner.own_interfaces().values()} - {real}
    removed = 0
    with connect() as conn:
        rows = conn.execute("SELECT * FROM devices").fetchall()
        copies = [r for r in rows if r["mac"] != real and ((r["ip"] or "").startswith("127.") or r["mac"] in others)]
        keep = conn.execute("SELECT * FROM devices WHERE mac = ?", (real,)).fetchone()
        for r in copies:
            if keep:
                conn.execute("UPDATE devices SET name = COALESCE(name, ?), kind_user = COALESCE(kind_user, ?), "
                             "known = MAX(known, ?), first_seen = MIN(first_seen, ?) WHERE mac = ?",
                             (r["name"], r["kind_user"], r["known"], r["first_seen"], real))
            _forget(conn, r["mac"])
            removed += 1
    if removed:
        log.info("Removed %d stray cop%s of this machine from the device list", removed, "y" if removed == 1 else "ies")
    return removed


def run_scan() -> dict:
    s = settings_store.load()
    if get_settings().demo:
        # Demo mode: don't touch the real network, just report what's in the database.
        with connect() as conn:
            online = conn.execute("SELECT COUNT(*) FROM devices WHERE online = 1").fetchone()[0]
        last_scan.update(at=now(), method="arp", subnet=s.scan_subnet or "192.168.1.0/24", found=online, error=None)
        return last_scan
    subnet = s.scan_subnet or None
    try:
        subnet = subnet or scanner.default_subnet()
        hosts, method = scanner.scan(subnet, s.scan_method)
    except scanner.NotOnNetwork as exc:
        log.info("%s", exc)
        last_scan.update(at=now(), error=str(exc), subnet=subnet, waiting=True)
        return last_scan
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

    try:
        tidy_self()
    except Exception:
        log.exception("Couldn't tidy SPIder's own entries")
    last_scan.update(at=ts, method=method, subnet=subnet, found=len(hosts), error=None, waiting=False)
    log.info("Scan via %s found %d hosts (%d new)", method, len(hosts), len(new_devices))
    return last_scan
