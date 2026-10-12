"""Ask YouTube directly why a link isn't playable, and whether the YouTube proxy works.

Streamlink 8.6.1 only reports "Waiting for streams" / "No playable streams": it hides YouTube's
reason, e.g. the datacenter "Sign in to confirm you're not a bot" check. The watch page's
playabilityStatus says exactly why, so the operator sees the real cause and the YouTube-embed
fallback can tell "YouTube is blocking us" apart from "the broadcast hasn't started".
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re

logger = logging.getLogger("youtube_probe")

_STATUS_RE = re.compile(r'"playabilityStatus":\{"status":"([A-Z_]+)"')
_REASON_RE = re.compile(r'"reason":"((?:[^"\\]|\\.)*)"')
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
# Skip the EU cookie-consent interstitial, which has no playabilityStatus
_COOKIES = {"SOCS": "CAI"}
_BOT_MARKERS = ("not a bot", "confirm you")


@dataclass
class Playability:
    status: str | None  # OK, LOGIN_REQUIRED, LIVE_STREAM_OFFLINE, UNPLAYABLE, ERROR; None = unknown
    reason: str | None
    is_live: bool
    blocked: bool  # YouTube's bot check against this server's (or proxy's) IP


def parse_playability(html: str) -> Playability:
    match = _STATUS_RE.search(html)
    if not match:
        return Playability(None, None, False, False)
    status = match.group(1)
    reason = None
    reason_match = _REASON_RE.search(html, match.end(), match.end() + 2000)
    if reason_match:
        try:
            reason = json.loads(f'"{reason_match.group(1)}"')
        except ValueError:
            reason = reason_match.group(1)
    blocked = status == "LOGIN_REQUIRED" and bool(reason) and any(m in reason.lower() for m in _BOT_MARKERS)
    return Playability(status, reason, '"isLive":true' in html, blocked)


def describe(p: Playability) -> str:
    """Operator-facing explanation. Blocked results return YouTube's reason only (the caller prefixes it)."""
    if p.blocked:
        return p.reason or "Sign in to confirm you're not a bot"
    if p.status == "LIVE_STREAM_OFFLINE":
        return f"Broadcast hasn't started: {p.reason}" if p.reason else "Broadcast hasn't started yet"
    if p.status == "OK" and not p.is_live:
        return "This YouTube link is a recording, not a live broadcast"
    if p.status == "OK":
        return "YouTube reports the broadcast is live; waiting for the stream to open"
    if p.status:
        return f"YouTube says {p.status}: {p.reason}" if p.reason else f"YouTube says {p.status}"
    return p.reason or "Could not read YouTube's answer"


def probe_playability(url: str, proxy: str | None = None, timeout: float = 15.0) -> Playability:
    """Fetch the watch page (through the same proxy Streamlink uses) and read playabilityStatus."""
    import requests  # imported lazily: the unit tests don't need it

    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(url, headers=_HEADERS, cookies=_COOKIES, proxies=proxies, timeout=timeout)
    except requests.RequestException as e:
        return Playability(None, f"Could not reach YouTube: {e.__class__.__name__}", False, False)
    if resp.status_code != 200:
        return Playability(None, f"YouTube returned HTTP {resp.status_code}", False, False)
    return parse_playability(resp.text)


def check_proxy(proxy: str, timeout: float = 10.0) -> tuple[bool, str | None]:
    """End-to-end check that YouTube is reachable through the proxy (tiny 204 response)."""
    import requests

    try:
        resp = requests.get(
            "https://www.youtube.com/generate_204",
            headers=_HEADERS,
            proxies={"http": proxy, "https": proxy},
            timeout=timeout,
        )
    except requests.RequestException as e:
        return False, f"{e.__class__.__name__}: {str(e)[:120]}"
    if resp.status_code != 204:
        return False, f"Unexpected HTTP {resp.status_code}"
    return True, None
