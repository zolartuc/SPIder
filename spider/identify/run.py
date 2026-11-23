"""Run identification: gather clues, then classify every device.

- every 5 minutes: classify all devices from what's known (cheap)
- every 15 minutes: ask the network (mDNS, UPnP) and Pi-hole's device names
- at most once a day per online device: the light port check
- weekly: refresh the MAC maker list
"""

import ipaddress
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .. import settings_store
from ..config import get_settings
from ..db import connect, now
from ..pihole import client as pihole
from ..watchtower import scanner, vendors
from . import classify, discover, facts, oui, probe

log = logging.getLogger("spider.identify")

ASK_EVERY = 15 * 60
PROBE_EVERY = timedelta(hours=24)
PROBES_PER_RUN = 24

_last_ask = 0.0


def _gateway() -> str | None:
    s = settings_store.load()
    try:
        net = ipaddress.ip_network(s.scan_subnet or scanner.default_subnet(), strict=False)
        return str(next(net.hosts()))
    except (ValueError, StopIteration):
        return None


def _refresh_makers() -> None:
    if not oui.refresh():
        return
    vendors.lookup.cache_clear()
    with connect() as conn:
        for d in conn.execute("SELECT mac, vendor FROM devices").fetchall():
            v = vendors.lookup(d["mac"])
            if v and v != d["vendor"]:
                conn.execute("UPDATE devices SET vendor = ? WHERE mac = ?", (v, d["mac"]))


def _ask_network() -> None:
    with connect() as conn:
        mac_by_ip = {r["ip"]: r["mac"] for r in conn.execute("SELECT ip, mac FROM devices WHERE ip IS NOT NULL")}
    for ip, info in discover.mdns().items():
        if ip in mac_by_ip:
            facts.add_many(mac_by_ip[ip], "mdns", info)
    for ip, info in discover.ssdp().items():
        if ip in mac_by_ip:
            facts.add_many(mac_by_ip[ip], "ssdp", info)
    for dev in pihole.network_devices():
        if dev["names"]:
            facts.add(dev["mac"], "pihole", "name", dev["names"][0])


def _probe_due(only: str | None = None) -> None:
    cutoff = (datetime.now(timezone.utc) - PROBE_EVERY).isoformat(timespec="seconds")
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.mac, d.ip FROM devices d
               LEFT JOIN device_facts f ON f.mac = d.mac AND f.source = 'ports' AND f.key = 'open'
               WHERE d.online = 1 AND d.ip IS NOT NULL AND (f.seen IS NULL OR f.seen < ?)""",
            (cutoff,),
        ).fetchall()
    targets = [(r["mac"], r["ip"]) for r in rows if only is None or r["mac"] == only][:PROBES_PER_RUN]
    if only and not targets:
        with connect() as conn:
            r = conn.execute("SELECT mac, ip FROM devices WHERE mac = ?", (only,)).fetchone()
        if r and r["ip"]:
            targets = [(r["mac"], r["ip"])]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (mac, _), result in zip(targets, pool.map(lambda t: probe.probe(t[1]), targets)):
            facts.add(mac, "ports", "open", result["open"] or [])  # store even "none open", so it isn't re-probed
            if not result["open"]:
                facts.add(mac, "ports", "open", "[]")
            if result.get("web"):
                facts.add(mac, "ports", "web", result["web"])


def classify_all(only: str | None = None) -> int:
    s = settings_store.load()
    ctx = classify.context_for(s, scanner.local_ip(), _gateway())
    with connect() as conn:
        devices = [dict(r) for r in conn.execute("SELECT * FROM devices" + (" WHERE mac = ?" if only else ""), (only,) if only else ())]
    changed = 0
    for d in devices:
        result = classify.classify(d, facts.for_device(d["mac"]), ctx)
        new_host = result["name"] or d["hostname"]
        with connect() as conn:
            conn.execute(
                "UPDATE devices SET kind = ?, model = ?, confidence = ?, identity = ?, identified_at = ?, hostname = ? WHERE mac = ?",
                (result["kind"], result["model"], result["confidence"], json.dumps(result["evidence"]), now(), new_host, d["mac"]),
            )
        changed += (result["kind"] != d.get("kind"))
    return changed


def run(force_ask: bool = False) -> None:
    """One identification pass (called every 5 minutes)."""
    global _last_ask
    demo = get_settings().demo
    if not demo:
        try:
            _refresh_makers()
        except Exception:
            log.exception("Maker list refresh failed")
        if force_ask or time.time() - _last_ask > ASK_EVERY:
            _last_ask = time.time()
            try:
                _ask_network()
            except Exception:
                log.exception("mDNS/UPnP discovery failed")
        try:
            _probe_due()
        except Exception:
            log.exception("Port check failed")
    n = classify_all()
    if n:
        log.info("Identification: %d device(s) changed type", n)


def identify_now(mac: str) -> dict:
    """'Identify again' for one device: fresh port check, ask the network, classify."""
    if not get_settings().demo:
        with connect() as conn:
            conn.execute("DELETE FROM device_facts WHERE mac = ? AND source = 'ports'", (mac,))
        _probe_due(only=mac)
        _ask_network()
    classify_all(only=mac)
    with connect() as conn:
        return dict(conn.execute("SELECT * FROM devices WHERE mac = ?", (mac,)).fetchone())
