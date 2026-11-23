"""Decide what a device is from everything SPIder has learned about it.

Each rule that matches proposes a type with a weight (how sure that clue makes us) and
a line of plain-English evidence. The strongest type wins; its evidence, plus anything
that agrees with it, is what the user sees under "How SPIder knows".
"""

import json
import re
from urllib.parse import urlparse

KINDS = [
    "Router", "Home server", "Pi-hole", "Home Assistant", "This SPIder", "Raspberry Pi", "NAS",
    "Windows PC", "Mac", "Linux computer", "Computer or laptop", "iPhone or iPad", "Android phone or tablet",
    "Phone, tablet or laptop", "Smartwatch", "Smart TV", "TV streamer", "Smart speaker", "Speaker", "Games console",
    "Printer", "3D printer", "Camera", "Smart plug or switch", "Smart bulb or light", "Smart home hub",
    "Smart home device", "Media server", "Network equipment", "Unknown",
]

GENERIC = {"Smart home device", "Computer or laptop", "Phone, tablet or laptop", "Network equipment",
           "Linux computer", "Unknown"}

SOURCE_LABEL = {
    "you": "You", "dhcp": "Its own announcement (DHCP)", "mdns": "Bonjour (mDNS)", "ssdp": "UPnP",
    "ports": "Open ports", "web": "Its web page", "maker": "Maker (from its hardware ID)", "name": "Its name",
    "bambu": "Bambu broadcast", "pihole": "Pi-hole", "network": "Its address",
}

# mDNS service -> (kind, weight, what it means)
MDNS_SERVICES = {
    "_googlecast._tcp": ("TV streamer", 85, "offers Google Cast (Chromecast)"),
    "_airplay._tcp": ("TV streamer", 70, "accepts AirPlay video"),
    "_raop._tcp": ("Speaker", 65, "accepts AirPlay audio"),
    "_companion-link._tcp": ("iPhone or iPad", 70, "is an Apple device (Companion Link)"),
    "_apple-mobdev2._tcp": ("iPhone or iPad", 80, "is an iPhone or iPad"),
    "_ipp._tcp": ("Printer", 90, "is a network printer (IPP)"), "_ipps._tcp": ("Printer", 90, "is a network printer"),
    "_printer._tcp": ("Printer", 85, "is a printer"), "_pdl-datastream._tcp": ("Printer", 85, "is a printer"),
    "_scanner._tcp": ("Printer", 70, "is a scanner or all-in-one printer"),
    "_hap._tcp": ("Smart home device", 80, "is a HomeKit accessory"),
    "_matter._tcp": ("Smart home device", 75, "is a Matter smart home device"),
    "_matterc._udp": ("Smart home device", 75, "is a Matter smart home device waiting to be set up"),
    "_hue._tcp": ("Smart home hub", 90, "is a Philips Hue bridge"),
    "_sonos._tcp": ("Speaker", 90, "is a Sonos speaker"),
    "_spotify-connect._tcp": ("Speaker", 55, "plays Spotify Connect"),
    "_esphomelib._tcp": ("Smart home device", 85, "runs ESPHome"),
    "_home-assistant._tcp": ("Home Assistant", 90, "runs Home Assistant"),
    "_amzn-wplay._tcp": ("TV streamer", 75, "is an Amazon Fire TV"),
    "_androidtvremote2._tcp": ("TV streamer", 80, "is an Android / Google TV"),
    "_nvstream._tcp": ("Computer or laptop", 55, "streams games (NVIDIA GameStream)"),
    "_smb._tcp": ("Computer or laptop", 40, "shares files (SMB)"),
    "_afpovertcp._tcp": ("Mac", 55, "shares files the Mac way (AFP)"),
    "_ssh._tcp": ("Linux computer", 35, "accepts SSH logins"),
    "_workstation._tcp": ("Linux computer", 50, "announces itself as a workstation"),
    "_device-info._tcp": ("Computer or laptop", 30, "describes its hardware"),
    "_octoprint._tcp": ("3D printer", 90, "runs OctoPrint"),
    "_elg._tcp": ("Smart bulb or light", 85, "is an Elgato light"),
    "_shelly._tcp": ("Smart plug or switch", 85, "is a Shelly device"),
    "_wled._tcp": ("Smart bulb or light", 85, "is a WLED light strip"),
}

