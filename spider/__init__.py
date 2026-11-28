"""SPIder - a Raspberry Pi home network watchtower and honeypot."""

# Bumped by 0.0.1 with every release pushed to GitHub (and tagged vX.Y.Z there).
__version__ = "1.0.7"
__release__ = "Beta"   # "" once SPIder is out of beta


def display_version(version: str = __version__, release: str = __release__) -> str:
    return f"{version} {release}".strip()
