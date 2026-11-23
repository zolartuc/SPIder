# SPIder

**A Raspberry Pi home network watchtower and honeypot.** Like a spider in its web, it feels every vibration on your network: every device that joins, every one that leaves, and anything that wanders into the honeypot.

> The name: a spider watches its web, and **SPI** is one of the Raspberry Pi's hardware buses.

Runs happily on a Raspberry Pi 4 (2 GB or more), but works on any Linux, macOS or Windows machine too.

## Features

### Watchtower (working now)
- Discovers every device on your LAN using ARP, so it works behind any router, including ISP ones
- Looks up each device's maker from its MAC address, entirely offline
- Spots phones and laptops that use private (randomised) MAC addresses
- Alerts on new devices, optionally by push notification to your phone via [ntfy](https://ntfy.sh)
- Online/offline history and IP-change tracking
- Lets you name devices and mark them as known, so strangers stand out

### Pi-hole stats (working now)
- Shows queries, blocked count and block percentage from a Pi-hole elsewhere on your network
- Supports Pi-hole **v6** (REST API) and **v5** (api.php)

### Dashboard
- A live spider-web map of your network, plus separate pages for Devices, Activity, Pi-hole and Honeypot
- Four themes: **Silk**, **Bridge** (starship console), **Cyanotype** (blueprint) and **Amber** (CRT terminal), saved per account
- Accounts with **admin** and **viewer** roles. The first visit asks you to create the admin
- Guests can look without signing in (you can switch this off). Any change needs an admin

### Settings page
- The Pi: display name, hostname, timezone and live system info (model, OS, CPU temperature, uptime)
- Network: DHCP or a fixed address, gateway and DNS
- Scanning, Pi-hole, phone alerts (with a test button) and user management
- Hostname, timezone and network changes are applied to the Pi when `SPIDER_ALLOW_SYSTEM_CHANGES=true` and SPIder runs on the Pi as root. Otherwise SPIder shows the exact commands to run

### Honeypot (next)
- See the [roadmap](#roadmap)

## Quick start (any PC, for development)

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional, edit to taste
python -m spider
```

Then open <http://localhost:8080>.

> On Windows without [Npcap](https://npcap.com), and on Linux without root, SPIder automatically falls back to a ping sweep plus the OS ARP table. It's slower but needs no special permissions. The status line shows which method was used.

## Running on a Raspberry Pi

1. Flash **Raspberry Pi OS Lite (64-bit)** and enable SSH.
2. Install Docker:
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER   # log out and back in
   ```
3. Clone and start:
   ```bash
   git clone https://github.com/zolartuc/SPIder.git
   cd SPIder
   cp .env.example .env && nano .env
   docker compose up -d --build
   ```
4. Open `http://<pi-ip>:8080`.

Give the Pi a **fixed IP address** (a DHCP reservation in your router) so you can always find the dashboard.

## Configuration

Settings live in `.env`; see [`.env.example`](.env.example) for all of them.

| Setting | Default | What it does |
|---|---|---|
| `SPIDER_SUBNET` | auto | Subnet to scan, e.g. `192.168.1.0/24` |
| `SPIDER_SCAN_INTERVAL` | `300` | Seconds between scans |
| `SPIDER_SCAN_METHOD` | `auto` | `arp`, `ping` or `auto` |
| `SPIDER_OFFLINE_AFTER_MINUTES` | `15` | How long before a silent device counts as offline |
| `SPIDER_NTFY_TOPIC` | – | ntfy topic for push alerts (pick something unguessable) |
| `SPIDER_PIHOLE_URL` | – | e.g. `http://192.168.1.2` |
| `SPIDER_PIHOLE_PASSWORD` | – | v6: an app password. v5: the API token |

### Pi-hole setup
- **v6:** In the Pi-hole web UI, go to *Settings → Web interface / API → Configure app password*, and put that password in `SPIDER_PIHOLE_PASSWORD`.
- **v5:** Go to *Settings → API → Show API token*.

## Security notes
- Passwords are hashed with scrypt. Sessions are random server-side tokens in an HttpOnly, SameSite=Strict cookie. Sign-in is throttled after 5 wrong passwords.
- The dashboard runs over plain HTTP, so keep it on your LAN and never port-forward it.
- There's no password-reset email. If you lose the admin password, delete the `users` table rows in `data/spider.db` to run the first-time setup again.
- Device hostnames are treated as untrusted input and escaped before display.

## Roadmap

- [x] Device discovery, vendor lookup and new-device alerts
- [x] Pi-hole stats
- [x] Accounts, roles and a Settings page
- [x] Separate pages and four themes
- [ ] **Honeypot, phase 1 (internal canary):** fake SSH, Telnet and web login on the LAN. Any connection means something on your network is scanning, which deserves an instant alert
- [ ] Honeypot: record attempted usernames, passwords and shell commands
- [ ] Per-device open-port scan ("what is this smart plug listening on?")
- [ ] Pi-hole: top blocked domains, and queries per device matched to the device list
- [ ] Device online/offline timeline chart
- [ ] **Honeypot, phase 2 (internet-facing):** run on an isolated VLAN or a cheap VPS, with an attacker world map
- [ ] Bambu Lab printer module (MQTT status and camera)

## License

MIT