HAP_CATEGORY = {
    "2": "Smart home hub", "5": "Smart bulb or light", "7": "Smart plug or switch", "8": "Smart plug or switch",
    "9": "Smart home device", "10": "Smart home device", "17": "Camera", "18": "Camera",
}

# Port -> (kind, weight, meaning)
PORTS = {
    6668: ("Smart plug or switch", 72, "answers on Tuya's local control port (6668)"),
    554: ("Camera", 70, "streams video (RTSP, port 554)"),
    9100: ("Printer", 80, "accepts print jobs (port 9100)"),
    631: ("Printer", 70, "accepts print jobs (IPP, port 631)"),
    62078: ("iPhone or iPad", 75, "has Apple's sync port open (62078)"),
    3389: ("Windows PC", 75, "accepts Remote Desktop (port 3389)"),
    548: ("Mac", 55, "shares files the Mac way (AFP)"),
    8123: ("Home Assistant", 85, "runs Home Assistant (port 8123)"),
    32400: ("Media server", 70, "runs Plex (port 32400)"),
    8009: ("TV streamer", 70, "accepts Google Cast (port 8009)"),
    5001: ("NAS", 45, "has Synology's secure port open (5001)"),
}

WEB_TITLES = [
    (r"\bSPIder\b", "This SPIder", 92, "is SPIder"),
    (r"pi-?hole", "Pi-hole", 92, "is a Pi-hole"),
    (r"home assistant", "Home Assistant", 90, "is Home Assistant"),
    (r"tasmota", "Smart plug or switch", 85, "runs Tasmota"),
    (r"shelly", "Smart plug or switch", 85, "is a Shelly device"),
    (r"esphome", "Smart home device", 80, "runs ESPHome"),
    (r"octoprint|mainsail|fluidd", "3D printer", 88, "is a 3D printer controller"),
    (r"synology|diskstation|qnap|truenas|unraid", "NAS", 85, "is a NAS"),
    (r"hikvision|reolink|dahua|ip ?cam|webcam|amcrest|tapo camera", "Camera", 80, "is a camera"),
    (r"\b(hp|epson|brother|canon|xerox|lexmark|kyocera)\b.*(print|laserjet|officejet|deskjet|ews)|laserjet|officejet|deskjet", "Printer", 85, "is a printer"),
    (r"gateway|router|hub manager|smart hub|livebox|technicolor|sagemcom|arcadyan|netgear|asus wrt|fritz|unifi|openwrt|tp-?link", "Router", 70, "looks like a router's admin page"),
]

