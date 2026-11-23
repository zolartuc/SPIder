"""Which build of SPIder is running, and is there a newer one on GitHub?

The build (git commit) is worked out from the repository's .git folder: at
Docker build time (saved to spider/build.json, since the image has no .git) or,
when running from a checkout, straight from .git. No git program needed.
"""

import json
import logging
import time
from pathlib import Path

import httpx

from . import __version__, settings_store
from .buildinfo import from_git

log = logging.getLogger("spider.version")

HERE = Path(__file__).parent
BUILD_FILE = HERE / "build.json"
CHECK_EVERY = 6 * 3600

_cache: dict = {"checked_at": None, "result": None}


# --------------------------------------------------------------------------- what's running

def current() -> dict:
    info: dict = {}
    if BUILD_FILE.exists():
        try:
            info = json.loads(BUILD_FILE.read_text())
        except (OSError, ValueError):
            info = {}
    if not info.get("commit"):
        info = from_git(HERE.parent / ".git")
    return {"version": __version__, **info}


# --------------------------------------------------------------------------- what's available

def check(force: bool = False) -> dict:
    """Compare the running commit with the tip of the branch on GitHub. Cached for 6 hours."""
    now = time.time()
    if not force and _cache["result"] and now - _cache["checked_at"] < CHECK_EVERY:
        return _cache["result"]

    s = settings_store.load()
    me = current()
    branch = me.get("branch") or "main"
    result = {"available": False, "behind_by": 0, "changes": [], "latest": None, "error": None,
              "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}

    if not me.get("commit"):
        result["error"] = "SPIder can't tell which build it's running, so it can't compare."
    elif not s.update_repo:
        result["error"] = "No repository set for update checks."
    else:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "SPIder-update-check"}
        if s.github_token:
            headers["Authorization"] = f"Bearer {s.github_token}"
        url = f"https://api.github.com/repos/{s.update_repo}/compare/{me['commit']}...{branch}"
        try:
            r = httpx.get(url, headers=headers, timeout=10)
            if r.status_code == 404:
                result["error"] = ("GitHub can't see the repository. If it's private, add a read-only GitHub token "
                                   "in Settings, Updates." if not s.github_token else
                                   "GitHub couldn't find the repository or this build. Check the repository name and token.")
            elif r.status_code in (401, 403):
                result["error"] = "GitHub refused the token (expired, or missing Contents: Read-only)." if s.github_token \
                    else "GitHub is limiting requests right now. SPIder will try again later."
            else:
                r.raise_for_status()
                d = r.json()
                ahead = int(d.get("ahead_by") or 0)
                commits = d.get("commits") or []
                result.update(
                    available=ahead > 0,
                    behind_by=ahead,
                    latest=(commits[-1]["sha"][:7] if commits else me["short"]),
                    changes=[{"short": c["sha"][:7],
                              "title": c["commit"]["message"].splitlines()[0],
                              "date": c["commit"]["author"]["date"]} for c in reversed(commits[-10:])],
                )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            result["error"] = f"Couldn't reach GitHub: {exc}"
    _cache.update(checked_at=now, result=result)
    if result["available"]:
        log.info("Update available: %d newer change(s) on GitHub", result["behind_by"])
    return result


def cached() -> dict | None:
    return _cache["result"]


# --------------------------------------------------------------------------- updating

def trigger_portainer() -> None:
    """Ask Portainer to pull and redeploy this stack via its webhook."""
    url = settings_store.get("portainer_webhook")
    if not url:
        raise RuntimeError("Add your Portainer stack webhook in Settings, Updates first.")
    try:
        r = httpx.post(url, timeout=15, verify=False)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Couldn't reach Portainer: {exc}") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"Portainer refused the update (HTTP {r.status_code}). Check the webhook link.")

