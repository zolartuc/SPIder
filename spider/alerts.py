import logging

import httpx

from . import settings_store

log = logging.getLogger("spider.alerts")


def send(title: str, message: str, tags: str = "spider") -> bool:
    """Send a push notification via ntfy if configured; always log it. Returns True if sent."""
    log.info("ALERT: %s - %s", title, message)
    s = settings_store.load()
    if not s.ntfy_topic:
        return False
    try:
        r = httpx.post(
            f"{s.ntfy_url.rstrip('/')}/{s.ntfy_topic}",
            content=message.encode(),
            headers={"Title": title, "Tags": tags},
            timeout=5,
        )
        r.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        log.warning("ntfy alert failed: %s", exc)
        return False