NAME_HINTS = [
    (r"iphone", "iPhone or iPad", "iPhone"), (r"ipad", "iPhone or iPad", "iPad"),
    (r"macbook|imac|mac-?mini|mac-?studio|mbp", "Mac", None),
    (r"android|galaxy|pixel|oneplus|redmi|poco|moto ?g|huawei|honor|oppo|vivo|realme|nokia", "Android phone or tablet", None),
    (r"^desktop-|^laptop-|-pc$|^pc-|windows", "Windows PC", None),
    (r"raspberrypi|^rpi|raspberry", "Raspberry Pi", None),
    (r"echo|alexa", "Smart speaker", "Amazon Echo"), (r"fire-?tv|^aft", "TV streamer", "Amazon Fire TV"),
    (r"chromecast|google-?tv", "TV streamer", "Chromecast"), (r"nest-?(mini|audio|hub)|google-?home", "Smart speaker", "Google Nest"),
    (r"roku", "TV streamer", "Roku"), (r"ps4|ps5|playstation", "Games console", "PlayStation"),
    (r"xbox", "Games console", "Xbox"), (r"nintendo|^switch$", "Games console", "Nintendo Switch"),
    (r"printer|laserjet|officejet|deskjet|^hp[a-f0-9]{6}|epson|^brn|brother|canon", "Printer", None),
    (r"doorbell|camera|^cam|-cam\b|ipcam", "Camera", None),
    (r"\btv\b|bravia|lgwebos|webos|tizen|samsung-?tv|tv-", "Smart TV", None),
    (r"sonos", "Speaker", "Sonos"), (r"homepod", "Smart speaker", "HomePod"),
    (r"watch", "Smartwatch", None), (r"\bhue\b|philips-hue", "Smart home hub", "Philips Hue bridge"),
    (r"plug|socket|outlet|smartplug", "Smart plug or switch", None), (r"bulb|lamp|light|led", "Smart bulb or light", None),
    (r"esp[_-]|tasmota|shelly|tuya|wlan0|smartlife", "Smart home device", None),
    (r"homeassistant|home-assistant", "Home Assistant", None), (r"pihole|pi-hole", "Pi-hole", None),
    (r"nas|diskstation|synology|qnap", "NAS", None), (r"bambu|p1s|p1p|x1c", "3D printer", "Bambu Lab"),
]

MAKERS = [  # (pattern on maker name, kind, weight, model text)
    (r"private mac", "Phone, tablet or laptop", 45, "Private address"),
    (r"tuya", "Smart plug or switch", 55, "Tuya (plug, bulb or switch)"),
    (r"espressif", "Smart home device", 45, "ESP Wi-Fi chip"),
    (r"bambu", "3D printer", 85, "Bambu Lab"),
    (r"raspberry", "Raspberry Pi", 55, "Raspberry Pi"),
    (r"sonos", "Speaker", 75, "Sonos"), (r"nintendo", "Games console", 75, "Nintendo"),
    (r"sony interactive", "Games console", 75, "PlayStation"),
    (r"amazon", "Smart speaker", 40, "Amazon (Echo, Fire TV or Kindle)"),
    (r"google", "Smart speaker", 35, "Google (Nest, Chromecast or Pixel)"),
    (r"apple", "Phone, tablet or laptop", 40, "Apple device"),
    (r"ring llc|ring inc", "Camera", 70, "Ring doorbell or camera"),
    (r"signify|philips lighting", "Smart home hub", 65, "Philips Hue"),
    (r"alertme|hive", "Smart home hub", 70, "Hive heating hub"),
    (r"synology|qnap", "NAS", 75, None),
    (r"hikvision|reolink|dahua|amcrest", "Camera", 70, None),
    (r"hewlett|\bhp inc|epson|brother|canon|xerox|lexmark|kyocera", "Printer", 60, None),
    (r"\blg electronics|hisense|tcl|vizio|roku|panasonic", "Smart TV", 50, None),
    (r"samsung", "Smart TV", 30, "Samsung"),
    (r"sagemcom|technicolor|arcadyan|huawei|zte|askey|sercomm|netgear|tp-?link|ubiquiti|mikrotik|avm", "Network equipment", 35, None),
    (r"intel|realtek|liteon|lite-on|hon hai|foxconn|azurewave|murata|universal global|qualcomm|mediatek|micro-star|giga-byte|asustek|dell|lenovo", "Computer or laptop", 30, None),
    (r"xiaomi", "Smart home device", 35, "Xiaomi"),
]


def _ev(source: str, text: str) -> dict:
    return {"source": SOURCE_LABEL.get(source, source), "text": text}


def _apple_model(code: str) -> tuple[str, str] | None:
    c = code or ""
    if c.startswith("iPhone") or re.match(r"^D\d+AP$", c):
        return "iPhone or iPad", "iPhone"
    if c.startswith("iPad") or re.match(r"^J\d+AP$", c):
        return "iPhone or iPad", "iPad"
    if c.startswith("AudioAccessory"):
        return "Smart speaker", "HomePod"
    if c.startswith("AppleTV"):
        return "TV streamer", "Apple TV"
    if c.startswith(("Mac", "iMac")):
        return "Mac", c
    if c.startswith("Watch"):
        return "Smartwatch", "Apple Watch"
    return None


