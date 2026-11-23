"""A stand-in Pi-hole (v6 API) that returns made-up numbers, for demos and screenshots.

Usage:
    python scripts/demo/fake_pihole.py [port]     (default port 8099)
"""

import json
import math
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOTAL, BLOCKED, CACHED = 48213, 9874, 14120


def history() -> list[dict]:
    """24 hours of 10-minute buckets with a believable daily rhythm, scaled to today's totals."""
    rng = random.Random(7)
    now = int(time.time()) // 600 * 600
    raw = []
    for i in range(144):
        ts = now - (143 - i) * 600
        hour = time.localtime(ts).tm_hour + time.localtime(ts).tm_min / 60
        # Quiet overnight, a morning bump, busy evening.
        level = 0.15 + 0.5 * math.exp(-((hour - 8) ** 2) / 6) + 1.0 * math.exp(-((hour - 20) ** 2) / 8)
        raw.append((ts, level * rng.uniform(0.8, 1.2), rng.uniform(0.15, 0.27), rng.uniform(0.25, 0.35)))
    scale = TOTAL / sum(r[1] for r in raw)
    out = []
    for ts, level, block_share, cache_share in raw:
        total = round(level * scale)
        blocked, cached = round(total * block_share), round(total * cache_share)
        out.append({"timestamp": ts, "total": total, "blocked": blocked, "cached": cached,
                    "forwarded": total - blocked - cached})
    return out


SUMMARY = {
    "queries": {"total": TOTAL, "blocked": BLOCKED, "percent_blocked": round(BLOCKED / TOTAL * 100, 2),
                "cached": CACHED, "forwarded": TOTAL - BLOCKED - CACHED, "unique_domains": 3120},
    "clients": {"active": 14, "total": 17},
    "gravity": {"domains_being_blocked": 412883},
}

TOP_BLOCKED = [
    ("googleads.g.doubleclick.net", 1843), ("app-measurement.com", 1210),
    ("device-metrics-us.amazon.com", 966), ("log-config.samsungacr.com", 804),
    ("browser.events.data.msn.com", 611), ("settings-win.data.microsoft.com", 498),
    ("sdkconfig.ad.intl.xiaomi.com", 372), ("analytics.tiktok.com", 301),
]

TOP_CLIENTS = [
    ("192.168.1.21", "", 9120), ("192.168.1.33", "gaming-pc.lan", 7311),
    ("192.168.1.30", "", 6870), ("192.168.1.32", "work-laptop.lan", 5402),
    ("192.168.1.22", "sonos-kitchen.lan", 3980), ("192.168.1.41", "", 2650),
    ("192.168.1.20", "", 1204), ("192.168.1.40", "", 990),
]


