"""SPIder updates itself from GitHub, no Portainer needed.

1. Ask GitHub for the newest commit on the branch, and the fingerprint (git tree hash)
   its spider/ folder should have.
2. Download that exact commit and fingerprint the download. Any mismatch: stop.
3. Test-load the new code in a separate Python process. If it won't import: stop.
4. Install new libraries if requirements.txt changed.
5. Swap the folders (the old one is kept as spider.previous) and restart.
   Docker's restart policy brings SPIder straight back on the new code.

Only runs inside Docker (where something restarts us). Data lives in the volume
and is never touched. Changes to the Dockerfile itself still need a redeploy.
"""

import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

import httpx

from . import settings_store
from .buildinfo import tree_hash
from .config import get_settings

log = logging.getLogger("spider.update")

PKG = Path(__file__).parent          # /app/spider
APP = PKG.parent                      # /app
PREVIOUS = APP / "spider.previous"

state: dict = {"state": "idle", "message": None, "started": None}
_lock = threading.Lock()


def possible() -> tuple[bool, str | None]:
    """Can SPIder update itself here? (reason if not)"""
    if get_settings().demo:
        return False, "Updates are switched off in demo mode."
    if not Path("/.dockerenv").exists():
        return False, "SPIder isn't running in Docker here, so update it the way you installed it."
    if not os.access(APP, os.W_OK):
        return False, "SPIder can't write to its own folder."
    return True, None


def _github(path: str, token: str, **kw) -> httpx.Response:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "SPIder-update"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.get(f"https://api.github.com{path}", headers=headers, timeout=kw.pop("timeout", 20),
                     follow_redirects=True, **kw)


def _set(st: str, message: str | None = None) -> None:
    state.update(state=st, message=message)
    if message:
        (log.warning if st == "failed" else log.info)("Self-update: %s", message)


def _run(repo: str, branch: str, token: str) -> None:
    # 1. what we're updating to
    _set("checking", "Asking GitHub for the latest build")
    r = _github(f"/repos/{repo}/commits/{branch}", token)
    r.raise_for_status()
    head = r.json()
    sha, root_tree = head["sha"], head["commit"]["tree"]["sha"]
    t = _github(f"/repos/{repo}/git/trees/{root_tree}", token)
    t.raise_for_status()
    expected = next((e["sha"] for e in t.json().get("tree", []) if e.get("path") == "spider" and e.get("type") == "tree"), None)
    if not expected:
        raise RuntimeError("GitHub's latest build has no spider folder.")

    # 2. download and verify
    _set("downloading", f"Downloading build {sha[:7]}")
    tar = _github(f"/repos/{repo}/tarball/{sha}", token, timeout=120)
    tar.raise_for_status()
    work = Path(tempfile.mkdtemp(prefix="spider-update-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(tar.content), mode="r:gz") as tf:
            tf.extractall(work, filter="data")  # refuses absolute paths, links out, devices
        tops = [p for p in work.iterdir() if p.is_dir()]
        if len(tops) != 1:
            raise RuntimeError("The download didn't look like SPIder.")
        top = tops[0]
        new_pkg = top / "spider"
        got = tree_hash(new_pkg)
        if got != expected:
            raise RuntimeError(f"The download didn't match GitHub's fingerprint ({(got or 'none')[:10]} vs {expected[:10]}), so nothing was changed.")

        # 3. will it start?
        _set("testing", "Checking the new code loads")
        test = subprocess.run([sys.executable, "-c", "import spider.main"], cwd=top, capture_output=True, text=True,
                              timeout=120, env={**os.environ, "SPIDER_DATA_DIR": tempfile.mkdtemp()})
        if test.returncode != 0 and "ModuleNotFoundError" not in test.stderr:
            raise RuntimeError("The new code wouldn't start, so nothing was changed: " + test.stderr.strip().splitlines()[-1][:200])

        # 4. libraries
        new_req, old_req = top / "requirements.txt", APP / "requirements.txt"
        if new_req.exists() and (not old_req.exists() or new_req.read_bytes() != old_req.read_bytes()):
            _set("installing", "Installing new libraries")
            pip = subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", str(new_req)],
                                 capture_output=True, text=True, timeout=900)
            if pip.returncode != 0:
                raise RuntimeError("Couldn't install the new libraries, so nothing was changed: " + pip.stderr.strip()[-200:])
            if test.returncode != 0:  # retry the load test now the libraries are there
                test = subprocess.run([sys.executable, "-c", "import spider.main"], cwd=top, capture_output=True, text=True,
                                      timeout=120, env={**os.environ, "SPIDER_DATA_DIR": tempfile.mkdtemp()})
                if test.returncode != 0:
                    raise RuntimeError("The new code wouldn't start, so nothing was changed.")
            shutil.copy2(new_req, old_req)
        elif test.returncode != 0:
            raise RuntimeError("The new code wouldn't start, so nothing was changed.")

        # 5. swap and restart
        _set("installing", f"Switching to build {sha[:7]}")
        (new_pkg / "build.json").write_text(json.dumps({"commit": sha, "short": sha[:7], "branch": branch, "tree": expected}))
        shutil.rmtree(PREVIOUS, ignore_errors=True)
        os.rename(PKG, PREVIOUS)
        try:
            shutil.move(str(new_pkg), str(PKG))
        except Exception:
            os.rename(PREVIOUS, PKG)  # put things back exactly as they were
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    _set("restarting", f"Restarting on build {sha[:7]}")
    threading.Timer(1.5, lambda: os._exit(0)).start()  # Docker restarts us (restart: unless-stopped)


def start() -> None:
    ok, why = possible()
    if not ok:
        raise RuntimeError(why)
    if not _lock.acquire(blocking=False):
        raise RuntimeError("An update is already running.")
    s = settings_store.load()
    from .version import current
    branch = current().get("branch") or "main"
    state.update(started=time.time())

    def go():
        try:
            _run(s.update_repo, branch, s.github_token)
        except Exception as exc:
            _set("failed", str(exc) or type(exc).__name__)
        finally:
            _lock.release()

    threading.Thread(target=go, name="self-update", daemon=True).start()
