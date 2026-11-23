"""Read the current git commit from a .git folder, with no dependencies (not even git itself).

Run as a script during the Docker build, before any packages are installed:
    python spider/buildinfo.py /src/.git > spider/build.json
"""

import json
import sys
from pathlib import Path


def from_git(gitdir) -> dict:
    """Resolve HEAD to a commit: handles branches, packed refs and a detached HEAD."""
    g = Path(gitdir)
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


if __name__ == "__main__":
    print(json.dumps(from_git(sys.argv[1] if len(sys.argv) > 1 else ".git")))
