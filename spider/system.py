"""Information about the machine SPIder runs on, and applying system settings.

Changing the hostname, timezone or network needs root on the Pi. That only
happens when SPIDER_ALLOW_SYSTEM_CHANGES=true and SPIder runs directly on Linux.
Otherwise SPIder saves the setting and hands back the exact commands to run.
Commands are always built as argument lists (never through a shell), from
values that have already been validated.
"""

import platform
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .config import get_settings
from .settings_store import SYSTEM_KEYS, AppSettings
from .watchtower.scanner import local_ip

_BOOT = time.time()


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(errors="ignore").strip("\x00\n ")
    except OSError:
        return None


DEMO_INFO = {
    "hostname": "spider",
    "ip": "192.168.1.10",
    "model": "Raspberry Pi 4 Model B Rev 1.5",
    "is_pi": True,
    "os": "Debian GNU/Linux 12 (bookworm)",
    "cpu_temp_c": 46.7,
    "uptime_s": 3 * 86400 + 7 * 3600,
    "can_apply": False,
}


def info() -> dict:
    if get_settings().demo:
        return dict(DEMO_INFO)
    model = _read("/proc/device-tree/model")
    temp_raw = _read("/sys/class/thermal/thermal_zone0/temp")
    uptime_raw = _read("/proc/uptime")
    os_name = None
    if release := _read("/etc/os-release"):
        for line in release.splitlines():
            if line.startswith("PRETTY_NAME="):
                os_name = line.split("=", 1)[1].strip('"')
    return {
        "hostname": socket.gethostname(),
        "ip": local_ip(),
        "model": model or f"{platform.system()} computer",
        "is_pi": bool(model and "raspberry pi" in model.lower()),
        "os": os_name or f"{platform.system()} {platform.release()}",
        "cpu_temp_c": round(int(temp_raw) / 1000, 1) if temp_raw and temp_raw.isdigit() else None,
        "uptime_s": int(float(uptime_raw.split()[0])) if uptime_raw else int(time.time() - _BOOT),
        "can_apply": can_apply(),
    }


def can_apply() -> bool:
    return get_settings().allow_system_changes and platform.system() == "Linux"


def _nm_connection(iface: str) -> str | None:
    if not shutil.which("nmcli"):
        return None
    out = subprocess.run(
        ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", iface],
        capture_output=True, text=True, timeout=10,
    )
    name = out.stdout.strip()
    return name or None


def plan(s: AppSettings, changed: set[str]) -> list[list[str]]:
    cmds: list[list[str]] = []
    if "hostname" in changed and s.hostname:
        cmds.append(["hostnamectl", "set-hostname", s.hostname])
        cmds.append(["sed", "-i", rf"s/^127\.0\.1\.1.*/127.0.1.1\t{s.hostname}/", "/etc/hosts"])
    if "timezone" in changed and s.timezone:
        cmds.append(["timedatectl", "set-timezone", s.timezone])
    if changed & {"lan_interface", "lan_mode", "lan_address", "lan_gateway", "lan_dns"}:
        con = (_nm_connection(s.lan_interface) if platform.system() == "Linux" else None) or f"<connection for {s.lan_interface}>"
        if s.lan_mode == "static":
            cmds.append([
                "nmcli", "connection", "modify", con,
                "ipv4.method", "manual",
                "ipv4.addresses", s.lan_address,
                "ipv4.gateway", s.lan_gateway,
                "ipv4.dns", s.lan_dns.replace(", ", " "),
            ])
        else:
            cmds.append([
                "nmcli", "connection", "modify", con,
                "ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", "",
            ])
        cmds.append(["nmcli", "connection", "up", con])
    return cmds


def apply(s: AppSettings, changed: set[str]) -> dict | None:
    if not (changed & SYSTEM_KEYS):
        return None
    cmds = plan(s, changed)
    shown = ["sudo " + shlex.join(c) for c in cmds]
    if not cmds:
        return None
    if not can_apply():
        return {"executed": False, "commands": shown}

    results = []
    for c in cmds:
        try:
            r = subprocess.run(c, capture_output=True, text=True, timeout=30)
            results.append({"command": shlex.join(c), "ok": r.returncode == 0, "output": (r.stderr or r.stdout).strip()[:500]})
            if r.returncode != 0:
                break
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append({"command": shlex.join(c), "ok": False, "output": str(exc)})
            break
    return {"executed": True, "commands": shown, "results": results}
