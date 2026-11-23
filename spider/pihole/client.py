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
    """Pi-hole refused the password. The message, if any, is Pi-hole's own reason."""


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
        body = auth.json() if "json" in auth.headers.get("content-type", "") else {}
        session = body.get("session", {}) if auth.status_code == 200 else {}
        if not session.get("valid"):
            if auth.status_code == 429:
                raise AuthFailed("Pi-hole has too many signed-in sessions right now. Wait a few minutes, or raise its session limit.")
            err = body.get("error") if isinstance(body.get("error"), dict) else {}
            raise AuthFailed(session.get("message") or err.get("message") or "")
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
    except AuthFailed as exc:
        return {"status": "auth_failed", "error": str(exc) or "Pi-hole said the password is wrong."}
    except Exception as exc:  # anything else: explain it in plain words
        log.debug("Pi-hole problem: %r", exc)
        return {"status": "offline", "error": explain(exc, s.pihole_url)}


def explain(exc: Exception, url: str) -> str:
    """Turn a connection error into something a person can act on."""
    text = str(exc)
    other = url.replace("https://", "http://", 1) if url.startswith("https://") else url.replace("http://", "https://", 1)
    if isinstance(exc, httpx.ConnectTimeout) or isinstance(exc, httpx.TimeoutException):
        return f"Nothing answered at {url} in time. Check the address and port, and that the Pi-hole is switched on."
    if "SSL" in text or "TLS" in text or "certificate" in text.lower():
        return f"That port didn't speak HTTPS. Try {other} instead."
    if isinstance(exc, httpx.ConnectError):
        return f"Couldn't connect to {url}. Check the address and port (the port Pi-hole's web page uses), and that the Pi-hole is on."
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Pi-hole answered with an error (HTTP {exc.response.status_code}). Check the address points at Pi-hole's web interface."
    if isinstance(exc, ValueError):
        return f"Something answered at {url}, but it isn't Pi-hole's API. Check the port, and use the address without /admin."
    return f"Couldn't talk to the Pi-hole: {text or type(exc).__name__}"


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


# --------------------------------------------------------------------------- device access (v6 only)
#
# SPIder pauses a device by putting it in its own Pi-hole group that answers nothing:
#   group "SPIder paused"  +  a deny regex ".*" attached ONLY to that group.
# The device keeps its Default group too, so taking it out of "SPIder paused" restores it exactly.

from urllib.parse import quote

PAUSE_GROUP = "SPIder paused"
CATCH_ALL = ".*"
SPIDER_TAG = "Added by SPIder"


class NotSupported(Exception):
    pass


def _v6_send(client: httpx.Client, base: str, password: str, method: str, path: str, body: dict | None = None):
    global _sid

    def go():
        return client.request(method, f"{base}{path}", json=body, headers={"X-FTL-SID": _sid} if _sid else {})

    r = go()
    if r.status_code == 401:
        auth = client.post(f"{base}/api/auth", json={"password": password})
        body = auth.json() if "json" in auth.headers.get("content-type", "") else {}
        session = body.get("session", {}) if auth.status_code == 200 else {}
        if not session.get("valid"):
            if auth.status_code == 429:
                raise AuthFailed("Pi-hole has too many signed-in sessions right now. Wait a few minutes, or raise its session limit.")
            err = body.get("error") if isinstance(body.get("error"), dict) else {}
            raise AuthFailed(session.get("message") or err.get("message") or "")
        _sid = session.get("sid")
        r = go()
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json() if r.content else {}


def _catch_all(client, base, password) -> dict | None:
    rules = _v6_send(client, base, password, "GET", "/api/domains/deny/regex") or {}
    return next((d for d in rules.get("domains", []) if d.get("domain") == CATCH_ALL), None)


def _pause_group(client, base, password) -> int:
    """Find or create the pause group and its catch-all rule, and prove the rule only touches that group.

    Pi-hole allows one ".*" deny-regex entry. If it's on any group other than SPIder's (above all
    Default, id 0) it would cut off the whole network, so SPIder deletes its own leaked rule and stops.
    """
    groups = _v6_send(client, base, password, "GET", "/api/groups")
    if groups is None:
        raise NotSupported
    gid = next((g["id"] for g in groups.get("groups", []) if g.get("name") == PAUSE_GROUP), None)
    if gid is None:
        made = _v6_send(client, base, password, "POST", "/api/groups",
                        {"name": PAUSE_GROUP, "comment": "Devices paused or blocked from SPIder", "enabled": True})
        gid = made["groups"][0]["id"]
    if gid == 0:
        raise RuntimeError("Pi-hole returned the Default group for SPIder's pause group; refusing to continue.")

    rule = _catch_all(client, base, password)
    if rule is None:
        _v6_send(client, base, password, "POST", "/api/domains/deny/regex",
                 {"domain": CATCH_ALL, "comment": "SPIder: answers nothing for paused devices", "groups": [gid], "enabled": True})
        rule = _catch_all(client, base, password)
    if rule is None:
        raise RuntimeError("Pi-hole didn't save SPIder's pause rule.")
    if set(rule.get("groups") or []) != {gid}:
        if (rule.get("comment") or "").startswith("SPIder"):
            _v6_send(client, base, password, "DELETE", f"/api/domains/deny/regex/{quote(CATCH_ALL, safe='')}")
            raise RuntimeError("Pi-hole put SPIder's pause rule on the wrong group, so SPIder removed it at once to protect your network.")
        raise RuntimeError("Your Pi-hole already has its own catch-all block rule, so SPIder won't touch it.")
    return gid


def _client(client, base, password, ident: str) -> dict | None:
    data = _v6_send(client, base, password, "GET", f"/api/clients/{quote(ident, safe='')}")
    found = (data or {}).get("clients") or []
    return found[0] if found else None


def set_paused(ident: str, paused: bool) -> dict:
    """Put a device (MAC, upper case) into or out of SPIder's pause group in Pi-hole."""

    def fetch(client, base, password):
        try:
            gid = _pause_group(client, base, password)
        except NotSupported:
            return {"status": "not_supported"}
        existing = _client(client, base, password, ident)
        if paused:
            if existing:
                groups = sorted(set(existing.get("groups") or [0]) | {gid})
                _v6_send(client, base, password, "PUT", f"/api/clients/{quote(ident, safe='')}",
                         {"comment": existing.get("comment") or "", "groups": groups})
            else:
                _v6_send(client, base, password, "POST", "/api/clients",
                         {"client": ident, "comment": SPIDER_TAG, "groups": [0, gid]})
        elif existing:
            groups = [g for g in (existing.get("groups") or []) if g != gid] or [0]
            if (existing.get("comment") or "").startswith(SPIDER_TAG) and groups == [0]:
                _v6_send(client, base, password, "DELETE", f"/api/clients/{quote(ident, safe='')}")
            else:
                _v6_send(client, base, password, "PUT", f"/api/clients/{quote(ident, safe='')}",
                         {"comment": existing.get("comment") or "", "groups": groups})
        return {"status": "ok"}

    try:
        return _call(fetch, timeout=15)
    except RuntimeError as exc:
        return {"status": "unsafe", "error": str(exc)}