# What each demo device talks to: (ip, [(domain, weight)], [(domain, weight, first_seen_hours_ago)]).
# The second list is sites a device starts using partway through the week: SPIder's "first contacts".
PROFILES = [
    ("192.168.1.21", [("nflxvideo.net", 40), ("netflix.com", 12), ("googlevideo.com", 30), ("youtube.com", 10),
                      ("bbci.co.uk", 14), ("bbc.co.uk", 6), ("samsungcloudsolution.com", 5), ("samsungotn.net", 3),
                      ("aiv-cdn.net", 8), ("primevideo.com", 3)],
                     [("disneyplus.com", 6, 50), ("bamgrid.com", 9, 50)]),
    ("192.168.1.33", [("steampowered.com", 10), ("steamcontent.com", 18), ("steamserver.net", 6), ("discord.com", 14),
                      ("discordapp.net", 6), ("epicgames.com", 5), ("twitch.tv", 8), ("ttvnw.net", 10),
                      ("nvidia.com", 3), ("windowsupdate.com", 4), ("microsoft.com", 5), ("google.com", 6)],
                     [("riotgames.com", 9, 30), ("leagueoflegends.com", 6, 30)]),
    ("192.168.1.30", [("whatsapp.net", 16), ("instagram.com", 10), ("cdninstagram.com", 14), ("spotify.com", 6),
                      ("scdn.co", 9), ("icloud.com", 8), ("apple.com", 7), ("mzstatic.com", 4), ("google.com", 8),
                      ("googleapis.com", 8), ("bbc.co.uk", 3), ("reddit.com", 5), ("amazon.co.uk", 3)],
                     [("temu.com", 3, 20)]),
    ("192.168.1.32", [("office.com", 10), ("microsoftonline.com", 8), ("sharepoint.com", 7), ("outlook.com", 9),
                      ("teams.microsoft.com", 10), ("zoom.us", 4), ("slack.com", 6), ("slack-edge.com", 4),
                      ("google.com", 5), ("windowsupdate.com", 3), ("github.com", 4), ("atlassian.net", 5)],
                     [("dropbox.com", 5, 72), ("dropboxapi.com", 4, 72)]),
    ("192.168.1.22", [("sonos.com", 8), ("sonos.net", 4), ("spotify.com", 10), ("scdn.co", 12), ("bbci.co.uk", 5)], []),
    ("192.168.1.41", [("amazonalexa.com", 12), ("amazon.com", 6), ("audible.co.uk", 3), ("amazonaws.com", 6)], []),
    ("192.168.1.20", [("bambulab.com", 12), ("amazonaws.com", 6), ("cloudfront.net", 4), ("ntp.org", 1)],
                     [("makerworld.com", 4, 26)]),
    ("192.168.1.23", [("hivehome.com", 10), ("alertme.com", 6), ("ntp.org", 1)], []),
    ("192.168.1.24", [("tuyaeu.com", 8), ("ntp.org", 1)], []),
    ("192.168.1.40", [("tuyaeu.com", 9), ("ntp.org", 1)], [("tuyacn.com", 7, 6), ("iot-dns.com", 2, 3)]),
    ("192.168.1.31", [("apple.com", 6), ("icloud.com", 8), ("instagram.com", 6), ("whatsapp.net", 8),
                      ("tiktokv.com", 10)], []),
    ("192.168.1.50", [("ntp.org", 2), ("aliyuncs.com", 5)], [("aliyun.com", 3, 2)]),
    ("192.168.1.2", [("github.com", 1), ("pi-hole.net", 1), ("ntp.org", 1)], []),
    ("192.168.1.10", [("github.com", 1), ("pypi.org", 1), ("ntp.org", 1)], []),
]

_FIRST_SEEN_DAYS = {"192.168.1.40": 1.6, "192.168.1.31": 2.2, "192.168.1.50": 0.9}

# How chatty each device is: TVs, PCs and phones dwarf plugs and hubs.
_RATE = {"192.168.1.21": 3.2, "192.168.1.33": 2.6, "192.168.1.30": 2.4, "192.168.1.32": 1.9, "192.168.1.31": 1.4,
         "192.168.1.22": 0.8, "192.168.1.41": 0.7, "192.168.1.20": 0.55, "192.168.1.40": 0.45, "192.168.1.23": 0.3,
         "192.168.1.24": 0.25, "192.168.1.50": 0.3, "192.168.1.2": 0.12, "192.168.1.10": 0.12}


def _sub(domain: str, rng: random.Random) -> str:
    """Make lookups look real: rr3---sn-xyz.googlevideo.com rather than googlevideo.com."""
    prefix = rng.choice(["", "", "api.", "cdn.", "www.", f"edge-{rng.randint(1, 40)}.", f"rr{rng.randint(1, 9)}---sn-{rng.randint(100, 999)}."])
    return prefix + domain


def query_log() -> list[dict]:
    """A week of answered lookups, newest first (like Pi-hole), with a daily rhythm per device."""
    rng = random.Random(11)
    now = time.time()
    out = []
    for ip, base, later in PROFILES:
        starts = now - _FIRST_SEEN_DAYS.get(ip, 7) * 86400
        sites = [(d, w, None) for d, w in base] + [(d, w, h) for d, w, h in later]
        weights = [w for _, w, _ in sites]
        t = starts
        while t < now:
            hour = time.localtime(t).tm_hour
            busy = 0.2 + 0.6 * math.exp(-((hour - 8) ** 2) / 6) + 1.1 * math.exp(-((hour - 20) ** 2) / 8)
            t += rng.expovariate(busy * _RATE.get(ip, 1) / 160)  # ~ one lookup every few minutes, faster in the evening
            domain, _, since_h = rng.choices(sites, weights)[0]
            if since_h is not None and t < now - since_h * 3600:
                continue
            if t < now:
                out.append({"time": t, "type": "A", "domain": _sub(domain, rng), "status": "FORWARDED",
                            "client": {"ip": ip, "name": None}})
    out.sort(key=lambda q: -q["time"])
    return out


