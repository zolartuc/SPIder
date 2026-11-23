"""Passive listeners: SPIder only hears what devices broadcast anyway. Nothing is sent.

- DHCP: when a device joins the network (or re-joins), it broadcasts a request that
  usually includes its own name (option 12) and often what it runs (option 60, e.g.
  "android-dhcp-14", "MSFT 5.0", "dhcpcd-9.4.1"), plus a parameter list that hints at the OS.
  Renewals are usually sent straight to the router, so names arrive as devices rejoin.
- Bambu Lab printers announce themselves every few seconds on UDP port 2021 with their
  model code and the name you gave the printer.
"""

import logging
import re
import socket
import threading

from . import facts

log = logging.getLogger("spider.listen")

BAMBU_MODELS = {
    "BL-P001": "X1 Carbon", "BL-P002": "X1", "C13": "X1E", "C11": "P1P", "C12": "P1S",
    "N1": "A1 mini", "N2S": "A1", "O1D": "H2D", "O1S": "H2S", "N6": "P2S",
}

_started = False


def _dhcp_packet(pkt) -> None:
    try:
        from scapy.layers.dhcp import BOOTP, DHCP

        if not pkt.haslayer(DHCP) or pkt[BOOTP].op != 1:  # requests from clients only
            return
        mac = ":".join(f"{b:02x}" for b in bytes(pkt[BOOTP].chaddr)[:6])
        found = {}
        for opt in pkt[DHCP].options:
            if not isinstance(opt, tuple) or len(opt) < 2:
                continue
            name, value = opt[0], opt[1]
            if name == "hostname":
                found["hostname"] = value.decode(errors="replace").strip() if isinstance(value, bytes) else str(value)
            elif name == "vendor_class_id":
                found["vendor_class"] = value.decode(errors="replace").strip() if isinstance(value, bytes) else str(value)
            elif name == "param_req_list":
                found["params"] = ",".join(str(x) for x in (value if isinstance(value, (list, tuple)) else [value]))
        if found:
            facts.add_many(mac, "dhcp", found)
            log.info("DHCP from %s: %s", mac, found.get("hostname") or found.get("vendor_class"))
    except Exception:
        log.debug("Couldn't read a DHCP packet", exc_info=True)


def _start_dhcp() -> None:
    try:
        from scapy.all import AsyncSniffer, conf

        conf.verb = 0
        AsyncSniffer(filter="udp and (port 67 or port 68)", prn=_dhcp_packet, store=False).start()
        log.info("Listening for DHCP names")
    except Exception as exc:
        log.warning("Can't listen for DHCP names (%s). Other identification still works.", exc)


def _bambu_loop() -> None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", 2021))
    except OSError as exc:
        log.info("Not listening for Bambu printers: %s", exc)
        return
    log.info("Listening for Bambu Lab printers")
    while True:
        try:
            data, (ip, _) = s.recvfrom(4096)
            text = data.decode(errors="replace")
            if "bambu" not in text.lower():
                continue
            head = dict(re.findall(r"^([\w.\-]+):\s*(.+?)\s*$", text, flags=re.MULTILINE))
            model = head.get("DevModel.bambu.com", "")
            mac = facts.mac_for_ip(head.get("Location", ip).strip()) or facts.mac_for_ip(ip)
            if mac:
                facts.add_many(mac, "bambu", {
                    "model": BAMBU_MODELS.get(model, model),
                    "name": head.get("DevName.bambu.com", ""),
                    "connect": head.get("DevConnect.bambu.com", ""),
                })
        except Exception:
            log.debug("Bad Bambu packet", exc_info=True)


def start() -> None:
    """Start the listeners once (each in the background)."""
    global _started
    if _started:
        return
    _started = True
    _start_dhcp()
    threading.Thread(target=_bambu_loop, name="bambu-listen", daemon=True).start()
