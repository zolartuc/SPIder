"""Minimal Pi-hole client supporting both v6 (REST API) and v5 (api.php).

Always returns a normalised dict so the dashboard doesn't care which version is running:
    {"status": "ok" | "offline" | "not_configured" | "auth_failed", "version": 5|6, ...stats}
"""

import logging

import httpx

from .. import settings_store

log = logging.getLogger("spider.pihole")

_sid: str | None = None  # cached v6 session id


def reset() -> None:
    """Forget the cached session (call when the URL or password changes)."""
    global _sid
    _sid = None


def _v6_summary(client: httpx.Client, base: str, password: str) -> dict | None:
    global _sid

    def get():
        headers = {"X-FTL-SID": _sid} if _sid else {}
        return client.get(f"{base}/api/stats/summary", headers=headers)

    r = get()
    if r.status_code == 404:
        return None  # not v6
    if r.status_code == 401:
        auth = client.post(f"{base}/api/auth", json={"password": password})
        session = auth.json().get("session", {}) if auth.status_code == 200 else {}
        if not session.get("valid"):
            return {"status": "auth_failed", "version": 6}
        _sid = session.get("sid")
        r = get()
    r.raise_for_status()

    d = r.json()
    q, g = d.get("queries", {}), d.get("gravity", {})
    return {
        "status": "ok",
        "version": 6,
        "queries_today": q.get("total"),
        "blocked_today": q.get("blocked"),
        "percent_blocked": round(q.get("percent_blocked") or 0, 1),
        "domains_on_blocklist": g.get("domains_being_blocked"),
        "active_clients": d.get("clients", {}).get("active"),
    }


def _v5_summary(client: httpx.Client, base: str, token: str) -> dict:
    r = client.get(f"{base}/admin/api.php", params={"summaryRaw": "", "auth": token})
    r.raise_for_status()
    d = r.json()
    if not d:  # v5 returns [] when the token is wrong
        return {"status": "auth_failed", "version": 5}
    return {
        "status": "ok",
        "version": 5,
        "queries_today": d.get("dns_queries_today"),
        "blocked_today": d.get("ads_blocked_today"),
        "percent_blocked": round(float(d.get("ads_percentage_today") or 0), 1),
        "domains_on_blocklist": d.get("domains_being_blocked"),
        "active_clients": d.get("unique_clients"),
        "blocking": d.get("status"),
    }


def summary() -> dict:
    s = settings_store.load()
    if not s.pihole_url:
        return {"status": "not_configured"}
    base = s.pihole_url.rstrip("/")
    try:
        with httpx.Client(timeout=4, verify=False) as client:
            return _v6_summary(client, base, s.pihole_password) or _v5_summary(
                client, base, s.pihole_password
            )
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("Pi-hole unreachable: %s", exc)
        return {"status": "offline", "error": str(exc)}
