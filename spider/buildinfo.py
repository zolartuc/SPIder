"""Work out which build of SPIder this is, with no dependencies (not even git itself).

Two ways, so it works however the code got here:
  1. Read the git commit from a .git folder (or a .git pointer file).
  2. Fingerprint the spider/ folder exactly the way git does (its "tree hash").
     GitHub can then say which commit has that same folder, even with no .git at all.

Run as a script during the Docker build, before any packages are installed:
    python spider/buildinfo.py /src/.git /src/spider > /build.json
"""

import hashlib
import json
import os
import sys
from pathlib import Path

SKIP_DIRS = {"__pycache__"}
SKIP_FILES = {"build.json"}


def _gitdir(path) -> Path | None:
    g = Path(path)
    if g.is_dir():
        return g
    try:  # worktrees and some tools use a ".git" file: "gitdir: /path/to/real/dir"
        text = g.read_text().strip()
        if text.startswith("gitdir:"):
            real = Path(text.split(":", 1)[1].strip())
            return real if real.is_absolute() else (g.parent / real)
    except OSError:
        pass
    return None


def from_git(gitdir) -> dict:
    """Resolve HEAD to a commit: handles branches, packed refs and a detached HEAD."""
    g = _gitdir(gitdir)
    if g is None:
        return {}
    try:
        head = (g / "HEAD").read_text().strip()
    except OSError:
        return {}
    branch = None
    commit = None
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        branch = ref.rsplit("/", 1)[-1]
        try:
            commit = (g / ref).read_text().strip()
        except OSError:
            try:
                for line in (g / "packed-refs").read_text().splitlines():
                    if line.endswith(" " + ref):
                        commit = line.split(" ", 1)[0]
                        break
            except OSError:
                pass
    else:
        commit = head
    if not commit or len(commit) < 7:
        return {}
    return {"commit": commit, "short": commit[:7], "branch": branch}


def tree_hash(folder) -> str | None:
    """The git tree hash of a folder, computed from the files themselves (same result as `git rev-parse HEAD:spider`)."""
    root = Path(folder)
    if not root.is_dir():
        return None

    def tree(d: Path) -> bytes:
        entries = []
        for child in d.iterdir():
            if child.is_dir():
                if child.name in SKIP_DIRS:
                    continue
                sub = tree(child)
                if sub is None:
                    continue  # git doesn't track empty folders
                entries.append((child.name + "/", b"40000 " + child.name.encode() + b"\0" + sub))
            elif child.is_file():
                if child.name in SKIP_FILES or child.suffix == ".pyc":
                    continue
                data = child.read_bytes()
                blob = hashlib.sha1(b"blob %d\0" % len(data) + data).digest()
                mode = b"100755" if os.access(child, os.X_OK) and sys.platform != "win32" else b"100644"
                entries.append((child.name, mode + b" " + child.name.encode() + b"\0" + blob))
        if not entries:
            return None
        body = b"".join(e for _, e in sorted(entries, key=lambda x: x[0]))
        return hashlib.sha1(b"tree %d\0" % len(body) + body).digest()

    digest = tree(root)
    return digest.hex() if digest else None


if __name__ == "__main__":
    gitdir = sys.argv[1] if len(sys.argv) > 1 else ".git"
    code = sys.argv[2] if len(sys.argv) > 2 else "spider"
    info = from_git(gitdir)
    if (t := tree_hash(code)):
        info["tree"] = t
    print(json.dumps(info))
