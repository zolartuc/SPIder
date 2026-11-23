"""Fill a fresh SPIder database with a made-up home network, for demos and screenshots.

Usage:
    python scripts/demo/seed.py <data-dir>

Creates an admin account "demo" (password "demo-password") in that folder.
None of this is real data: names, addresses and hardware IDs are invented.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit(__doc__)

data_dir = Path(sys.argv[1]).resolve()
data_dir.mkdir(parents=True, exist_ok=True)
if (data_dir / "spider.db").exists():
    sys.exit(f"{data_dir / 'spider.db'} already exists. Use an empty folder.")

os.environ["SPIDER_DATA_DIR"] = str(data_dir)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from spider import auth, settings_store  # noqa: E402
from spider.db import connect, init_db  # noqa: E402

NOW = datetime.now(timezone.utc)


def ts(**ago) -> str:
    return (NOW - timedelta(**ago)).isoformat(timespec="seconds")


# (ip, mac, vendor, hostname, name, known, online, first seen days ago)
DEVICES = [
    ("192.168.1.1",   "a4:91:b1:3c:20:01", "Sagemcom Broadband SAS",       None,              "Router",             1, 1, 9),
    ("192.168.1.2",   "d8:3a:dd:51:0e:42", "Raspberry Pi Trading Ltd",     "pihole.lan",      "Pi-hole",         1, 1, 9),
    ("192.168.1.10",  "dc:a6:32:7b:19:c4", "Raspberry Pi Trading Ltd",     "spider.lan",      "SPIder",          1, 1, 9),
    ("192.168.1.20",  "08:fb:ea:42:6d:10", "Bambu Lab",                    None,              "Bambu P1S",       1, 1, 8),
    ("192.168.1.21",  "d0:d0:03:8e:22:7a", "Samsung Electronics Co.,Ltd",  None,              "Living room TV",  1, 1, 9),
    ("192.168.1.22",  "48:a6:b8:19:f2:33", "Sonos, Inc.",                  "sonos-kitchen",   "Kitchen speaker", 1, 1, 9),
    ("192.168.1.23",  "00:1c:2b:66:a1:0c", "Alertme.com Limited",          None,              "Heating hub",     1, 1, 9),
    ("192.168.1.24",  "c0:4e:30:2a:9b:58", "Espressif Inc.",               "plug-desk",       "Desk plug",       1, 1, 7),
    ("192.168.1.30",  "da:41:7e:0f:c3:91", "Private MAC (phone/laptop)",   None,              "My phone",        1, 1, 9),
    ("192.168.1.32",  "f4:8e:38:a7:5d:12", "Dell Inc.",                    "work-laptop",     "Work laptop",     1, 1, 6),
    ("192.168.1.33",  "00:d8:61:c4:e8:0b", "Micro-Star INTL CO., LTD.",    "gaming-pc",       "Gaming PC",       1, 1, 9),
    ("192.168.1.41",  "44:65:0d:b2:7c:6e", "Amazon Technologies Inc.",     None,              "Bedroom Echo",    1, 0, 9),
    ("192.168.1.42",  "98:41:5c:3e:01:d7", "Nintendo Co.,Ltd",             None,              "Switch",          1, 0, 5),
    ("192.168.1.31",  "5e:c2:19:8a:44:f0", "Private MAC (phone/laptop)",   None,              None,              0, 1, 2),
    ("192.168.1.40",  "68:57:2d:91:aa:03", "Tuya Smart Inc.",              None,              None,              0, 1, 1),
    ("192.168.1.50",  "3c:7c:3f:5b:e2:9d", None,                           None,              None,              0, 1, 0),
    ("192.168.1.51",  "64:09:80:17:2c:b4", "Xiaomi Communications Co Ltd", None,              None,              0, 0, 3),
]

init_db()

with connect() as conn:
    for ip, mac, vendor, host, name, known, online, days in DEVICES:
        first = ts(days=days, hours=3, minutes=len(mac))
        last = ts(minutes=1) if online else ts(hours=5 + days)
        conn.execute(
            "INSERT INTO devices (mac, ip, hostname, vendor, name, known, online, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mac, ip, host, vendor, name, known, online, first, last),
        )
        conn.execute("INSERT INTO events (ts, type, mac, detail) VALUES (?, 'new_device', ?, ?)",
                     (first, mac, f"{ip} {host or ''} {vendor or ''}".strip()))

    by_ip = {d[0]: d[1] for d in DEVICES}
    story = [
        (ts(hours=26), "offline", "192.168.1.42", "192.168.1.42"),
        (ts(hours=21), "online", "192.168.1.32", "192.168.1.32"),
        (ts(hours=19), "ip_changed", "192.168.1.24", "192.168.1.19 -> 192.168.1.24"),
        (ts(hours=14), "offline", "192.168.1.41", "192.168.1.41"),
        (ts(hours=9), "online", "192.168.1.20", "192.168.1.20"),
        (ts(hours=5), "offline", "192.168.1.51", "192.168.1.51"),
        (ts(hours=3), "online", "192.168.1.33", "192.168.1.33"),
        (ts(minutes=48), "online", "192.168.1.30", "192.168.1.30"),
        (ts(minutes=12), "online", "192.168.1.31", "192.168.1.31"),
    ]
    for when, kind, ip, detail in story:
        conn.execute("INSERT INTO events (ts, type, mac, detail) VALUES (?, ?, ?, ?)", (when, kind, by_ip[ip], detail))

    # Events are shown newest first by id, so re-insert them in time order.
    rows = conn.execute("SELECT ts, type, mac, detail FROM events ORDER BY ts").fetchall()
    conn.execute("DELETE FROM events")
    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'events'")
    conn.executemany("INSERT INTO events (ts, type, mac, detail) VALUES (?, ?, ?, ?)", [tuple(r) for r in rows])

settings_store.update({
    "device_name": "SPIder",
    "hostname": "spider",
    "timezone": "Europe/London",
    "lan_mode": "static",
    "lan_address": "192.168.1.10/24",
    "lan_gateway": "192.168.1.1",
    "lan_dns": "192.168.1.2, 1.1.1.1",
    "scan_subnet": "192.168.1.0/24",
    "pihole_url": os.environ.get("SPIDER_DEMO_PIHOLE_URL", "http://127.0.0.1:8099"),
    "pihole_password": "demo",
    "ntfy_topic": "spider-demo-alerts",
})

admin = auth.create_user("demo", "demo-password", "admin")
auth.create_user("guest", "guest-password", "viewer")
auth.set_theme(admin["id"], "bridge")

print(f"Demo network written to {data_dir}")
print(json.dumps({"devices": len(DEVICES), "admin": "demo"}))
