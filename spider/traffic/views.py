"""Read side of Traffic: shapes SPIder's history for the Traffic page."""

import time
from collections import defaultdict
from datetime import datetime, timezone

from ..db import connect
from .collector import LEARNING, hour_of, iso, last_run
from .domains import category

WINDOW_HOURS = 24


def _hours(now: float) -> list[str]:
    return [hour_of(now - (WINDOW_HOURS - 1 - i) * 3600) for i in range(WINDOW_HOURS)]


def _device_info(conn) -> dict[str, dict]:
    """device_key -> display info, for both MAC keys and ip: keys."""
    info = {}
    for d in conn.execute("SELECT * FROM devices"):
        entry = {"mac": d["mac"], "ip": d["ip"], "name": d["name"] or d["hostname"], "vendor": d["vendor"],
                 "known": bool(d["known"]), "online": bool(d["online"])}
        info[d["mac"]] = entry
    return info


def _describe(key: str, info: dict) -> dict:
    if key in info:
        return {"key": key, **info[key]}
    ip = key[3:] if key.startswith("ip:") else None
    return {"key": key, "mac": None, "ip": ip, "name": None, "vendor": None, "known": False, "online": None}


def _learning(first_seen: str | None, now: float) -> bool:
    if not first_seen:
        return True
    return datetime.fromtimestamp(now, timezone.utc) - datetime.fromisoformat(first_seen) < LEARNING


def _first_contacts(conn, now: float, days: int, key: str | None = None) -> list[dict]:
    since = iso(now - days * 86400)
    sql = """SELECT s.device_key, s.site, s.first_seen, d.first_seen AS learned
             FROM traffic_sites s JOIN traffic_devices d USING (device_key)
             WHERE s.first_seen >= ?""" + (" AND s.device_key = ?" if key else "") + " ORDER BY s.first_seen DESC"
    out = []
    for r in conn.execute(sql, (since, key) if key else (since,)):
        if datetime.fromisoformat(r["first_seen"]) - datetime.fromisoformat(r["learned"]) >= LEARNING:
            out.append({"key": r["device_key"], "site": r["site"], "category": category(r["site"]), "first_seen": r["first_seen"]})
    return out


def overview() -> dict:
    now = time.time()
    hours = _hours(now)
    idx = {h: i for i, h in enumerate(hours)}
    with connect() as conn:
        info = _device_info(conn)
        learned = {r["device_key"]: r["first_seen"] for r in conn.execute("SELECT * FROM traffic_devices")}
        rows = conn.execute("SELECT device_key, site, hour, count FROM traffic WHERE hour >= ?", (hours[0],)).fetchall()
        contacts = _first_contacts(conn, now, 7)

    per_device = defaultdict(lambda: {"count": 0, "sites": set(), "spark": [0] * WINDOW_HOURS})
    per_category = defaultdict(int)
    all_sites = set()
    for r in rows:
        d = per_device[r["device_key"]]
        d["count"] += r["count"]
        d["sites"].add(r["site"])
        if r["hour"] in idx:
            d["spark"][idx[r["hour"]]] += r["count"]
        per_category[category(r["site"])] += r["count"]
        all_sites.add(r["site"])

    new_by_key = defaultdict(int)
    for c in contacts:
        new_by_key[c["key"]] += 1

    devices = []
    for key in set(per_device) | set(learned):
        d = per_device.get(key) or {"count": 0, "sites": set(), "spark": [0] * WINDOW_HOURS}
        devices.append({**_describe(key, info), "count": d["count"], "sites": len(d["sites"]), "spark": d["spark"],
                        "new_7d": new_by_key.get(key, 0), "learning": _learning(learned.get(key), now)})
    devices.sort(key=lambda x: -x["count"])

    for c in contacts:
        c["device"] = _describe(c["key"], info)

    return {
        "status": last_run.get("status") or "starting",
        "last_run": last_run,
        "hours": hours,
        "totals": {"lookups": sum(d["count"] for d in devices), "sites": len(all_sites),
                   "devices": sum(1 for d in devices if d["count"])},
        "devices": devices,
        "categories": sorted(({"category": k, "count": v} for k, v in per_category.items()), key=lambda x: -x["count"]),
        "first_contacts": contacts[:20],
    }


