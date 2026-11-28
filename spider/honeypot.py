"""The honeypot: fake doors on the LAN that nothing legitimate should ever knock on.

SPIder listens on a few ports that attackers and worms probe first (Telnet, SSH, RDP,
SMB, a router-style web login). None of your devices has a reason to connect to them,
so any connection is worth knowing about.

What happens on a connection:
  * a short, believable exchange (a login prompt, a version banner, a web login page),
  * SPIder writes down who it was, which door, and what they tried (usernames,
    passwords, client software, the first bytes),
  * the connection is closed. Nothing the visitor sends is ever run or stored as a file.

Everything is capped: a few KB read per connection, a 10 second limit, and at most
50 visitors at once, so the honeypot can't be used to tire the Pi out.
Repeat knocks from the same address on the same door within 10 minutes are counted
on one entry instead of filling the database.
"""

import asyncio
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

from . import alerts, settings_store
from .config import get_settings
from .db import add_event, connect, now

log = logging.getLogger("spider.honeypot")

READ_LIMIT = 4096
TIME_LIMIT = 10
MAX_VISITORS = 50
MERGE_WITHIN = timedelta(minutes=10)
ALERT_EVERY = timedelta(minutes=30)   # per source address

SERVICES = {
    21: "ftp", 22: "ssh", 23: "telnet", 2222: "ssh", 2323: "telnet",
    80: "web", 81: "web", 8000: "web", 8081: "web", 8888: "web",
    445: "smb", 139: "smb", 3389: "rdp", 5900: "vnc", 1433: "sql", 3306: "sql",
}
LABELS = {"ftp": "FTP", "ssh": "SSH", "telnet": "Telnet", "web": "Web login", "smb": "Windows file sharing",
          "rdp": "Remote Desktop", "vnc": "VNC screen sharing", "sql": "Database", "raw": "Other"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS honeypot_hits (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,      -- first knock
    last_ts   TEXT NOT NULL,      -- latest knock merged into this entry
    ip        TEXT NOT NULL,
    mac       TEXT,
    port      INTEGER NOT NULL,
    service   TEXT NOT NULL,
    count     INTEGER NOT NULL DEFAULT 1,
    detail    TEXT NOT NULL,      -- JSON: what they tried
    seen      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_honeypot_ts ON honeypot_hits (last_ts DESC);
"""

# port -> {"service", "status": listening | off | error, "error"}
listeners: dict[int, dict] = {}
_servers: list[asyncio.base_events.Server] = []
_loop: asyncio.AbstractEventLoop | None = None
_gate: asyncio.Semaphore | None = None
_last_alert: dict[str, datetime] = {}
_db_lock = threading.Lock()


def init_schema(conn) -> None:
    conn.executescript(SCHEMA)


def service_for(port: int) -> str:
    return SERVICES.get(port, "raw")


def parse_ports(text: str) -> list[int]:
    ports = []
    for p in re.split(r"[,\s]+", text or ""):
        if p.strip():
            n = int(p)
            if not 1 <= n <= 65535:
                raise ValueError
            if n not in ports:
                ports.append(n)
    return ports


# --------------------------------------------------------------------------- talking to visitors

def _printable(data: bytes, limit: int = 200) -> str:
    text = data[:limit].decode("latin-1")
    return "".join(c if 32 <= ord(c) < 127 else "·" for c in text)


def _strip_telnet(data: bytes) -> bytes:
    """Drop telnet option negotiation (IAC sequences) so only typed text is left."""
    out, i = bytearray(), 0
    while i < len(data):
        b = data[i]
        if b == 255 and i + 1 < len(data):
            i += 3 if data[i + 1] in (251, 252, 253, 254) else 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


async def _readline(reader: asyncio.StreamReader, budget: list[int]) -> bytes:
    """One line, or whatever arrives before the visitor goes quiet, within the read budget."""
    buf = b""
    while budget[0] > 0 and not re.search(rb"[\r\n]", buf):
        chunk = await reader.read(min(256, budget[0]))
        if not chunk:
            break
        budget[0] -= len(chunk)
        buf += chunk
    return buf


async def _telnet(reader, writer, budget) -> dict:
    writer.write(b"\r\nUbuntu 20.04.6 LTS\r\nlogin: ")
    await writer.drain()
    user = _strip_telnet(await _readline(reader, budget)).strip()
    writer.write(b"Password: ")
    await writer.drain()
    pw = _strip_telnet(await _readline(reader, budget)).strip()
    await asyncio.sleep(1.5)  # a real login pauses before saying no
    writer.write(b"\r\nLogin incorrect\r\n")
    await writer.drain()
    return {k: v for k, v in (("username", _printable(user, 64)), ("password", _printable(pw, 64))) if v}


async def _ssh(reader, writer, budget) -> dict:
    writer.write(b"SSH-2.0-OpenSSH_8.4p1 Debian-5+deb11u3\r\n")
    await writer.drain()
    hello = await _readline(reader, budget)
    client = _printable(hello.split(b"\r")[0].split(b"\n")[0], 120)
    return {"client": client} if client else {}


LOGIN_PAGE = b"""<!doctype html><html><head><title>Router Login</title>
<style>body{font-family:sans-serif;background:#eee;display:grid;place-items:center;height:100vh;margin:0}
form{background:#fff;padding:24px 28px;border-radius:6px;box-shadow:0 1px 4px #0003;display:grid;gap:10px;width:260px}
h1{font-size:18px;margin:0 0 6px}input,button{padding:8px;font-size:14px}</style></head>
<body><form method="post" action="/login"><h1>Router administration</h1>
<input name="username" placeholder="Username"><input name="password" type="password" placeholder="Password">
<button>Log in</button></form></body></html>"""


async def _web(reader, writer, budget) -> dict:
    head = b""
    while budget[0] > 0 and b"\r\n\r\n" not in head:
        chunk = await reader.read(min(1024, budget[0]))
        if not chunk:
            break
        budget[0] -= len(chunk)
        head += chunk
    top, _, body = head.partition(b"\r\n\r\n")
    lines = top.decode("latin-1").split("\r\n")
    request = lines[0][:160] if lines else ""
    headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:]) if v}
    want = min(int(headers.get("content-length", "0") or 0), budget[0], 2048) if headers.get("content-length", "").isdigit() else 0
    while len(body) < want:
        chunk = await reader.read(want - len(body))
        if not chunk:
            break
        body += chunk
    info = {"request": _printable(request.encode("latin-1"), 160)}
    if ua := headers.get("user-agent"):
        info["client"] = _printable(ua.encode("latin-1"), 160)
    if request.startswith("POST") and body:
        form = parse_qs(body.decode("latin-1", "replace"))
        for field, key in (("username", "username"), ("user", "username"), ("login", "username"),
                           ("password", "password"), ("pass", "password"), ("pwd", "password")):
            if form.get(field) and key not in info:
                info[key] = _printable(form[field][0].encode("latin-1", "replace"), 64)
        page, status = b"<!doctype html><p>Invalid username or password.</p><a href='/'>Try again</a>", b"401 Unauthorized"
    else:
        page, status = LOGIN_PAGE, b"200 OK"
    writer.write(b"HTTP/1.1 " + status + b"\r\nServer: lighttpd/1.4.59\r\nContent-Type: text/html\r\n"
                 b"Content-Length: " + str(len(page)).encode() + b"\r\nConnection: close\r\n\r\n" + page)
    await writer.drain()
    return info


async def _ftp(reader, writer, budget) -> dict:
    writer.write(b"220 (vsFTPd 3.0.3)\r\n")
    await writer.drain()
    info = {}
    for _ in range(3):
        line = (await _readline(reader, budget)).strip()
        if not line:
            break
        cmd, _, arg = line.partition(b" ")
        if cmd.upper() == b"USER":
            info["username"] = _printable(arg, 64)
            writer.write(b"331 Please specify the password.\r\n")
        elif cmd.upper() == b"PASS":
            info["password"] = _printable(arg, 64)
            await asyncio.sleep(1.5)
            writer.write(b"530 Login incorrect.\r\n")
            break
        else:
            writer.write(b"530 Please login with USER and PASS.\r\n")
        await writer.drain()
    await writer.drain()
    return info


async def _listen_only(reader, writer, budget) -> dict:
    """SMB, RDP, VNC, databases and anything else: just see what the visitor opens with."""
    first = await reader.read(min(512, budget[0]))
    info = {}
    if first:
        info["first_bytes"] = _printable(first, 120)
        if m := re.search(rb"mstshash=([^\r\n]{1,64})", first):  # Remote Desktop clients send the username
            info["username"] = _printable(m.group(1), 64)
    return info


HANDLERS = {"telnet": _telnet, "ssh": _ssh, "web": _web, "ftp": _ftp}


async def _visit(port: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername") or ("?", 0)
    ip = peer[0].removeprefix("::ffff:")
    service = service_for(port)
    info: dict = {}
    if _gate.locked():  # too many at once: note it, don't talk
        writer.close()
        await asyncio.to_thread(record, ip, port, service, {"note": "dropped, too many connections at once"})
        return
    async with _gate:
        try:
            info = await asyncio.wait_for(HANDLERS.get(service, _listen_only)(reader, writer, [READ_LIMIT]), TIME_LIMIT)
        except (asyncio.TimeoutError, ConnectionError, OSError, ValueError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass
    await asyncio.to_thread(record, ip, port, service, info)


# --------------------------------------------------------------------------- recording

def _mac_for(ip: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT mac FROM devices WHERE ip = ? ORDER BY last_seen DESC", (ip,)).fetchone()
    if row:
        return row["mac"]
    try:  # host networking: the kernel's neighbour table knows anyone who just talked to us
        for line in Path("/proc/net/arp").read_text().splitlines()[1:]:
            parts = line.split()
            if parts[0] == ip and parts[3] != "00:00:00:00:00:00":
                return parts[3].lower()
    except OSError:
        pass
    return None


def _ignored(ip: str) -> bool:
    from .blocking import local_ip
    skip = {p.strip() for p in re.split(r"[,\s]+", settings_store.get("honeypot_ignore") or "") if p.strip()}
    return ip in skip or ip == local_ip()


def _record(ip: str, port: int, service: str, info: dict, when: datetime | None = None, test: bool = False) -> dict | None:
    """Write a knock down (or add it to the last one from the same address and door) and alert if it's new."""
    if not test and _ignored(ip):
        return None
    when = when or datetime.now(timezone.utc)
    ts = when.isoformat(timespec="seconds")
    if test:
        info = {**info, "test": True}
    mac = None if test else _mac_for(ip)
    with _db_lock, connect() as conn:
        recent = conn.execute(
            "SELECT * FROM honeypot_hits WHERE ip = ? AND port = ? AND last_ts >= ? ORDER BY id DESC LIMIT 1",
            (ip, port, (when - MERGE_WITHIN).isoformat(timespec="seconds"))).fetchone()
        if recent:
            old = json.loads(recent["detail"])
            tried = old.get("tries", [])
            if (info.get("username") or info.get("password")) and len(tried) < 20:
                tried.append({k: info[k] for k in ("username", "password") if k in info})
            merged = old | {k: v for k, v in info.items() if k not in ("username", "password")} | ({"tries": tried} if tried else {})
            conn.execute("UPDATE honeypot_hits SET last_ts = ?, count = count + 1, detail = ?, seen = 0, mac = COALESCE(mac, ?) WHERE id = ?",
                         (ts, json.dumps(merged), mac, recent["id"]))
            return None
        detail = dict(info)
        if info.get("username") or info.get("password"):
            detail["tries"] = [{k: info[k] for k in ("username", "password") if k in info}]
        conn.execute("INSERT INTO honeypot_hits (ts, last_ts, ip, mac, port, service, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (ts, ts, ip, mac, port, service, json.dumps(detail)))
        dev = conn.execute("SELECT name, hostname, vendor, known FROM devices WHERE mac = ?", (mac,)).fetchone() if mac else None
        if not test:
            add_event(conn, "honeypot", mac, f"{ip} {port} {service}")
    log.warning("Honeypot: %s knocked on %s (%d) %s", ip, LABELS.get(service, service), port, info)
    if not test:
        _maybe_alert(ip, port, service, info, dev, when)
    return {"ip": ip, "port": port, "service": service}


def _maybe_alert(ip, port, service, info, dev, when) -> None:
    if not settings_store.get("alert_honeypot"):
        return
    last = _last_alert.get(ip)
    if last and when - last < ALERT_EVERY:
        return
    _last_alert[ip] = when
    who = (dev["name"] or dev["hostname"]) if dev else None
    if dev and dev["known"]:
        title = f"One of your devices touched the honeypot: {who or ip}"
        msg = f"{who or 'A known device'} ({ip}) connected to the fake {LABELS.get(service, service)} on port {port}. It may be infected, or running a scan."
    else:
        title = "Someone is poking around your network"
        msg = f"{who or 'An unknown device'} at {ip} connected to the fake {LABELS.get(service, service)} on port {port}."
    if info.get("username"):
        msg += f"\nTried to log in as {info['username']!r}."
    alerts.send(title, msg, tags="rotating_light,spider_web")


_test_ports: set[int] = set()


def record(ip: str, port: int, service: str, info: dict, when: datetime | None = None, test: bool = False) -> dict | None:
    """A knock from the Pi itself is only kept when it's the Settings self-test."""
    if ip in ("127.0.0.1", "::1"):
        if not (test or port in _test_ports):
            return None
        test = True
    return _record(ip, port, service, info, when, test)


# --------------------------------------------------------------------------- starting and stopping

async def start() -> None:
    """Open the fake doors chosen in Settings (closing any that were open)."""
    global _loop, _gate
    _loop = asyncio.get_running_loop()
    _gate = _gate or asyncio.Semaphore(MAX_VISITORS)
    await stop()
    try:
        ports = parse_ports(settings_store.get("honeypot_ports"))
    except ValueError:
        ports = []
    if not settings_store.get("honeypot_enabled"):
        for p in ports:
            listeners[p] = {"service": service_for(p), "status": "off", "error": None}
        return
    if get_settings().demo:  # demo: pretend, never bind real ports
        for p in ports:
            listeners[p] = {"service": service_for(p), "status": "listening", "error": None}
        return
    for p in ports:
        try:
            srv = await asyncio.start_server(lambda r, w, p=p: _visit(p, r, w), host="0.0.0.0", port=p,
                                             reuse_address=True, limit=READ_LIMIT)
            _servers.append(srv)
            listeners[p] = {"service": service_for(p), "status": "listening", "error": None}
        except OSError as exc:
            why = "something else on the Pi is already using it" if exc.errno in (98, 48, 10048) else \
                  ("SPIder isn't allowed to open ports below 1024 (run it as root, or in Docker)" if p < 1024 else "the system won't let SPIder use it") if exc.errno == 13 else str(exc)
            listeners[p] = {"service": service_for(p), "status": "error", "error": why}
            log.warning("Honeypot couldn't open port %d: %s", p, why)
    log.info("Honeypot listening on %s", [p for p, l in listeners.items() if l["status"] == "listening"] or "nothing")


async def stop() -> None:
    while _servers:
        srv = _servers.pop()
        srv.close()
        await srv.wait_closed()
    listeners.clear()


def restart_soon() -> None:
    """Called from a worker thread after Settings change."""
    if _loop and _loop.is_running():
        asyncio.run_coroutine_threadsafe(start(), _loop)


# --------------------------------------------------------------------------- reading it back

def _since(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def unseen() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM honeypot_hits WHERE seen = 0 AND last_ts >= ?", (_since(24 * 7),)).fetchone()[0]


def overview(show_passwords: bool) -> dict:
    """Visits grouped by who made them, newest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT h.*, d.name, d.hostname, d.vendor, d.known, d.kind, d.kind_user FROM honeypot_hits h "
            "LEFT JOIN devices d ON d.mac = h.mac WHERE h.last_ts >= ? ORDER BY h.last_ts DESC LIMIT 500",
            (_since(24 * 30),)).fetchall()
    sources: dict[str, dict] = {}
    for r in rows:
        key = r["mac"] or f"ip:{r['ip']}"
        detail = json.loads(r["detail"])
        if not show_passwords:
            for t in detail.get("tries", []):
                if t.get("password"):
                    t["password"] = "•" * min(len(t["password"]), 8)
            detail.pop("password", None)
        detail.pop("username", None)
        s = sources.setdefault(key, {
            "key": key, "ip": r["ip"], "mac": r["mac"], "name": r["name"] or r["hostname"], "vendor": r["vendor"],
            "kind": r["kind_user"] or r["kind"], "known": bool(r["known"]), "on_list": r["known"] is not None,
            "test": bool(detail.get("test")), "first": r["ts"], "last": r["last_ts"], "knocks": 0, "unseen": 0,
            "ports": [], "visits": []})
        s["first"] = min(s["first"], r["ts"])
        s["knocks"] += r["count"]
        s["unseen"] += 0 if r["seen"] else 1
        if r["port"] not in s["ports"]:
            s["ports"].append(r["port"])
        s["visits"].append({"id": r["id"], "ts": r["ts"], "last": r["last_ts"], "port": r["port"], "service": r["service"],
                            "label": LABELS.get(r["service"], r["service"]), "count": r["count"], "seen": bool(r["seen"]), **detail})
    from .blocking import current, protected_reason
    blocks = current()
    out = sorted(sources.values(), key=lambda s: s["last"], reverse=True)
    for s in out:
        s["ports"].sort()
        s["block"] = blocks.get(s["mac"]) if s["mac"] else None
        s["protected"] = protected_reason(s["ip"])
        s["scan"] = len(s["ports"]) >= 3   # several doors tried = someone scanning
    with connect() as conn:
        week = conn.execute("SELECT COALESCE(SUM(count), 0) FROM honeypot_hits WHERE last_ts >= ?", (_since(24 * 7),)).fetchone()[0]
    return {
        "enabled": bool(settings_store.get("honeypot_enabled")),
        "listeners": [{"port": p, "label": LABELS.get(l["service"], l["service"]), **l} for p, l in sorted(listeners.items())],
        "sources": out,
        "unseen": sum(s["unseen"] for s in out if not s["test"]),
        "knocks_week": week,
    }


def mark_seen(key: str | None = None) -> int:
    with _db_lock, connect() as conn:
        if key is None:
            return conn.execute("UPDATE honeypot_hits SET seen = 1 WHERE seen = 0").rowcount
        if key.startswith("ip:"):
            return conn.execute("UPDATE honeypot_hits SET seen = 1 WHERE seen = 0 AND mac IS NULL AND ip = ?", (key[3:],)).rowcount
        return conn.execute("UPDATE honeypot_hits SET seen = 1 WHERE seen = 0 AND mac = ?", (key,)).rowcount


def clear_tests() -> None:
    with _db_lock, connect() as conn:
        conn.execute("DELETE FROM honeypot_hits WHERE detail LIKE '%\"test\": true%'")


async def self_test() -> dict:
    """Knock on our own first open door, so the owner can see it working."""
    open_ports = [p for p, l in listeners.items() if l["status"] == "listening"]
    if not open_ports:
        raise RuntimeError("The honeypot isn't listening on any port yet. Turn it on in Settings.")
    port = open_ports[0]
    if get_settings().demo:
        await asyncio.to_thread(record, "127.0.0.1", port, service_for(port), {"username": "test", "password": "test"}, None, True)
        return {"port": port}
    _test_ports.add(port)
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 3)
        writer.write(b"test\r\ntest\r\n")
        await writer.drain()
        try:
            await asyncio.wait_for(reader.read(1024), 4)
        except asyncio.TimeoutError:
            pass
        writer.close()
    except OSError as exc:
        raise RuntimeError(f"Couldn't reach port {port}: {exc}")
    finally:
        await asyncio.sleep(2.5)
        _test_ports.discard(port)
    return {"port": port}


