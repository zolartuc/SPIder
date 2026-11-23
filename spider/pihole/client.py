"""Minimal Pi-hole client supporting both v6 (REST API) and v5 (api.php).

Everything is normalised so the dashboard doesn't care which version is running:
    summary()  -> {"status": "ok" | "offline" | "not_configured" | "auth_failed", "version": 5|6, ...today's totals}
    details()  -> {"status": ..., "hours": [...24 hourly buckets...], "top_blocked": [...], "top_clients": [...]}
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

import httpx

from .. import settings_store

log = logging.getLogger("spider.pihole")

_sid: str | None = None  # cached v6 session id
TOP_N = 8


class AuthFailed(Exception):
    pass


def reset() -> None:
    """Forget the cached session (call when the URL or password changes)."""
    global _sid
    _sid = None


# --------------------------------------------------------------------------- transport

def _v6_get(client: httpx.Client, base: str, password: str, path: str, params: dict | None = None) -> dict | None:
    """GET a v6 endpoint, signing in if needed. Returns None if this isn't a v6 Pi-hole."""
    global _sid

    def get():
        return client.get(f"{base}{path}", params=params, headers={"X-FTL-SID": _sid} if _sid else {})

    r = get()
    if r.status_code == 404:
        return None
    if r.status_code == 401:
        auth = client.post(f"{base}/api/auth", json={"password": password})
        session = auth.json().get("session", {}) if auth.status_code == 200 else {}
        if not session.get("valid"):
            raise AuthFailed
        _sid = session.get("sid")
        r = get()
    r.raise_for_status()
    return r.json()


def _v5_get(client: httpx.Client, base: str, token: str, **params) -> dict:
    r = client.get(f"{base}/admin/api.php", params={**{k: "" if v is True else v for k, v in params.items()}, "auth": token})
    r.raise_for_status()
    data = r.json()
    if not data:  # v5 answers [] when the token is wrong
        raise AuthFailed
    return data


def _call(fn, timeout: float = 5) -> dict:
    s = settings_store.load()
    if not s.pihole_url:
        return {"status": "not_configured"}
    try:
        with httpx.Client(timeout=timeout, verify=False) as client:
            return fn(client, s.pihole_url.rstrip("/"), s.pihole_password)
    except AuthFailed:
        return {"status": "auth_failed"}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.debug("Pi-hole unreachable: %s", exc)
        return {"status": "offline", "error": str(exc)}


# --------------------------------------------------------------------------- summary

def summary() -> dict:
    def fetch(client, base, password):
        d = _v6_get(client, base, password, "/api/stats/summary")
        if d is not None:
            q, g = d.get("queries", {}), d.get("gravity", {})
            return {
                "status": "ok", "version": 6,
                "queries_today": q.get("total"),
                "blocked_today": q.get("blocked"),
                "cached_today": q.get("cached"),
                "forwarded_today": q.get("forwarded"),
                "percent_blocked": round(q.get("percent_blocked") or 0, 1),
                "domains_on_blocklist": g.get("domains_being_blocked"),
                "active_clients": d.get("clients", {}).get("active"),
            }
        d = _v5_get(client, base, password, summaryRaw=True)
        return {
            "status": "ok", "version": 5,
            "queries_today": d.get("dns_queries_today"),
            "blocked_today": d.get("ads_blocked_today"),
            "cached_today": d.get("queries_cached"),
            "forwarded_today": d.get("queries_forwarded"),
            "percent_blocked": round(float(d.get("ads_percentage_today") or 0), 1),
            "domains_on_blocklist": d.get("domains_being_blocked"),
            "active_clients": d.get("unique_clients"),
            "blocking": d.get("status"),
        }

    return _call(fetch)


# --------------------------------------------------------------------------- query log

# Answered lookups only. Anything Pi-hole blocked is ads/trackers: Pi-hole's business, not SPIder's.
_V6_ALLOWED = {"FORWARDED", "CACHE", "CACHE_STALE", "RETRIED", "RETRIED_DNSSEC", "IN_PROGRESS", "ALREADY_FORWARDED"}
_V5_ALLOWED = {2, 3, 12, 13, 14, 17}
PAGE = 5000
MAX_ROWS = 400_000  # safety cap for one catch-up


