"""Start-up check for a newer public release. One small GET of the GitHub 'latest release' record on a
background thread; nothing is downloaded or installed, and nothing but the request itself is sent."""
from __future__ import annotations

import json
import re
import threading
import urllib.request

REPO = "lrizio/MiniVNATiny"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE_URL = f"https://github.com/{REPO}/releases/latest"


def _ver(text: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:4])


def newer(latest: str, current: str) -> bool:
    try:
        return _ver(latest) > _ver(current)
    except ValueError:
        return False


def parse(raw: bytes, current: str):
    """(version, page URL) of the latest release if it is newer than `current`, else None."""
    rec = json.loads(raw)
    tag = str(rec.get("tag_name", ""))
    if rec.get("draft") or rec.get("prerelease") or not newer(tag, current):
        return None
    return tag.lstrip("vV"), str(rec.get("html_url") or PAGE_URL)


def check_async(current: str, callback) -> None:
    """Run the check on a daemon thread; callback(version, url) is called from that thread only if a
    newer release exists. Silent on any failure (offline, rate limited, odd reply)."""
    def run():
        try:
            req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json",
                                                           "User-Agent": f"MiniVNATiny/{current}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                out = parse(r.read(), current)
            if out:
                callback(*out)
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=run, daemon=True).start()