def classify(device: dict, facts: dict[str, dict[str, str]], context: dict) -> dict:
    """Return {"kind", "model", "confidence", "evidence", "name"}."""
    cands: list[tuple[str, int, str | None, dict]] = []  # (kind, weight, model, evidence)
    names: list[tuple[int, str, str]] = []                # (priority, name, source)
    roles: list[str] = []
    ip = device.get("ip") or ""

    def add(kind, weight, model, source, text):
        cands.append((kind, weight, model, _ev(source, text)))

    def j(source, key, default=None):
        raw = facts.get(source, {}).get(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    # --- the user always wins
    if device.get("kind_user"):
        add(device["kind_user"], 1000, device.get("model"), "you", "An admin set this.")

    # --- network position
    if ip and ip == context.get("local_ip"):
        roles.append("SPIder")
        add("This SPIder", 92, None, "network", "This is the machine SPIder runs on.")
    if ip and ip == context.get("pihole_host"):
        roles.append("Pi-hole")
        add("Pi-hole", 90, None, "network", "Your Pi-hole lives at this address.")
    if ip and ip == context.get("gateway"):
        add("Router", 88, None, "network", f"It's your network's gateway ({ip}).")

    # --- Bambu printers announce themselves
    if facts.get("bambu"):
        model = facts["bambu"].get("model")
        add("3D printer", 98, f"Bambu Lab {model}" if model else "Bambu Lab", "bambu", f"Announces itself as a Bambu Lab {model or 'printer'}.")
        if facts["bambu"].get("name"):
            names.append((95, facts["bambu"]["name"], "bambu"))

    # --- mDNS / Bonjour
    m = facts.get("mdns", {})
    services = j("mdns", "services", []) or []
    txt = j("mdns", "txt", {}) or {}
    for svc in services:
        if svc in MDNS_SERVICES:
            kind, w, meaning = MDNS_SERVICES[svc]
            if svc == "_home-assistant._tcp":
                roles.append("Home Assistant")
            add(kind, w, None, "mdns", f"It {meaning}.")
    if "_hap._tcp" in services and txt.get("ci") in HAP_CATEGORY:
        add(HAP_CATEGORY[txt["ci"]], 88, txt.get("md"), "mdns", f"HomeKit says it's a {HAP_CATEGORY[txt['ci']].lower()}.")
    if "_googlecast._tcp" in services and txt.get("md"):
        md = txt["md"]
        kind = "Smart speaker" if re.search(r"mini|audio|speaker|nest hub|home\b", md, re.I) else "TV streamer"
        if re.search(r"tv|bravia|vizio|tcl|hisense|philips|sharp", md, re.I) and "Chromecast" not in md:
            kind = "Smart TV"
        add(kind, 92, md, "mdns", f"Google Cast says it's a {md}.")
    for key in ("model", "am", "rpmd"):
        if (hit := _apple_model(txt.get(key, ""))):
            add(hit[0], 88, hit[1] if hit[1] != txt.get(key) else txt[key], "mdns", f"Apple model code {txt[key]} means it's an {hit[1]}." if hit[1] in ("iPhone", "iPad", "Apple TV", "Apple Watch") else f"Apple model code {txt[key]} ({hit[1]}).")
            break
    if txt.get("ty") or txt.get("product"):
        add("Printer", 92, (txt.get("ty") or txt.get("product", "")).strip("()"), "mdns", f"It describes itself as a {(txt.get('ty') or txt.get('product')).strip('()')}.")
    for key, prio in (("fn", 90), ("friendly_name", 88)):
        if txt.get(key):
            names.append((prio, txt[key], "mdns"))
    for n in j("mdns", "names", []) or []:
        names.append((80, n, "mdns"))
    if m.get("host"):
        names.append((60, m["host"], "mdns"))

    # --- UPnP
    u = facts.get("ssdp", {})
    if u:
        maker, model, ftype = u.get("manufacturer", ""), u.get("model", ""), (u.get("device_type", "") + " " + " ".join(j("ssdp", "types", []) or []))
        brand = re.sub(r"[^\w-]", "", maker.split(" ")[0]) if maker else ""
        parts = [model] if brand and model.lower().startswith(brand.lower()) else [brand, model]
        if u.get("model_number") and u["model_number"] not in model:
            parts.append(u["model_number"])
        label = " ".join(x for x in parts if x).strip()
        if "InternetGatewayDevice" in ftype:
            add("Router", 85, label or None, "ssdp", "It describes itself as an internet gateway (router).")
        elif "ZonePlayer" in ftype:
            add("Speaker", 92, label or "Sonos", "ssdp", "It describes itself as a Sonos player.")
        elif re.search(r"MediaRenderer|dial-multiscreen|RemoteControl|MediaServer", ftype):
            tvish = re.search(r"tv|samsung|lg|sony|bravia|hisense|tcl|philips|panasonic|vizio|roku|sharp|toshiba", f"{maker} {model} {u.get('friendly_name', '')}", re.I)
            kind = "Smart TV" if tvish else ("Speaker" if "Renderer" in ftype else "Media server")
            if re.search(r"roku|fire ?tv|chromecast|apple ?tv|shield", f"{maker} {model}", re.I):
                kind = "TV streamer"
            if re.search(r"xbox|playstation", f"{maker} {model}", re.I):
                kind = "Games console"
            add(kind, 82, label or None, "ssdp", f"It describes itself over UPnP as {label or 'a media device'}.")
        elif label:
            add("Smart home device", 35, label, "ssdp", f"It describes itself over UPnP as {label}.")
        if u.get("friendly_name"):
            names.append((85, u["friendly_name"], "ssdp"))

    # --- open ports and web page
    ports = j("ports", "open", []) or []
    for p in ports:
        if p in PORTS:
            kind, w, meaning = PORTS[p]
            if p == 8123:
                roles.append("Home Assistant")
            add(kind, w, None, "ports", f"It {meaning}.")
    if 445 in ports and 139 in ports:
        add("Windows PC", 50, None, "ports", "It shares files the Windows way (ports 139 and 445).")
    if 53 in ports and ip != context.get("gateway") and ip != context.get("pihole_host"):
        add("Home server", 45, None, "ports", "It answers DNS lookups (port 53), like a Pi-hole or router.")
    if 1883 in ports:
        add("Smart home hub", 45, None, "ports", "It runs an MQTT broker (port 1883), used by smart home hubs.")
    web = j("ports", "web", {}) or {}
    if web.get("title"):
        for pattern, kind, w, meaning in WEB_TITLES:
            if re.search(pattern, web["title"], re.I):
                if kind in ("This SPIder", "Pi-hole", "Home Assistant"):
                    roles.append({"This SPIder": "SPIder"}.get(kind, kind))
                add(kind, w, None, "web", f"Its web page “{web['title']}” says it {meaning}.")
                break

    # --- what it calls itself when it joins (DHCP)
    d = facts.get("dhcp", {})
    vc = d.get("vendor_class", "")
    if vc.startswith("android-dhcp"):
        add("Android phone or tablet", 72, None, "dhcp", f"It identifies as Android ({vc}).")
    elif vc.startswith("MSFT"):
        add("Windows PC", 72, None, "dhcp", "It identifies as Windows.")
    elif vc.startswith("dhcpcd"):
        add("Linux computer", 35, None, "dhcp", f"It runs Linux ({vc}).")
    elif vc.startswith("udhcp"):
        add("Smart home device", 30, None, "dhcp", "It runs a tiny embedded Linux, typical of smart gadgets.")
    if d.get("hostname"):
        names.append((88, d["hostname"], "dhcp"))
    if facts.get("pihole", {}).get("name"):
        names.append((55, facts["pihole"]["name"], "pihole"))
    if device.get("hostname"):
        names.append((40, device["hostname"].split(".")[0], "name"))

    # --- names that give it away
    for prio, n, src in sorted(names, reverse=True)[:4]:
        for pattern, kind, model in NAME_HINTS:
            if re.search(pattern, n, re.I):
                add(kind, 62 if src != "name" else 55, model, src, f"It's called “{n}”.")
                break

    # --- maker (MAC address)
    vendor = device.get("vendor") or ""
    for pattern, kind, w, model in MAKERS:
        if re.search(pattern, vendor, re.I):
            add(kind, w, model, "maker", f"It's made by {vendor.rstrip('.')}." if not vendor.startswith("Private") else
                "It uses a private (randomised) hardware address, which phones, tablets and laptops do for privacy.")
            break

    # --- several server roles on one box
    roles = list(dict.fromkeys(roles))
    if len(roles) >= 2:
        # A box with several jobs is described by all of them; this conclusion outranks its parts.
        add("Home server", 400, ", ".join(roles), "network", "It runs " + ", ".join(roles) + ".")

    if not cands:
        return {"kind": "Unknown", "model": None, "confidence": "low",
                "evidence": [_ev("maker", f"Only its maker is known: {vendor}.")] if vendor else [], "name": _best_name(names)}

    # Combine: total weight per kind, so agreeing clues add up; the single strongest clue breaks ties.
    totals: dict[str, float] = {}
    peak: dict[str, int] = {}
    for kind, w, _, _ in cands:
        totals[kind] = totals.get(kind, 0) + w
        peak[kind] = max(peak.get(kind, 0), w)
    pool = list(totals)
    # Vague types ("smart home device", "computer or laptop"...) only win when nothing specific
    # has decent evidence: a name saying "camera" beats two hints that it's "some gadget".
    specific = [k for k in pool if k not in GENERIC]
    if specific and max(peak[k] for k in specific) >= 50 and \
            max(peak[k] for k in specific) >= max((peak[k] for k in pool if k in GENERIC), default=0) - 15:
        pool = specific
    best = max(pool, key=lambda k: (totals[k], peak[k]))
    mine = sorted([c for c in cands if c[0] == best], key=lambda c: -c[1])
    strongest = mine[0][1]
    model = next((c[2] for c in mine if c[2]), None)
    if not model and vendor and not vendor.startswith("Private"):
        model = _short_maker(vendor)
    evidence = [c[3] for c in mine]
    # Mention the maker even when it didn't decide it.
    if vendor and not any(e["source"] == SOURCE_LABEL["maker"] for e in evidence):
        evidence.append(_ev("maker", f"It's made by {vendor.rstrip('.')}." if not vendor.startswith("Private") else
                            "It uses a private (randomised) hardware address."))
    confidence = "high" if strongest >= 80 or (totals[best] >= 120 and len(mine) >= 2) else "medium" if strongest >= 50 or totals[best] >= 80 else "low"
    return {"kind": best, "model": model, "confidence": confidence, "evidence": evidence[:8], "name": _best_name(names)}


def _short_maker(vendor: str) -> str:
    v = re.sub(r"[,.]?\s*\b(Inc|Ltd|Co|LLC|GmbH|SAS|Limited|Corporation|Corp|International|Technologies|Technology|Electronics|Trading)\b\.?", "", vendor, flags=re.I)
    return re.sub(r"\s+", " ", v).strip(" ,.") or vendor


def _best_name(names: list[tuple[int, str, str]]) -> str | None:
    for _, n, _ in sorted(names, reverse=True):
        n = n.strip()
        if n and not re.fullmatch(r"(wlan0|localhost|esp_[0-9a-f]+|[0-9a-f]{12}|android-[0-9a-f]+|unknown)", n, re.I):
            return n[:60]
    return None


def context_for(settings, local_ip: str, gateway: str | None) -> dict:
    return {"local_ip": local_ip, "gateway": gateway,
            "pihole_host": urlparse(settings.pihole_url).hostname if settings.pihole_url else None}
