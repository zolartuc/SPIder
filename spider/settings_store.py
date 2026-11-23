"""Settings people can change from the dashboard.

Values in .env are the defaults; anything saved from the Settings page is stored
in the database and wins. Everything is validated before it's saved.
"""

import ipaddress
import json
import re
import threading
from typing import Literal
from zoneinfo import ZoneInfoNotFoundError, ZoneInfo, available_timezones

from pydantic import BaseModel, Field, field_validator, model_validator

from .config import get_settings
from .db import connect

HOSTNAME_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")

SECRET_KEYS = {"pihole_password"}
SYSTEM_KEYS = {"hostname", "timezone", "lan_interface", "lan_mode", "lan_address", "lan_gateway", "lan_dns"}


def normalise_url(v: str) -> str:
    """'Https://10.0.0.2:7300/admin/' -> 'https://10.0.0.2:7300' (scheme lower case, no trailing /admin or slash)."""
    v = (v or "").strip()
    v = re.sub(r"^(https?)://", lambda m: m.group(1).lower() + "://", v, flags=re.IGNORECASE)
    v = re.sub(r"/admin/?$", "", v.rstrip("/"), flags=re.IGNORECASE)
    return v.rstrip("/")


class AppSettings(BaseModel):
    # Device
    device_name: str = Field("SPIder", min_length=1, max_length=40)
    hostname: str = ""
    timezone: str = ""

    # Network (LAN)
    lan_interface: str = "eth0"
    lan_mode: Literal["dhcp", "static"] = "dhcp"
    lan_address: str = ""   # e.g. 192.168.1.50/24
    lan_gateway: str = ""
    lan_dns: str = ""       # comma separated

    # Scanning
    scan_subnet: str = ""
    scan_interval: int = Field(300, ge=60, le=86400)
    scan_method: Literal["auto", "arp", "ping"] = "auto"
    offline_after_minutes: int = Field(15, ge=1, le=1440)

    # Pi-hole
    pihole_url: str = ""
    pihole_password: str = ""

    # Alerts
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    alert_new_device: bool = True
    alert_new_contact: bool = False

    # Access
    guest_view: bool = True

    @field_validator("*", mode="before")
    @classmethod
    def strip_strings(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("hostname")
    @classmethod
    def valid_hostname(cls, v):
        if v and not HOSTNAME_RE.match(v):
            raise ValueError("Use letters, numbers and dashes only (no spaces), up to 63 characters.")
        return v.lower()

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, v):
        if v:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Pick a timezone from the list, like Europe/London.")
        return v

    @field_validator("lan_interface")
    @classmethod
    def valid_iface(cls, v):
        if not IFACE_RE.match(v):
            raise ValueError("Interface names look like eth0 or wlan0.")
        return v

    @field_validator("lan_address")
    @classmethod
    def valid_address(cls, v):
        if v:
            try:
                iface = ipaddress.IPv4Interface(v if "/" in v else f"{v}/24")
            except ValueError:
                raise ValueError("Enter an address like 192.168.1.50/24.")
            return str(iface)
        return v

    @field_validator("lan_gateway")
    @classmethod
    def valid_gateway(cls, v):
        if v:
            try:
                ipaddress.IPv4Address(v)
            except ValueError:
                raise ValueError("Enter the router's address, like 192.168.1.1.")
        return v

    @field_validator("lan_dns")
    @classmethod
    def valid_dns(cls, v):
        parts = [p.strip() for p in re.split(r"[,\s]+", v) if p.strip()]
        for p in parts:
            try:
                ipaddress.ip_address(p)
            except ValueError:
                raise ValueError(f"{p} isn't an IP address. Separate DNS servers with commas.")
        return ", ".join(parts)

    @field_validator("scan_subnet")
    @classmethod
    def valid_subnet(cls, v):
        if v:
            try:
                net = ipaddress.IPv4Network(v, strict=False)
            except ValueError:
                raise ValueError("Enter a subnet like 192.168.1.0/24, or leave it blank to detect it.")
            if net.prefixlen < 20:
                raise ValueError("That's too big to scan. Use /20 or smaller, like /24.")
            return str(net)
        return v

    @field_validator("pihole_url", "ntfy_url")
    @classmethod
    def valid_url(cls, v):
        v = normalise_url(v)
        if v and not re.match(r"^https?://[^\s/$.?#].[^\s]*$", v):
            raise ValueError("Enter a full address starting with http:// or https://.")
        return v

    @field_validator("ntfy_topic")
    @classmethod
    def valid_topic(cls, v):
        if v and not re.match(r"^[A-Za-z0-9_-]{1,64}$", v):
            raise ValueError("Topics use letters, numbers, dashes and underscores only.")
        return v

    @model_validator(mode="after")
    def static_needs_address(self):
        if self.lan_mode == "static" and not (self.lan_address and self.lan_gateway):
            raise ValueError("A fixed address needs both the address and the router (gateway).")
        return self


def _env_defaults() -> dict:
    env = get_settings()
    return {
        "scan_subnet": env.subnet,
        "scan_interval": env.scan_interval,
        "scan_method": env.scan_method,
        "offline_after_minutes": env.offline_after_minutes,
        "ntfy_url": normalise_url(env.ntfy_url),
        "ntfy_topic": env.ntfy_topic,
        "pihole_url": normalise_url(env.pihole_url),
        "pihole_password": env.pihole_password,
    }


_lock = threading.Lock()
_cache: AppSettings | None = None


def load() -> AppSettings:
    global _cache
    with _lock:
        if _cache is None:
            with connect() as conn:
                stored = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT * FROM settings")}
            merged = AppSettings().model_dump() | {k: v for k, v in _env_defaults().items() if v not in ("", None)}
            merged |= {k: v for k, v in stored.items() if k in AppSettings.model_fields}
            _cache = AppSettings.model_construct(**merged)
        return _cache


def get(key: str):
    return getattr(load(), key)


def update(changes: dict) -> tuple[AppSettings, set[str]]:
    """Validate and save. Raises pydantic.ValidationError on bad input."""
    global _cache
    current = load().model_dump()
    # Blank secret = "keep what's there".
    changes = {k: v for k, v in changes.items() if k in AppSettings.model_fields and not (k in SECRET_KEYS and v in ("", None))}
    new = AppSettings(**(current | changes))
    changed = {k for k in changes if getattr(new, k) != current.get(k)}
    with _lock, connect() as conn:
        for k in changed:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, json.dumps(getattr(new, k))),
            )
        _cache = new
    return new, changed


def public() -> dict:
    """Settings safe to send to the browser (secrets replaced by 'is it set?')."""
    data = load().model_dump()
    for k in SECRET_KEYS:
        data[f"{k}_set"] = bool(data.pop(k))
    return data


_TZ_CACHE: list[str] | None = None


def timezones() -> list[str]:
    global _TZ_CACHE
    if _TZ_CACHE is None:
        _TZ_CACHE = sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))
    return _TZ_CACHE
