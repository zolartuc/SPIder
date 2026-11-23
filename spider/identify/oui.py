"""Up-to-date MAC address maker list (Wireshark's 'manuf' file), refreshed weekly.

Handles ordinary 24-bit prefixes and the finer /28 and /36 blocks that big
makers sub-let to small ones (which the old built-in list often gets wrong).
"""

import logging
import os
import time
from pathlib import Path

import httpx

from ..config import get_settings

log = logging.getLogger("spider.oui")

URL = "https://www.wireshark.org/download/automated/data/manuf"
MAX_AGE = 7 * 86400

_table: dict[int, dict[int, str]] | None = None  # prefix bits -> {prefix value: maker}
_loaded_mtime = 0.0


def _path() -> Path:
    return get_settings().data_dir / "manuf.txt"


def _parse(text: str) -> dict[int, dict[int, str]]:
    table: dict[int, dict[int, str]] = {24: {}, 28: {}, 36: {}}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        prefix, short = parts[0].strip(), parts[1].strip()
        name = (parts[2].strip() if len(parts) > 2 and parts[2].strip() else short)
        bits = 24
        if "/" in prefix:
            prefix, b = prefix.split("/", 1)
            bits = int(b)
        hexdigits = prefix.replace(":", "").replace("-", "")
        if bits not in table or len(hexdigits) < bits // 4:
            continue
        table[bits][int(hexdigits[: bits // 4], 16)] = name
    return table


def _load() -> None:
    global _table, _loaded_mtime
    p = _path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return
    if _table is not None and mtime == _loaded_mtime:
        return
    try:
        _table = _parse(p.read_text(encoding="utf-8", errors="replace"))
        _loaded_mtime = mtime
    except OSError as exc:
        log.warning("Couldn't read maker list: %s", exc)


def lookup(mac: str) -> str | None:
    _load()
    if not _table:
        return None
    digits = mac.replace(":", "").replace("-", "").lower()
    for bits in (36, 28, 24):  # most specific first
        hit = _table[bits].get(int(digits[: bits // 4], 16))
        if hit:
            return hit
    return None


def refresh(force: bool = False) -> bool:
    """Download a fresh list if ours is missing or over a week old. Returns True if updated."""
    if get_settings().demo:
        return False
    p = _path()
    try:
        if not force and time.time() - p.stat().st_mtime < MAX_AGE:
            return False
    except OSError:
        pass
    try:
        r = httpx.get(URL, timeout=30, follow_redirects=True, headers={"User-Agent": "SPIder"})
        r.raise_for_status()
        if "\t" not in r.text[:5000]:
            raise ValueError("unexpected file format")
        tmp = p.with_suffix(".tmp")
        tmp.write_text(r.text, encoding="utf-8")
        os.replace(tmp, p)
        log.info("Maker list updated (%d KB)", len(r.content) // 1024)
        return True
    except (httpx.HTTPError, ValueError, OSError) as exc:
        log.warning("Couldn't update the maker list: %s", exc)
        return False
