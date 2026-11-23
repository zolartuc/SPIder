"""A light knock on a few telling ports (once a day per device), plus a web page title if there is one.

It's an ordinary connection attempt, the same as opening a page, never an exploit.
Only devices on your own network are checked.
"""

import html
import re
import socket
from concurrent.futures import ThreadPoolExecutor

import httpx

PORTS = {
    22: "ssh", 23: "telnet", 53: "dns", 80: "http", 139: "netbios", 443: "https", 445: "smb",
    548: "afp", 554: "rtsp", 631: "ipp", 1883: "mqtt", 3389: "rdp", 5000: "http-alt", 5001: "synology",
    6668: "tuya", 7000: "airplay", 8008: "cast-http", 8009: "cast", 8080: "http-alt2", 8123: "home-assistant",
    8883: "mqtt-tls", 9100: "printer", 32400: "plex", 62078: "iphone-sync",
}


def _open(ip: str, port: int, timeout: float = 0.45) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _title(ip: str, port: int) -> dict:
    scheme = "https" if port in (443, 5001) else "http"
    try:
        with httpx.Client(timeout=2.5, verify=False, follow_redirects=True, max_redirects=2) as c:
            with c.stream("GET", f"{scheme}://{ip}:{port}/") as r:
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > 65536:
                        break
                server = r.headers.get("server", "")
    except Exception:
        return {}
    m = re.search(rb"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    title = html.unescape(m.group(1).decode(errors="replace")).strip()[:80] if m else ""
    title = re.sub(r"\s+", " ", title)
    return {k: v for k, v in {"title": title, "server": server[:80]}.items() if v}


def probe(ip: str) -> dict:
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = dict(zip(PORTS, pool.map(lambda p: _open(ip, p), PORTS)))
    open_ports = [p for p, ok in results.items() if ok]
    out = {"open": open_ports}
    for port in (80, 8080, 443, 5000, 8123):
        if port in open_ports:
            info = _title(ip, port)
            if info:
                out["web"] = {"port": port, **info}
                break
    return out
