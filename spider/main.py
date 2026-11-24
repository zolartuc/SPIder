import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError

from . import __version__, alerts, auth, blocking, selfupdate, settings_store, system, version
from .config import get_settings
from .db import connect, init_db
from .pihole import client as pihole
from .watchtower import scanner
from .identify import listen as identify_listen
from .identify import run as identify
from .traffic import collector as traffic_collector
from .traffic import views as traffic_views
from .watchtower import service as watchtower

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("spider")

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


def asset(path: str) -> str:
    """/static URL with a version stamp from the file's modified time, so browsers never run stale code."""
    try:
        stamp = int((HERE / "static" / path).stat().st_mtime)
    except OSError:
        stamp = 0
    return f"/static/{path}?v={stamp}"


templates.env.globals["asset"] = asset
_scan_lock = asyncio.Lock()
THEMES = ("silk", "bridge", "cyanotype", "amber")

NAV = [
    ("overview", "/", "Overview"),
    ("devices", "/devices", "Devices"),
    ("activity", "/activity", "Activity"),
    ("traffic", "/traffic", "Traffic"),
    ("pihole", "/pihole", "Pi-hole"),
    ("honeypot", "/honeypot", "Honeypot"),
]


async def scan_now() -> dict:
    async with _scan_lock:
        return await asyncio.to_thread(watchtower.run_scan)


async def scan_loop() -> None:
    while True:
        try:
            await scan_now()
        except Exception:
            log.exception("Background scan crashed")
        await asyncio.sleep(settings_store.get("scan_interval"))


async def identify_loop() -> None:
    await asyncio.sleep(15)  # let the first scan find devices
    if not get_settings().demo:
        identify_listen.start()
    while True:
        try:
            await asyncio.to_thread(identify.run)
        except Exception:
            log.exception("Identification crashed")
        await asyncio.sleep(300)


async def update_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await asyncio.to_thread(version.check)
        except Exception:
            log.exception("Update check crashed")
        await asyncio.sleep(version.CHECK_EVERY)


async def blocks_loop() -> None:
    while True:
        await asyncio.sleep(5)
        try:
            await asyncio.to_thread(blocking.expire)
        except Exception:
            log.exception("Pause expiry crashed")


async def traffic_loop() -> None:
    await asyncio.sleep(3)  # let the first device scan go first, so lookups map to known devices
    while True:
        try:
            await asyncio.to_thread(traffic_collector.run)
        except Exception:
            log.exception("Traffic collector crashed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    tasks = [asyncio.create_task(scan_loop()), asyncio.create_task(traffic_loop()), asyncio.create_task(blocks_loop()),
             asyncio.create_task(update_loop()), asyncio.create_task(identify_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="SPIder", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.exception_handler(RequestValidationError)
async def friendly_validation(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    return JSONResponse({"detail": first.get("msg", "That request wasn't valid.")}, status_code=422)


# --------------------------------------------------------------------------- access helpers

def viewer(request: Request) -> dict | None:
    """Anyone may read if guest viewing is on; otherwise you must be signed in."""
    user = auth.current_user(request)
    if not user and not settings_store.get("guest_view"):
        raise HTTPException(401, "Sign in to see this.")
    return user


def safe_next(target: str | None) -> str:
    return target if target and target.startswith("/") and not target.startswith("//") else "/"


def page(request: Request, name: str, title: str, template: str):
    if not auth.has_users():
        return RedirectResponse("/setup", status_code=303)
    user = auth.current_user(request)
    if not user and not settings_store.get("guest_view"):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    return templates.TemplateResponse(request, template, {
        "page": name, "title": title, "user": user, "nav": NAV, "version": __version__,
        "theme": (user or {}).get("theme"), "layout": (user or {}).get("layout"),
        "device_name": settings_store.get("device_name"),
        "demo": get_settings().demo, "build": version.current(),
    })


# --------------------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def overview_page(request: Request):
    return page(request, "overview", "Your network", "overview.html")


@app.get("/devices", response_class=HTMLResponse)
def devices_page(request: Request):
    return page(request, "devices", "Devices", "devices.html")


@app.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request):
    return page(request, "activity", "Activity", "activity.html")


@app.get("/pihole", response_class=HTMLResponse)
def pihole_page(request: Request):
    return page(request, "pihole", "Pi-hole", "pihole.html")


@app.get("/traffic", response_class=HTMLResponse)
def traffic_page(request: Request):
    return page(request, "traffic", "Traffic", "traffic.html")


@app.get("/honeypot", response_class=HTMLResponse)
def honeypot_page(request: Request):
    return page(request, "honeypot", "Honeypot", "honeypot.html")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return page(request, "settings", "Settings", "settings.html")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None):
    if not auth.has_users():
        return RedirectResponse("/setup", status_code=303)
    if auth.current_user(request):
        return RedirectResponse(safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"mode": "login", "next": safe_next(next), "demo": get_settings().demo, "build": version.current()})


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if auth.has_users():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"mode": "setup", "next": "/settings"})