def device(key: str) -> dict | None:
    now = time.time()
    hours = _hours(now)
    idx = {h: i for i, h in enumerate(hours)}
    with connect() as conn:
        info = _device_info(conn)
        learned = conn.execute("SELECT first_seen FROM traffic_devices WHERE device_key = ?", (key,)).fetchone()
        if not learned and key not in info:
            return None
        rows = conn.execute("SELECT site, hour, count FROM traffic WHERE device_key = ? AND hour >= ?",
                            (key, hour_of(now - 7 * 86400))).fetchall()
        sites_meta = {r["site"]: dict(r) for r in conn.execute("SELECT * FROM traffic_sites WHERE device_key = ?", (key,))}
        contacts = _first_contacts(conn, now, 30, key)

    spark = [0] * WINDOW_HOURS
    today = defaultdict(int)
    week = defaultdict(int)
    for r in rows:
        week[r["site"]] += r["count"]
        if r["hour"] in idx:
            spark[idx[r["hour"]]] += r["count"]
            today[r["site"]] += r["count"]

    per_category = defaultdict(int)
    for site, n in today.items():
        per_category[category(site)] += n

    new_sites = {c["site"] for c in contacts}
    sites = sorted(
        ({"site": s, "category": category(s), "today": today.get(s, 0), "week": n,
          "first_seen": sites_meta.get(s, {}).get("first_seen"), "last_seen": sites_meta.get(s, {}).get("last_seen"),
          "new": s in new_sites}
         for s, n in week.items()),
        key=lambda x: (-x["today"], -x["week"]),
    )
    return {
        "device": {**_describe(key, info), "learning": _learning(learned["first_seen"] if learned else None, now),
                   "learning_since": learned["first_seen"] if learned else None},
        "hours": hours,
        "spark": spark,
        "count": sum(today.values()),
        "categories": sorted(({"category": k, "count": v} for k, v in per_category.items()), key=lambda x: -x["count"]),
        "sites": sites[:60],
        "first_contacts": contacts[:30],
    }


# --------------------------------------------------------------------------- flags

FLAG_HOURS = 24  # a first contact nobody has looked at yet stays "unusual" this long


def _parse_contact(detail: str) -> tuple[str, str, str]:
    """new_contact events store 'site ip category words'."""
    parts = (detail or "").split(" ")
    return (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else ""), " ".join(parts[2:])


def flags() -> list[dict]:
    """Devices doing something unusual: unchecked first contacts from the last day, newest first."""
    now = time.time()
    with connect() as conn:
        info = _device_info(conn)
        mac_by_ip = {d["ip"]: mac for mac, d in info.items() if d.get("ip")}
        rows = conn.execute(
            "SELECT mac, detail, ts FROM events WHERE type = 'new_contact' AND ack = 0 AND ts >= ? ORDER BY ts DESC",
            (iso(now - FLAG_HOURS * 3600),),
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        site, ip, cat = _parse_contact(r["detail"])
        mac = r["mac"] or mac_by_ip.get(ip)
        key = mac or f"ip:{ip}"
        f = out.setdefault(key, {**_describe(key, info), "count": 0, "sites": [], "latest": {"site": site, "category": cat, "ts": r["ts"]}})
        f["count"] += 1
        if site not in f["sites"]:
            f["sites"].append(site)
    return list(out.values())


def acknowledge(key: str) -> int:
    """Mark a device's unchecked first contacts as looked at. Returns how many were cleared."""
    with connect() as conn:
        if key.startswith("ip:"):
            ip = key[3:]
            row = conn.execute("SELECT mac FROM devices WHERE ip = ?", (ip,)).fetchone()
            mac = row["mac"] if row else None
        else:
            mac = key
            row = conn.execute("SELECT ip FROM devices WHERE mac = ?", (mac,)).fetchone()
            ip = row["ip"] if row else None
        cur = conn.execute(
            "UPDATE events SET ack = 1 WHERE type = 'new_contact' AND ack = 0 AND (mac = ? OR detail LIKE ?)",
            (mac, f"% {ip} %" if ip else "\x00"),
        )
        return cur.rowcount
