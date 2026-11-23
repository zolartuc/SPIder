"""Ask devices what they are, the polite way devices ask each other.

- mDNS / Bonjour (224.0.0.251:5353): "which services are here?", then the details of each.
  Chromecasts give their model and room name, printers their model, HomeKit gear its
  category, Macs their model, ESPHome boards their friendly name, and so on.
- SSDP / UPnP (239.255.255.250:1900): devices reply with a link to an XML description
  holding their friendly name, maker and model. SPIder only fetches it from the
  replying device itself.
"""

import logging
import re
import socket
import time
from collections import defaultdict
from urllib.parse import urlparse
from xml.etree import ElementTree

import dns.flags
import dns.message
import dns.name
import dns.rdataclass
import dns.rdatatype
import httpx

from ..watchtower.scanner import local_ip

log = logging.getLogger("spider.discover")

MDNS = ("224.0.0.251", 5353)
SSDP = ("239.255.255.250", 1900)
TXT_KEEP = {"md", "fn", "model", "ty", "product", "ci", "friendly_name", "am", "rpmd", "mf",
            "manufacturer", "vendor", "usb_mfg", "usb_mdl", "note", "rpfl"}


def _mcast_socket(ttl: int = 2) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip()))
    except OSError:
        pass
    s.bind(("0.0.0.0", 0))
    return s


# --------------------------------------------------------------------------- mDNS

def _mdns_ask(qnames: list[str], wait: float) -> list[tuple[str, dns.message.Message]]:
    replies = []
    s = _mcast_socket(255)
    try:
        for i in range(0, len(qnames), 12):
            msg = dns.message.Message()
            for q in qnames[i:i + 12]:
                msg.find_rrset(msg.question, dns.name.from_text(q), dns.rdataclass.IN, dns.rdatatype.PTR, create=True)
            s.sendto(msg.to_wire(), MDNS)
        end = time.time() + wait
        while (left := end - time.time()) > 0:
            s.settimeout(left)
            try:
                data, (ip, _) = s.recvfrom(9000)
            except (socket.timeout, OSError):
                break
            try:
                replies.append((ip, dns.message.from_wire(data, ignore_trailing=True)))
            except Exception:
                continue
    finally:
        s.close()
    return replies


def mdns(wait: float = 2.5) -> dict[str, dict]:
    """{ip: {"services": [...], "names": [...], "host": str, "txt": {...}}}"""
    found: dict[str, dict] = defaultdict(lambda: {"services": set(), "names": set(), "host": None, "txt": {}})
    types = set()
    for ip, m in _mdns_ask(["_services._dns-sd._udp.local."], wait):
        for rr in m.answer + m.additional:
            if rr.rdtype == dns.rdatatype.PTR:
                for r in rr:
                    t = r.target.to_text().rstrip(".")
                    if t.endswith(".local") and t.startswith("_"):
                        types.add(t + ".")
                        found[ip]["services"].add(t.removesuffix(".local"))
    if types:
        for ip, m in _mdns_ask(sorted(types), wait):
            d = found[ip]
            for rr in m.answer + m.additional:
                owner = rr.name.to_text().rstrip(".")
                for r in rr:
                    if rr.rdtype == dns.rdatatype.PTR:
                        inst = r.target.to_text().rstrip(".")
                        svc = owner.removesuffix(".local")
                        d["services"].add(svc)
                        # The instance name is the first label, as raw UTF-8 bytes (handles ' and emoji properly).
                        label = r.target.labels[0].decode("utf-8", "replace") if r.target.labels else ""
                        if label and not label.startswith("_"):
                            d["names"].add(label)
                    elif rr.rdtype == dns.rdatatype.TXT:
                        for raw in r.strings:
                            kv = raw.decode(errors="replace")
                            if "=" in kv:
                                k, v = kv.split("=", 1)
                                if k.lower() in TXT_KEEP and v:
                                    d["txt"][k.lower()] = v[:120]
                    elif rr.rdtype == dns.rdatatype.A and r.address == ip:
                        d["host"] = owner.removesuffix(".local")
    return {ip: {"services": sorted(d["services"]), "names": sorted(d["names"])[:6], "host": d["host"], "txt": d["txt"]}
            for ip, d in found.items() if d["services"] or d["host"]}


# --------------------------------------------------------------------------- SSDP / UPnP

def _xml_text(root, tag: str) -> str | None:
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == tag and (el.text or "").strip():
            return el.text.strip()[:120]
    return None


def _describe(url: str) -> dict:
    try:
        r = httpx.get(url, timeout=3, follow_redirects=False)
        body = r.content[:65536]
        if r.status_code != 200 or b"<!ENTITY" in body or b"<!DOCTYPE" in body:
            return {}
        root = ElementTree.fromstring(body)
    except Exception:
        return {}
    return {k: v for k, v in {
        "friendly_name": _xml_text(root, "friendlyName"),
        "manufacturer": _xml_text(root, "manufacturer"),
        "model": _xml_text(root, "modelName"),
        "model_number": _xml_text(root, "modelNumber"),
        "description": _xml_text(root, "modelDescription"),
        "device_type": _xml_text(root, "deviceType"),
    }.items() if v}


def ssdp(wait: float = 3.0) -> dict[str, dict]:
    """{ip: {"server": ..., "types": [...], friendly_name/manufacturer/model...}}"""
    probe = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
             "MX: 2\r\nST: ssdp:all\r\n\r\n").encode()
    seen: dict[str, dict] = defaultdict(lambda: {"locations": [], "types": set(), "server": None})
    s = _mcast_socket(2)
    try:
        s.sendto(probe, SSDP)
        time.sleep(0.2)
        s.sendto(probe, SSDP)
        end = time.time() + wait
        while (left := end - time.time()) > 0:
            s.settimeout(left)
            try:
                data, (ip, _) = s.recvfrom(4096)
            except (socket.timeout, OSError):
                break
            head = {k.lower(): v.strip() for k, v in re.findall(r"^([\w.\-]+):\s*(.*?)\s*$", data.decode(errors="replace"), re.M)}
            d = seen[ip]
            if head.get("server"):
                d["server"] = head["server"][:120]
            if head.get("st"):
                d["types"].add(head["st"][:100])
            loc = head.get("location")
            if loc and urlparse(loc).hostname == ip and loc not in d["locations"] and len(d["locations"]) < 3:
                d["locations"].append(loc)
    finally:
        s.close()

    out = {}
    for ip, d in seen.items():
        info = {"server": d["server"], "types": sorted(d["types"])[:12]}
        for loc in d["locations"]:
            desc = _describe(loc)
            if desc:
                info.update(desc)
                break
        out[ip] = {k: v for k, v in info.items() if v}
    return out