def queries(since: float, until: float) -> dict:
    """Answered lookups between two Unix times: {"status": "ok", "rows": [(ts, domain, client_ip), ...]}."""

    def fetch(client, base, password):
        rows: list[tuple[float, str, str]] = []
        first = _v6_get(client, base, password, "/api/queries",
                        {"from": int(since), "until": int(until), "length": PAGE, "start": 0, "disk": "true"})
        if first is not None:
            page, start, cursor = first, 0, first.get("cursor")
            while True:
                batch = page.get("queries", [])
                for q in batch:
                    if q.get("status") in _V6_ALLOWED and q.get("type") != "PTR":
                        rows.append((float(q["time"]), q.get("domain") or "", (q.get("client") or {}).get("ip") or ""))
                start += len(batch)
                total = page.get("recordsFiltered", start)
                if not batch or start >= total or start >= MAX_ROWS:
                    break
                params = {"from": int(since), "until": int(until), "length": PAGE, "start": start, "disk": "true"}
                if cursor is not None:
                    params["cursor"] = cursor
                page = _v6_get(client, base, password, "/api/queries", params) or {}
            return {"status": "ok", "version": 6, "rows": rows}

        data = _v5_get(client, base, password, getAllQueries=True, **{"from": int(since), "until": int(until)})
        for r in data.get("data", []):
            try:
                ts, qtype, domain, client_ip, status = float(r[0]), r[1], r[2], r[3], int(r[4])
            except (ValueError, IndexError, TypeError):
                continue
            if status in _V5_ALLOWED and qtype != "PTR":
                rows.append((ts, domain, client_ip.split("|")[-1]))
        return {"status": "ok", "version": 5, "rows": rows}

    return _call(fetch, timeout=60)


# --------------------------------------------------------------------------- details

def _hourly(points: list[tuple[float, int, int, int]], split: bool) -> list[dict]:
    """Fold (timestamp, total, blocked, cached) samples into the last 24 hourly buckets."""
    now_hour = int(time.time()) // 3600 * 3600
    start = now_hour - 23 * 3600
    buckets = defaultdict(lambda: [0, 0, 0])
    for ts, total, blocked, cached in points:
        h = int(ts) // 3600 * 3600
        if start <= h <= now_hour:
            b = buckets[h]
            b[0] += total; b[1] += blocked; b[2] += cached
    out = []
    for h in range(start, now_hour + 1, 3600):
        total, blocked, cached = buckets[h]
        cached = cached if split else 0
        out.append({
            "t": datetime.fromtimestamp(h, timezone.utc).isoformat(timespec="seconds"),
            "total": total,
            "blocked": blocked,
            "cached": cached,
            "forwarded": max(0, total - blocked - cached),
        })
    return out


def details() -> dict:
    def fetch(client, base, password):
        hist = _v6_get(client, base, password, "/api/history")
        if hist is not None:
            top = _v6_get(client, base, password, "/api/stats/top_domains", {"blocked": "true", "count": TOP_N}) or {}
            clients = _v6_get(client, base, password, "/api/stats/top_clients", {"count": TOP_N}) or {}
            points = [(p["timestamp"], p.get("total", 0), p.get("blocked", 0), p.get("cached", 0)) for p in hist.get("history", [])]
            return {
                "status": "ok", "version": 6, "split": True,
                "hours": _hourly(points, split=True),
                "top_blocked": [{"domain": d["domain"], "count": d["count"]} for d in top.get("domains", [])][:TOP_N],
                "top_clients": [{"ip": c.get("ip"), "name": c.get("name") or None, "count": c["count"]}
                                for c in clients.get("clients", [])][:TOP_N],
            }

        # v5: no cache split in the history, so the chart shows allowed vs blocked.
        over = _v5_get(client, base, password, overTimeData10mins=True)
        items = _v5_get(client, base, password, topItems=TOP_N)
        sources = _v5_get(client, base, password, topClients=TOP_N)
        ads = over.get("ads_over_time", {})
        points = [(float(ts), int(total), int(ads.get(ts, 0)), 0) for ts, total in over.get("domains_over_time", {}).items()]
        top_clients = []
        for key, count in sources.get("top_sources", {}).items():
            name, _, ip = key.partition("|")
            top_clients.append({"ip": ip or name, "name": name if ip else None, "count": count})
        return {
            "status": "ok", "version": 5, "split": False,
            "hours": _hourly(points, split=False),
            "top_blocked": [{"domain": d, "count": c} for d, c in items.get("top_ads", {}).items()][:TOP_N],
            "top_clients": top_clients[:TOP_N],
        }

    return _call(fetch)
