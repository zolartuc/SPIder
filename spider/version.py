"""Which build of SPIder is running, and is there a newer one on GitHub?

Two ways to know the build, so it works however the code reached the Pi:
  - the git commit, read from .git at Docker build time (saved to spider/build.json)
    or straight from .git when running from a checkout;
  - a fingerprint of the spider/ folder computed exactly the way git does. GitHub is
    then asked which recent commit has that same folder. This needs no .git at all.
"""

import json
import logging
import time
from pathlib import Path

import httpx

from . import __version__, settings_store
from .buildinfo import from_git, tree_hash

log = logging.getLogger("spider.version")

HERE = Path(__file__).parent
BUILD_FILE = HERE / "build.json"
CHECK_EVERY = 6 * 3600
SEARCH_DEPTH = 30  # how far back to look for this build's fingerprint

_cache: dict = {"checked_at": None, "result": None}
_identified: dict | None = None      # commit found by fingerprint
_runtime_tree: str | None = None
_tree_of_root: dict[str, str | None] = {}  # GitHub root tree sha -> spider/ tree sha


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
    if not info.get("commit") and _identified:
        info.update(_identified)
    if not info.get("tree"):
        if _runtime_tree is None:
            _runtime_tree = tree_hash(HERE) or ""
        if _runtime_tree:
            info["tree"] = _runtime_tree
    return {"version": __version__, **info}


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


def _change(c: dict) -> dict:
    return {"short": c["sha"][:7], "title": c["commit"]["message"].splitlines()[0], "date": c["commit"]["author"]["date"]}


def _spider_tree(repo: str, root_sha: str, headers: dict) -> str | None:
    if root_sha not in _tree_of_root:
        r = httpx.get(f"https://api.github.com/repos/{repo}/git/trees/{root_sha}", headers=headers, timeout=10)
        r.raise_for_status()
        _tree_of_root[root_sha] = next((e["sha"] for e in r.json().get("tree", []) if e.get("path") == "spider" and e.get("type") == "tree"), None)
    return _tree_of_root[root_sha]


def _by_commit(me: dict, repo: str, branch: str, headers: dict, token: str, result: dict) -> None:
    r = httpx.get(f"https://api.github.com/repos/{repo}/compare/{me['commit']}...{branch}", headers=headers, timeout=10)
    if (err := _github_error(r, token)):
        result["error"] = err
        return
    r.raise_for_status()
    d = r.json()
    ahead = int(d.get("ahead_by") or 0)
    commits = d.get("commits") or []
    result.update(available=ahead > 0, behind_by=ahead,
                  latest=(commits[-1]["sha"][:7] if commits else me["short"]),
                  changes=[_change(c) for c in reversed(commits[-10:])])


def _by_fingerprint(me: dict, repo: str, branch: str, headers: dict, token: str, result: dict) -> None:
    """No commit known: find the newest recent commit whose spider/ folder matches ours exactly."""
    global _identified
    r = httpx.get(f"https://api.github.com/repos/{repo}/commits", params={"sha": branch, "per_page": SEARCH_DEPTH},
                  headers=headers, timeout=10)
    if (err := _github_error(r, token)):
        result["error"] = err
        return
    r.raise_for_status()
    commits = r.json()
    for i, c in enumerate(commits):
        if _spider_tree(repo, c["commit"]["tree"]["sha"], headers) == me["tree"]:
            _identified = {"commit": c["sha"], "short": c["sha"][:7], "branch": branch, "matched_by": "fingerprint"}
            result.update(available=i > 0, behind_by=i, latest=commits[0]["sha"][:7] if commits else None,
                          changes=[_change(x) for x in commits[:min(i, 10)]])
            return
    # Not among recent builds: either very old or changed locally. Either way, newer code exists.
    result.update(available=True, behind_by=len(commits), latest=commits[0]["sha"][:7] if commits else None,
                  changes=[_change(x) for x in commits[:10]],
                  note=f"This build isn't one of the last {SEARCH_DEPTH} on GitHub, so it's older than those (or was changed by hand).")


def check(force: bool = False) -> dict:
    """Compare what's running with the tip of the branch on GitHub. Cached for 6 hours."""
    now = time.time()
    if not force and _cache["result"] and now - _cache["checked_at"] < CHECK_EVERY:
        return _cache["result"]

    s = settings_store.load()
    me = current()
    branch = me.get("branch") or "main"
    result = {"available": False, "behind_by": 0, "changes": [], "latest": None, "error": None, "note": None,
              "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
    if not s.update_repo:
        result["error"] = "No repository set for update checks."
    elif not (me.get("commit") or me.get("tree")):
        result["error"] = "SPIder can't tell which build it's running, so it can't compare."
    else:
        headers = _headers(s.github_token)
        try:
            if me.get("commit") and me.get("matched_by") != "fingerprint":
                _by_commit(me, s.update_repo, branch, headers, s.github_token, result)
            else:
                _by_fingerprint(me, s.update_repo, branch, headers, s.github_token, result)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            result["error"] = f"Couldn't reach GitHub: {exc}"
    _cache.update(checked_at=now, result=result)
    if result["available"]:
        log.info("Update available: %s newer change(s) on GitHub", result["behind_by"])
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
