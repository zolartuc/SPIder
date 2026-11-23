"""MAC address -> manufacturer lookup, using the offline database bundled with scapy."""

from functools import lru_cache

_manufdb = None


def _db():
    global _manufdb
    if _manufdb is None:
        try:
            from scapy.all import conf

            _manufdb = conf.manufdb
        except Exception:
            _manufdb = False
    return _manufdb


def is_randomised(mac: str) -> bool:
    """Phones/laptops often use a random 'private' MAC per network (locally administered bit)."""
    return bool(int(mac[:2], 16) & 0b10)


@lru_cache(maxsize=4096)
def lookup(mac: str) -> str | None:
    if is_randomised(mac):
        return "Private MAC (phone/laptop)"
    from ..identify import oui  # up-to-date list first, built-in list as a fallback
    if fresh := oui.lookup(mac):
        return fresh
    db = _db()
    if not db:
        return None
    try:
        short, long = db.lookup(mac)
        name = long or short
        return None if not name or name.lower() == mac.lower() else name
    except Exception:
        return None