# --------------------------------------------------------------------------- auth API

class Credentials(BaseModel):
    username: str
    password: str


@app.post("/api/setup")
def api_setup(body: Credentials):
    if auth.has_users():
        raise HTTPException(409, "SPIder already has an admin.")
    user = auth.create_user(body.username.strip(), body.password, "admin")
    resp = JSONResponse({"user": user})
    auth.set_cookie(resp, auth.create_session(user["id"]))
    return resp


@app.post("/api/login")
def api_login(body: Credentials, request: Request):
    ip = request.client.host if request.client else "?"
    user = auth.authenticate(body.username.strip(), body.password, ip)
    resp = JSONResponse({"user": user})
    auth.set_cookie(resp, auth.create_session(user["id"]))
    return resp


@app.post("/api/logout")
def api_logout(request: Request):
    auth.delete_session(request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/me")
def api_me(request: Request):
    return {"user": auth.current_user(request)}


class ThemeBody(BaseModel):
    theme: Literal["silk", "bridge", "cyanotype", "amber"]


class LayoutBody(BaseModel):
    layout: Literal["full", "boxed"]


@app.put("/api/me/layout")
def api_my_layout(body: LayoutBody, user=Depends(auth.require_user)):
    auth.set_layout(user["id"], body.layout)
    return {"ok": True}


@app.put("/api/me/theme")
def api_my_theme(body: ThemeBody, user=Depends(auth.require_user)):
    auth.set_theme(user["id"], body.theme)
    return {"ok": True}


class PasswordChange(BaseModel):
    current: str
    new: str


@app.put("/api/me/password")
def api_my_password(body: PasswordChange, user=Depends(auth.require_user)):
    auth.change_password(user["id"], body.current, body.new)
    resp = JSONResponse({"ok": True})
    auth.set_cookie(resp, auth.create_session(user["id"]))  # keep this browser signed in
    return resp


class NewUser(Credentials):
    role: Literal["admin", "viewer"] = "viewer"


@app.get("/api/users")
def api_users(_=Depends(auth.require_admin)):
    return auth.list_users()


@app.post("/api/users")
def api_add_user(body: NewUser, _=Depends(auth.require_admin)):
    return auth.create_user(body.username.strip(), body.password, body.role)


@app.delete("/api/users/{user_id}")
def api_delete_user(user_id: int, admin=Depends(auth.require_admin)):
    auth.delete_user(user_id, admin)
    return {"ok": True}


# --------------------------------------------------------------------------- settings API

@app.get("/api/settings")
def api_settings(request: Request, user=Depends(viewer)):
    return {
        "values": settings_store.public(),
        "editable": bool(user and user["role"] == "admin"),
        "detected_subnet": scanner.default_subnet(),
        "timezones": settings_store.timezones(),
        "system": system.info(),
    }


@app.put("/api/settings")
def api_update_settings(changes: dict, _=Depends(auth.require_admin)):
    try:
        new, changed = settings_store.update(changes)
    except ValidationError as exc:
        err = exc.errors()[0]
        field = err["loc"][0] if err.get("loc") else None
        msg = err["msg"].removeprefix("Value error, ")
        return JSONResponse({"detail": msg, "field": field}, status_code=422)
    if changed & {"pihole_url", "pihole_password"}:
        pihole.reset()
    return {"changed": sorted(changed), "values": settings_store.public(), "apply": system.apply(new, changed)}


@app.get("/api/vitals")
def api_vitals(_=Depends(viewer)):
    """Status panel: is SPIder watching, plus the Pi's temperature, load, memory and storage."""
    from datetime import datetime, timezone
    last = watchtower.last_scan
    interval = settings_store.get("scan_interval")
    if last.get("error"):
        status, text = "bad", "Scan failed"
    elif not last.get("at"):
        status, text = "wait", "Starting"
    else:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last["at"])).total_seconds()
        status, text = ("ok", "Watching") if age < interval * 2 + 120 else ("bad", "Scans stopped")
    v = system.vitals()
    t = v.get("temp_c")
    v["temp_level"] = None if t is None else "hot" if t >= 75 else "warm" if t >= 60 else "ok"
    return {"status": status, "status_text": text, **v}


