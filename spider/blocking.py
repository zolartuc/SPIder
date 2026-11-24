"""Pause or block a device's internet, through Pi-hole.

A paused device's lookups go unanswered, so apps and websites stop working.
It's DNS-level: good for "the Switch is off until morning", not a firewall.
A device using its own DNS (or hard-coded addresses) can get around it.
"""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import HTTPException

from .db import add_event, connect, now
from .pihole import client as pihole
from . import settings_store
from .watchtower.scanner import local_ip

log = logging.getLogger("spider.blocking")


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes % 60 == 0:
        h = minutes // 60
        return f"{h} hour{'s' if h != 1 else ''}"
    return f"{minutes} minutes"


def availability() -> dict:
    """Can SPIder pause devices right now? Needs a Pi-hole v6."""
    summary = pihole.summary()
    if summary.get("status") == "not_configured":
        return {"available": False, "reason": "Connect a Pi-hole in Settings to pause devices."}
    if summary.get("status") != "ok":
        return {"available": False, "reason": "Your Pi-hole isn't reachable, so devices can't be paused right now."}
    if summary.get("version") != 6:
        return {"available": False, "reason": "Pausing devices needs Pi-hole v6. Yours is v5."}
    return {"available": True, "reason": None}


def current() -> dict[str, dict]:
    with connect() as conn:
        return {r["mac"]: dict(r) for r in conn.execute("SELECT * FROM blocks")}


def protected_reason(ip: str | None) -> str | None:
    """Why a device can't be paused (the router, the Pi-hole, SPIder itself), or None."""
    if not ip:
        return None
    if ip == local_ip():
        return "SPIder won't pause the machine it runs on."
    host = urlparse(settings_store.get("pihole_url") or "").hostname
    if host and ip == host:
        return "SPIder won't pause your Pi-hole: that would cut off DNS for everyone."
    if ip.endswith(".1"):
        return "SPIder won't pause your router."
    return None


def block(mac: str, seconds: int | None, user: str) -> dict:
    with connect() as conn:
        dev = conn.execute("SELECT * FROM devices WHERE mac = ?", (mac,)).fetchone()
    if not dev:
        raise HTTPException(404, "SPIder doesn't know that device.")
    if reason := protected_reason(dev["ip"]):
        raise HTTPException(400, reason)

    res = pihole.set_paused(mac.upper(), True)
    _raise_for(res)

    until = None if seconds is None else (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")
    detail = f"paused for {_fmt_duration(seconds)}" if seconds else "blocked until someone unblocks it"
    with connect() as conn:
        conn.execute(
            "INSERT INTO blocks (mac, until, created, by_user) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(mac) DO UPDATE SET until = excluded.until, created = excluded.created, by_user = excluded.by_user",
            (mac, until, now(), user),
        )
        add_event(conn, "blocked", mac, f"{detail} by {user}")
    return {"mac": mac, "until": until}


def unblock(mac: str, user: str | None = None, expired: bool = False) -> None:
    res = pihole.set_paused(mac.upper(), False)
    _raise_for(res)
    with connect() as conn:
        if conn.execute("DELETE FROM blocks WHERE mac = ?", (mac,)).rowcount:
            add_event(conn, "unblocked", mac, "pause ended" if expired else f"unblocked by {user}")


def expire() -> None:
    """Called every few seconds: lift pauses whose time is up."""
    stamp = now()
    with connect() as conn:
        due = [r["mac"] for r in conn.execute("SELECT mac FROM blocks WHERE until IS NOT NULL AND until <= ?", (stamp,))]
    for mac in due:
        try:
            unblock(mac, expired=True)
            log.info("Pause ended for %s", mac)
        except HTTPException as exc:
            log.warning("Couldn't end pause for %s yet: %s", mac, exc.detail)  # retried next time


def _raise_for(res: dict) -> None:
    status = res.get("status")
    if status == "ok":
        return
    detail = res.get("error") or ""
    messages = {
        "not_configured": "Connect a Pi-hole in Settings to pause devices.",
        "offline": f"SPIder can't reach your Pi-hole. {detail} Nothing was changed.",
        "auth_failed": f"Your Pi-hole turned down SPIder's password. {detail} Nothing was changed.",
        "refused": f"{detail} Nothing was changed.",
        "not_supported": "Pausing devices needs Pi-hole v6.",
    }
    msg = messages.get(status) or detail or "Pi-hole said no."
    raise HTTPException(502 if status in ("offline", "unsafe") else 409, " ".join(msg.split()))