_LOG: list[dict] = []

# Pi-hole v6 group management, in memory: enough for SPIder's pause feature.
GROUPS = [{"id": 0, "name": "Default", "comment": "The default group", "enabled": True}]
REGEX_DENY: list[dict] = []
CLIENTS: dict[str, dict] = {}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if self._manage("GET"):
            return
        if path == "/api/stats/summary":
            self._json(200, SUMMARY)
        elif path == "/api/history":
            self._json(200, {"history": history()})
        elif path == "/api/stats/top_domains":
            self._json(200, {"domains": [{"domain": d, "count": c} for d, c in TOP_BLOCKED],
                             "total_queries": TOTAL, "blocked_queries": BLOCKED})
        elif path == "/api/queries":
            from urllib.parse import parse_qs, urlparse
            qs = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            lo, hi = float(qs.get("from", 0)), float(qs.get("until", 9e12))
            start, length = int(qs.get("start", 0)), int(qs.get("length", 100))
            matched = [q for q in _LOG if lo <= q["time"] <= hi]
            self._json(200, {"queries": matched[start:start + length], "cursor": 1,
                             "recordsTotal": len(_LOG), "recordsFiltered": len(matched)})
        elif path == "/api/stats/top_clients":
            self._json(200, {"clients": [{"ip": ip, "name": n, "count": c} for ip, n, c in TOP_CLIENTS],
                             "total_queries": TOTAL})
        else:
            self._json(404, {"error": "not found"})

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _manage(self, method: str) -> bool:
        from urllib.parse import unquote
        path = self.path.split("?")[0]
        if path == "/api/groups":
            if method == "GET":
                self._json(200, {"groups": GROUPS}); return True
            if method == "POST":
                g = {**self._body(), "id": max(x["id"] for x in GROUPS) + 1}
                GROUPS.append(g); self._json(201, {"groups": [g]}); return True
        if path == "/api/domains/deny/regex":
            if method == "GET":
                self._json(200, {"domains": REGEX_DENY}); return True
            if method == "POST":
                d = {"groups": [0], **self._body(), "type": "deny", "kind": "regex"}
                REGEX_DENY.append(d); self._json(201, {"domains": [d]}); return True
        if path.startswith("/api/domains/deny/regex/") and method == "DELETE":
            dom = unquote(path.rsplit("/", 1)[1])
            REGEX_DENY[:] = [d for d in REGEX_DENY if d["domain"] != dom]
            self.send_response(204); self.end_headers(); return True
        if path == "/api/clients" and method == "POST":
            c = self._body(); CLIENTS[c["client"]] = c
            print(f"  [pi-hole] {c['client']} groups -> {c.get('groups')}", flush=True)
            self._json(201, {"clients": [c]}); return True
        if path.startswith("/api/clients/"):
            ident = unquote(path.rsplit("/", 1)[1])
            if method == "GET":
                self._json(200, {"clients": [CLIENTS[ident]] if ident in CLIENTS else []}); return True
            if method == "PUT":
                CLIENTS[ident] = {**CLIENTS.get(ident, {"client": ident}), **self._body()}
                print(f"  [pi-hole] {ident} groups -> {CLIENTS[ident].get('groups')}", flush=True)
                self._json(200, {"clients": [CLIENTS[ident]]}); return True
            if method == "DELETE":
                CLIENTS.pop(ident, None)
                print(f"  [pi-hole] {ident} removed (back to normal)", flush=True)
                self.send_response(204); self.end_headers(); return True
        return False

    def do_POST(self):
        if self.path.startswith("/api/auth"):
            self._json(200, {"session": {"valid": True, "sid": "demo"}})
        elif not self._manage("POST"):
            self._json(404, {"error": "not found"})

    def do_PUT(self):
        if not self._manage("PUT"):
            self._json(404, {"error": "not found"})

    def do_DELETE(self):
        if not self._manage("DELETE"):
            self._json(404, {"error": "not found"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    _LOG.extend(query_log())
    print(f"Fake Pi-hole on http://127.0.0.1:{port} with {len(_LOG):,} logged lookups")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