@app.get("/api/system")
def api_system(_=Depends(viewer)):
    return system.info()


@app.post("/api/alerts/test")
def api_test_alert(_=Depends(auth.require_admin)):
    if not settings_store.get("ntfy_topic"):
        raise HTTPException(400, "Add an ntfy topic first, then save.")
    if not alerts.send("SPIder test alert", "If you can read this, alerts are working.", tags="white_check_mark"):
        raise HTTPException(502, "ntfy didn't accept the alert. Check the server address and topic.")
    return {"ok": True}


# --------------------------------------------------------------------------- network API

@app.get("/api/status")
def status(_=Depends(viewer)):
    return {"version": __version__, "last_scan": watchtower.last_scan, "scanning": _scan_lock.locked()}


@app.post("/api/scan")
async def trigger_scan(_=Depends(auth.require_user)):
    if _scan_lock.locked():
        return {"queued": False, "detail": "scan already running"}
    asyncio.create_task(scan_now())
    return {"queued": True}


@app.get("/api/devices")
def devices(_=Depends(viewer)):
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM devices").fetchall()]

    def ip_key(ip: str | None):
        try:
            return tuple(int(p) for p in (ip or "").split("."))
        except ValueError:
            return (999,)

    rows.sort(key=lambda d: (-d["online"], d["known"], ip_key(d["ip"])))
    blocks = blocking.current()
    for d in rows:
        d["block"] = blocks.get(d["mac"])
        try:
            d["identity"] = json.loads(d.get("identity") or "[]")
        except ValueError:
            d["identity"] = []
        d["protected"] = blocking.protected_reason(d["ip"])
    return rows


class DeviceUpdate(BaseModel):
    name: str | None = None
    known: bool | None = None
    kind: str | None = None  # an admin's correction of what the device is; "" clears it


@app.patch("/api/devices/{mac}")
def update_device(mac: str, body: DeviceUpdate, _=Depends(auth.require_admin)):
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM devices WHERE mac = ?", (mac,)).fetchone():
            raise HTTPException(404, "Unknown device")
        if body.name is not None:
            conn.execute("UPDATE devices SET name = ? WHERE mac = ?", ((body.name.strip() or None) and body.name.strip()[:40], mac))
        if body.known is not None:
            conn.execute("UPDATE devices SET known = ? WHERE mac = ?", (int(body.known), mac))
        if body.kind is not None:
            from .identify.classify import KINDS
            if body.kind and body.kind not in KINDS:
                raise HTTPException(422, "Pick a type from the list.")
            conn.execute("UPDATE devices SET kind_user = ? WHERE mac = ?", (body.kind or None, mac))
    return {"ok": True}


@app.get("/api/blocks")
async def blocks_status(_=Depends(viewer)):
    info = await asyncio.to_thread(blocking.availability)
    return {**info, "blocks": blocking.current()}


class BlockBody(BaseModel):
    minutes: int | None = None  # None (and no seconds) = until someone unblocks
    seconds: int | None = None  # for short test pauses


