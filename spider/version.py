"""Which version of SPIder is running, and is there a newer one on GitHub?

Every release pushed to GitHub bumps __version__ in spider/__init__.py by 0.0.1, so
the check reads that file on GitHub and compares the numbers. The git commit (from
build.json or .git) and the folder fingerprint are kept to tell builds apart.
"""

import json
import logging
import re
import time
from pathlib import Path

import httpx

from . import __version__, display_version, settings_store
from .buildinfo import from_git, tree_hash

log = logging.getLogger("spider.version")

HERE = Path(__file__).parent
BUILD_FILE = HERE / "build.json"
CHECK_EVERY = 6 * 3600

_cache: dict = {"checked_at": None, "result": None}
_runtime_tree: str | None = None


# --------------------------------------------------------------------------- what's running

def current() -> dict:
    global _runtime_tree
    info: dict = {}
    if BUILD_FILE.exists():
        try:
            info = json.loads(BUILD_FILE.read_text())
        except (OSError, ValueError):
            info = {}
    if not info.get("commit"):
        info = {**from_git(HERE.parent / ".git"), **{k: v for k, v in info.items() if k == "tree"}}
    if not info.get("tree"):
        if _runtime_tree is None:
            _runtime_tree = tree_hash(HERE) or ""
        if _runtime_tree:
            info["tree"] = _runtime_tree
    return {"version": __version__, "label": display_version(), **info}


# --------------------------------------------------------------------------- what's available

def _headers(token: str) -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "SPIder-update-check"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _github_error(r: httpx.Response, token: str) -> str | None:
    if r.status_code == 404:
        return ("GitHub can't see the repository. If it's private, add a read-only GitHub token under Settings, Updates, Advanced."
                if not token else "GitHub couldn't find the repository or this build. Check the repository name and token.")
    if r.status_code in (401, 403):
        return ("GitHub refused the token (expired, or missing Contents: Read-only)." if token
                else "GitHub is limiting requests right now. SPIder will try again later.")
    return None


def _parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def _latest_on_github(repo: str, branch: str, headers: dict, token: str) -> tuple[dict | None, str | None]:
    """Read __version__ and __release__ from spider/__init__.py on the branch."""
    r = httpx.get(f"https://api.github.com/repos/{repo}/contents/spider/__init__.py", params={"ref": branch},
                  headers={**headers, "Accept": "application/vnd.github.raw+json"}, timeout=10)
    if (err := _github_error(r, token)):
        return None, err
    r.raise_for_status()
    text = r.text
    ver = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    rel = re.search(r'^__release__\s*=\s*["\']([^"\']*)["\']', text, re.M)
    if not ver:
        return None, "GitHub's copy of SPIder has no version number."
    return {"version": ver.group(1), "label": display_version(ver.group(1), rel.group(1) if rel else "")}, None


def check(force: bool = False) -> dict:
    """Is a newer version number on GitHub? Cached for 6 hours."""
    now = time.time()
    if not force and _cache["result"] and now - _cache["checked_at"] < CHECK_EVERY:
        return _cache["result"]

    s = settings_store.load()
    branch = current().get("branch") or "main"
    result = {"available": False, "latest": None, "latest_label": None, "error": None,
              "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
    if not s.update_repo:
        result["error"] = "No repository set for update checks."
    else:
        try:
            latest, err = _latest_on_github(s.update_repo, branch, _headers(s.github_token), s.github_token)
            if err:
                result["error"] = err
            else:
                result.update(latest=latest["version"], latest_label=latest["label"],
                              available=_parse(latest["version"]) > _parse(__version__))
        except (httpx.HTTPError, ValueError) as exc:
            result["error"] = f"Couldn't reach GitHub: {exc}"
    _cache.update(checked_at=now, result=result)
    if result["available"]:
        log.info("Update available: SPIder %s (running %s)", result["latest"], __version__)
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
