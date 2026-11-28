"""Network discovery.

Two strategies:
  * arp  - raw ARP broadcast via scapy. Fast and thorough, but needs root on
           Linux (or Npcap + admin on Windows).
  * ping - ping every address, then read the OS ARP table. Works unprivileged
           anywhere, slightly less thorough (devices that ignore ping still
           usually show up because the ARP exchange happens anyway).
"""

from __future__ import annotations

import ipaddress
import logging
import platform
import re
import shutil
import socket
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("spider.scanner")

IS_WINDOWS = platform.system() == "Windows"

_IP_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")
_MAC_RE = re.compile(r"([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})")


@dataclass
class Host:
    ip: str
    mac: str
    hostname: str | None = None


# --------------------------------------------------------------------------- helpers

def local_ip() -> str:
    """The IP this machine uses on the LAN (no packets are actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class NotOnNetwork(RuntimeError):
    pass


def network_ready() -> str:
    """This machine's LAN address, or NotOnNetwork while it hasn't got one (just after boot)."""
    ip = local_ip()
    if ipaddress.ip_address(ip).is_loopback:
        raise NotOnNetwork("The Pi isn't on the network yet. SPIder will try again shortly.")
    return ip


def default_subnet() -> str:
    return str(ipaddress.ip_network(f"{network_ready()}/24", strict=False))


def normalise_mac(mac: str) -> str:
    parts = re.split(r"[:-]", mac.strip())
    return ":".join(p.zfill(2) for p in parts).lower()


def is_unicast(mac: str) -> bool:
    if mac in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
        return False
    return not (int(mac[:2], 16) & 1)  # multicast bit


SYS_NET = Path("/sys/class/net")


def _iface_ipv4(name: str) -> str | None:
    try:
        import fcntl
        import struct
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            packed = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name[:15].encode()))  # SIOCGIFADDR
        return socket.inet_ntoa(packed[20:24])
    except (OSError, ImportError):
        return None


def own_interfaces() -> dict[str, dict]:
    """Every network card on this machine (Linux): name -> {mac, ip, physical}."""
    out = {}
    if not SYS_NET.is_dir():
        return out
    for d in SYS_NET.iterdir():
        try:
            mac = (d / "address").read_text().strip().lower()
        except OSError:
            continue
        if d.name == "lo" or not mac or mac == "00:00:00:00:00:00":
            continue
        out[d.name] = {"mac": mac, "ip": _iface_ipv4(d.name), "physical": (d / "device").exists()}
    return out


def own_mac(ip: str | None = None) -> str:
    """The hardware ID of the card that holds this machine's LAN address.

    Not uuid.getnode(): on a Pi running Docker that often picks one of Docker's virtual
    cards (02:42:...), and a different one after each restart.
    """
    ip = ip or local_ip()
    cards = own_interfaces()
    for c in cards.values():
        if c["ip"] == ip:
            return c["mac"]
    for name in ("eth0", "end0", "wlan0"):
        if name in cards:
            return cards[name]["mac"]
    physical = sorted(n for n, c in cards.items() if c["physical"])
    if physical:
        return cards[physical[0]]["mac"]
    node = uuid.getnode()
    return ":".join(f"{(node >> s) & 0xFF:02x}" for s in range(40, -1, -8))


# --------------------------------------------------------------------------- strategies

def scan_arp(subnet: str, timeout: float = 2.0) -> list[Host]:
    from scapy.all import ARP, Ether, conf, srp  # imported lazily - scapy is slow to load

    conf.verb = 0
    answered, _ = srp(
        Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=subnet), timeout=timeout, retry=1
    )
    return [Host(ip=r.psrc, mac=normalise_mac(r.hwsrc)) for _, r in answered]


_HAS_PING = shutil.which("ping") is not None


def _ping(ip: str, timeout: float) -> None:
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), ip]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _nudge(ip: str) -> None:
    """No ping available (e.g. a slim container): send one empty UDP packet instead.
    The OS has to look the address up on the LAN first, which fills the ARP table just the same."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"", (ip, 9))  # port 9 is "discard"
    except OSError:
        pass


def read_arp_table(network: ipaddress.IPv4Network) -> list[Host]:
    hosts: dict[str, Host] = {}
    proc_arp = Path("/proc/net/arp")

    if proc_arp.exists():
        lines = proc_arp.read_text().splitlines()[1:]
        rows = [(cols[0], cols[3]) for cols in (l.split() for l in lines) if len(cols) >= 4]
    else:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True).stdout
        rows = []
        for line in out.splitlines():
            ip_m, mac_m = _IP_RE.search(line), _MAC_RE.search(line)
            if ip_m and mac_m:
                rows.append((ip_m.group(1), mac_m.group(1)))

    for ip, mac in rows:
        try:
            if ipaddress.ip_address(ip) not in network:
                continue
        except ValueError:
            continue
        mac = normalise_mac(mac)
        if is_unicast(mac):
            hosts[ip] = Host(ip=ip, mac=mac)
    return list(hosts.values())


def scan_ping(subnet: str, timeout: float = 1.0) -> list[Host]:
    network = ipaddress.ip_network(subnet, strict=False)
    if _HAS_PING:
        with ThreadPoolExecutor(max_workers=64) as pool:
            list(pool.map(lambda ip: _ping(str(ip), timeout), network.hosts()))
    else:
        for ip in network.hosts():
            _nudge(str(ip))
        time.sleep(timeout + 1.5)  # give the OS time to collect ARP replies
    return read_arp_table(network)


# --------------------------------------------------------------------------- public API

def resolve_hostnames(hosts: list[Host], timeout: float = 3.0) -> None:
    def lookup(h: Host) -> None:
        try:
            h.hostname = socket.gethostbyaddr(h.ip)[0]
        except (OSError, UnicodeError):
            pass

    pool = ThreadPoolExecutor(max_workers=32)
    wait([pool.submit(lookup, h) for h in hosts], timeout=timeout)
    pool.shutdown(wait=False, cancel_futures=True)


def scan(subnet: str | None = None, method: str = "auto") -> tuple[list[Host], str]:
    """Scan the network. Returns (hosts, method_actually_used)."""
    me = network_ready()
    subnet = subnet or default_subnet()
    hosts: list[Host] = []
    used = method

    if method in ("auto", "arp"):
        try:
            hosts = scan_arp(subnet)
            used = "arp"
        except Exception as exc:  # no root / no Npcap / etc.
            if method == "arp":
                raise
            log.info("Raw ARP scan unavailable (%s); falling back to ping sweep", exc)
            method = "ping"

    if method == "ping":
        hosts = scan_ping(subnet)
        used = "ping"

    # This machine never appears in its own ARP table - add it.
    hosts = [h for h in hosts if not ipaddress.ip_address(h.ip).is_loopback]
    if not any(h.ip == me for h in hosts) and ipaddress.ip_address(me) in ipaddress.ip_network(subnet, strict=False):
        hosts.append(Host(ip=me, mac=own_mac(me), hostname=socket.gethostname()))

    resolve_hostnames([h for h in hosts if not h.hostname])
    return hosts, used