@app.put("/api/devices/{mac}/block")
async def block_device(mac: str, body: BlockBody, admin=Depends(auth.require_admin)):
    total = body.seconds if body.seconds is not None else (body.minutes * 60 if body.minutes is not None else None)
    if total is not None and not 30 <= total <= 7 * 24 * 3600:
        raise HTTPException(422, "Pauses run from 30 seconds to 7 days.")
    return await asyncio.to_thread(blocking.block, mac, total, admin["username"])


@app.delete("/api/devices/{mac}/block")
async def unblock_device(mac: str, admin=Depends(auth.require_admin)):
    await asyncio.to_thread(blocking.unblock, mac, admin["username"])
    return {"ok": True}


@app.get("/api/version")
def version_info(_=Depends(viewer)):
    can_self, why = selfupdate.possible()
    return {**version.current(), "update": version.cached(), "updating": selfupdate.state,
            "can_update": can_self or bool(settings_store.get("portainer_webhook")),
            "update_method": "self" if can_self else ("portainer" if settings_store.get("portainer_webhook") else None),
            "cant_update_because": None if can_self else why}


@app.post("/api/version/check")
async def version_check(_=Depends(auth.require_admin)):
    return await asyncio.to_thread(version.check, True)


@app.post("/api/version/update")
async def version_update(admin=Depends(auth.require_admin)):
    can_self, why = selfupdate.possible()
    try:
        if can_self:
            selfupdate.start()
            log.info("Self-update started by %s", admin["username"])
            return {"ok": True, "method": "self"}
        await asyncio.to_thread(version.trigger_portainer)
    except RuntimeError as exc:
        raise HTTPException(409 if can_self else 502, str(exc))
    log.info("Update requested by %s through Portainer", admin["username"])
    return {"ok": True, "method": "portainer"}


@app.post("/api/devices/{mac}/identify")
async def identify_device(mac: str, _=Depends(auth.require_admin)):
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM devices WHERE mac = ?", (mac,)).fetchone():
            raise HTTPException(404, "Unknown device")
    d = await asyncio.to_thread(identify.identify_now, mac)
    return {"kind": d["kind"], "model": d["model"], "confidence": d["confidence"]}


@app.get("/api/kinds")
def kinds(_=Depends(viewer)):
    from .identify.classify import KINDS
    return KINDS


@app.get("/api/events")
def events(limit: int = 50, _=Depends(viewer)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.*, d.name, d.vendor FROM events e LEFT JOIN devices d ON d.mac = e.mac "
            "ORDER BY e.ts DESC, e.id DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/pihole")
async def pihole_summary(_=Depends(viewer)):
    return await asyncio.to_thread(pihole.summary)


@app.get("/api/pihole/details")
async def pihole_details(_=Depends(viewer)):
    data = await asyncio.to_thread(pihole.details)
    if data.get("status") == "ok" and data.get("top_clients"):
        # Put SPIder's own names on Pi-hole's clients, so "192.168.1.21" reads "Living room TV".
        with connect() as conn:
            known = {r["ip"]: dict(r) for r in conn.execute("SELECT ip, mac, name, hostname, vendor FROM devices")}
        for c in data["top_clients"]:
            d = known.get(c.get("ip"))
            if d:
                c["device"] = {"mac": d["mac"], "name": d["name"] or d["hostname"], "vendor": d["vendor"]}
    return data


@app.get("/api/traffic")
async def traffic_overview(_=Depends(viewer)):
    return await asyncio.to_thread(traffic_views.overview)


@app.get("/api/traffic/device/{key}")
async def traffic_device(key: str, _=Depends(viewer)):
    data = await asyncio.to_thread(traffic_views.device, key)
    if data is None:
        raise HTTPException(404, "SPIder hasn't seen any traffic from that device.")
    return data


@app.get("/api/flags")
async def traffic_flags(_=Depends(viewer)):
    return await asyncio.to_thread(traffic_views.flags)


@app.post("/api/flags/{key}/ack")
async def traffic_flag_ack(key: str, _=Depends(auth.require_user)):
    return {"cleared": await asyncio.to_thread(traffic_views.acknowledge, key)}


def run() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run("spider.main:app", host=s.host, port=s.port)


if __name__ == "__main__":
    run()
