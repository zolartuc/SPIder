"""Regenerate the README screenshots from the demo network.

    pip install -r requirements-dev.txt
    python scripts/screenshots.py

Starts a throwaway SPIder in demo mode (made-up devices, no real scanning) plus a
fake Pi-hole, then drives Microsoft Edge or Chrome (whichever is installed) to
capture each page. Images are written to docs/screenshots/.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
PORT, PIHOLE_PORT = 8091, 8098
BASE = f"http://127.0.0.1:{PORT}"


def wait_for_port(port: int, timeout: float = 20) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise SystemExit(f"Nothing came up on port {port}")


def launch_browser(p):
    for channel in ("msedge", "chrome", None):
        try:
            return p.chromium.launch(channel=channel) if channel else p.chromium.launch()
        except Exception:
            continue
    raise SystemExit("No browser found. Install Edge or Chrome, or run: playwright install chromium")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix="spider-demo-"))
    py = sys.executable

    subprocess.run([py, str(ROOT / "scripts" / "demo" / "seed.py"), str(data)], check=True,
                   env=os.environ | {"SPIDER_DEMO_PIHOLE_URL": f"http://127.0.0.1:{PIHOLE_PORT}"})
    env = os.environ | {"SPIDER_DATA_DIR": str(data), "SPIDER_DEMO": "true",
                        "SPIDER_HOST": "127.0.0.1", "SPIDER_PORT": str(PORT)}
    procs = [
        subprocess.Popen([py, str(ROOT / "scripts" / "demo" / "fake_pihole.py"), str(PIHOLE_PORT)]),
        subprocess.Popen([py, "-m", "spider"], cwd=ROOT, env=env),
    ]
    try:
        wait_for_port(PIHOLE_PORT)
        wait_for_port(PORT)
        with sync_playwright() as p:
            browser = launch_browser(p)
            shoot(browser)
            browser.close()
    finally:
        for proc in procs:
            proc.terminate()
    print(f"Screenshots written to {OUT}")


def signed_in(browser, theme: str, **ctx):
    ctx.setdefault("viewport", {"width": 1440, "height": 900})
    ctx.setdefault("device_scale_factor", 1.5)
    context = browser.new_context(color_scheme="dark", reduced_motion="reduce", **ctx)
    context.request.post(f"{BASE}/api/login", data={"username": "demo", "password": "demo-password"})
    context.request.put(f"{BASE}/api/me/theme", data={"theme": theme})
    return context


def snap(page, path: str, name: str, settle: float = 1.2, before=None) -> None:
    page.goto(BASE + path, wait_until="networkidle")
    page.add_style_tag(content=".demo-badge, .demo-hint { display: none !important; }")
    page.wait_for_timeout(int(settle * 1000))
    if before:
        before(page)
        page.wait_for_timeout(400)
    page.screenshot(path=OUT / f"{name}.png")
    print("  ", name)


def shoot(browser) -> None:
    # Bridge: every page.
    ctx = signed_in(browser, "bridge")
    page = ctx.new_page()
    snap(page, "/", "bridge-overview")
    snap(page, "/devices", "bridge-devices")
    snap(page, "/activity", "bridge-activity")
    snap(page, "/settings", "bridge-settings")
    snap(page, "/settings", "bridge-settings-network",
         before=lambda pg: pg.evaluate("document.getElementById('network').scrollIntoView({block: 'start'})"))
    snap(page, "/devices", "bridge-user-menu",
         before=lambda pg: pg.click(".user-menu summary"))
    ctx.close()

    # Pi-hole page is long, and only <main> scrolls, so give it a taller window.
    ctx = signed_in(browser, "bridge", viewport={"width": 1440, "height": 1320})
    snap(ctx.new_page(), "/pihole", "bridge-pihole", settle=1.6)
    ctx.close()

    # Traffic: the network view, then the unidentified smart plug that started calling a Chinese cloud server.
    ctx = signed_in(browser, "bridge", viewport={"width": 1440, "height": 1320})
    page = ctx.new_page()
    snap(page, "/traffic", "bridge-traffic", settle=1.5)
    snap(page, "/traffic#device=68%3A57%3A2d%3A91%3Aaa%3A03", "bridge-traffic-device", settle=1.8)
    ctx.close()

    # Bridge on a phone.
    ctx = signed_in(browser, "bridge", viewport={"width": 390, "height": 844}, device_scale_factor=2,
                    is_mobile=True, has_touch=True)
    snap(ctx.new_page(), "/", "bridge-mobile")
    ctx.close()

    # Sign-in page, in Bridge.
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5, color_scheme="dark")
    ctx.add_init_script("try { localStorage.setItem('spider-theme', 'bridge'); } catch (e) {}")
    snap(ctx.new_page(), "/login", "bridge-sign-in", settle=0.6)
    ctx.close()

    # The other themes, overview only.
    for theme in ("silk", "cyanotype", "amber"):
        ctx = signed_in(browser, theme)
        snap(ctx.new_page(), "/", f"{theme}-overview")
        ctx.close()


if __name__ == "__main__":
    main()
