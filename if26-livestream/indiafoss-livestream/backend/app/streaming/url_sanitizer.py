"""YouTube URL parser, validator, and canonicalizer for IndiaFOSS 2026 livestream backend.

Strips all tracking/query parameters (pp, si, feature, etc.), fragments, user credentials,
and reconstructs a clean, safe canonical URL: https://www.youtube.com/watch?v=VIDEO_ID
"""

import re
from urllib.parse import parse_qs, urlparse

# Standard YouTube 11-character base64-style video ID regex
YOUTUBE_VIDEO_ID_REGEX = re.compile(r"^[0-9A-Za-z_-]{11}$")

# Explicitly allowed YouTube hostnames
ALLOWED_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


class InvalidYouTubeURLError(ValueError):
    """Raised when an input URL is not a valid, supported YouTube video/live URL."""


def extract_youtube_video_id(raw_url: str) -> str:
    """Extract and validate the 11-character YouTube video ID from a URL.

    Supported patterns:
    - https://www.youtube.com/watch?v=VIDEO_ID (with any query params like &pp=, &si=)
    - https://youtube.com/watch?v=VIDEO_ID
    - https://www.youtube.com/live/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID
    - https://youtu.be/VIDEO_ID
    """
    if not raw_url or not isinstance(raw_url, str):
        raise InvalidYouTubeURLError("URL must be a non-empty string.")

    cleaned_url = raw_url.strip()
    if not cleaned_url:
        raise InvalidYouTubeURLError("URL cannot be whitespace.")

    # Prefix with scheme if missing
    if not cleaned_url.startswith(("http://", "https://")):
        cleaned_url = "https://" + cleaned_url

    try:
        parsed = urlparse(cleaned_url)
    except Exception as exc:
        raise InvalidYouTubeURLError(f"Malformed URL structure: {exc}") from exc

    # Reject unexpected schemes
    if parsed.scheme not in ("http", "https"):
        raise InvalidYouTubeURLError(f"Unsupported URL scheme: {parsed.scheme}")

    # Reject user credentials in URL (e.g. https://user:pass@youtube.com)
    if parsed.username or parsed.password:
        raise InvalidYouTubeURLError("User credentials in URL are strictly forbidden.")

    # Reject custom ports
    if parsed.port and parsed.port not in (80, 443):
        raise InvalidYouTubeURLError(f"Unexpected URL port: {parsed.port}")

    hostname = (parsed.hostname or "").lower()
    if hostname not in ALLOWED_YOUTUBE_HOSTS:
        raise InvalidYouTubeURLError(
            f"Host '{hostname}' is not an authorized YouTube domain."
        )

    video_id: str | None = None

    # Handle short URL domain: youtu.be/VIDEO_ID
    if "youtu.be" in hostname:
        path_parts = [p for p in parsed.path.split("/") if p]
        if path_parts:
            video_id = path_parts[0]
    else:
        # Standard youtube.com domain
        path = parsed.path.rstrip("/")
        if path == "/watch":
            qs = parse_qs(parsed.query)
            v_list = qs.get("v")
            if v_list and len(v_list) > 0:
                video_id = v_list[0]
        elif path.startswith("/live/"):
            parts = path.split("/live/")
            if len(parts) > 1 and parts[1]:
                video_id = parts[1].split("/")[0]
        elif path.startswith("/embed/"):
            parts = path.split("/embed/")
            if len(parts) > 1 and parts[1]:
                video_id = parts[1].split("/")[0]

    if not video_id:
        raise InvalidYouTubeURLError(
            f"Could not extract a valid YouTube video ID from URL: {raw_url}"
        )

    # Validate strict 11-character video ID
    video_id = video_id.strip()
    if not YOUTUBE_VIDEO_ID_REGEX.match(video_id):
        raise InvalidYouTubeURLError(
            f"Extracted video ID '{video_id}' does not match expected YouTube format (11 chars: [0-9A-Za-z_-])."
        )

    return video_id


def canonicalize_youtube_url(raw_url: str) -> str:
    """Extract and validate the YouTube stream/video URL, stripping all tracking parameters.

    Returns format:
    - Video: https://www.youtube.com/watch?v=VIDEO_ID
    - Channel live: https://www.youtube.com/@handle/live or /channel/ID/live
    """
    if not raw_url or not isinstance(raw_url, str):
        raise InvalidYouTubeURLError("URL must be a non-empty string.")

    cleaned_url = raw_url.strip()
    if not cleaned_url:
        raise InvalidYouTubeURLError("URL cannot be whitespace.")

    if not cleaned_url.startswith(("http://", "https://")):
        cleaned_url = "https://" + cleaned_url

    try:
        parsed = urlparse(cleaned_url)
    except Exception as exc:
        raise InvalidYouTubeURLError(f"Malformed URL structure: {exc}") from exc

    if parsed.scheme not in ("http", "https"):
        raise InvalidYouTubeURLError(f"Unsupported URL scheme: {parsed.scheme}")

    if parsed.username or parsed.password:
        raise InvalidYouTubeURLError("User credentials in URL are strictly forbidden.")

    if parsed.port and parsed.port not in (80, 443):
        raise InvalidYouTubeURLError(f"Unexpected URL port: {parsed.port}")

    hostname = (parsed.hostname or "").lower()
    if hostname not in ALLOWED_YOUTUBE_HOSTS:
        raise InvalidYouTubeURLError(
            f"Host '{hostname}' is not an authorized YouTube domain."
        )

    # 1. Check for Channel Live URLs: /@handle/live, /channel/ID/live, /c/name/live, /user/name/live
    path = parsed.path.rstrip("/")
    channel_live_match = re.match(
        r"^/(@[a-zA-Z0-9_.-]+|channel/[a-zA-Z0-9_-]+|c/[a-zA-Z0-9_.-]+|user/[a-zA-Z0-9_.-]+)/live$",
        path,
    )
    if channel_live_match:
        return f"https://www.youtube.com{channel_live_match.group(0)}"

    # 2. Extract and validate video ID
    video_id = extract_youtube_video_id(raw_url)
    return f"https://www.youtube.com/watch?v={video_id}"
