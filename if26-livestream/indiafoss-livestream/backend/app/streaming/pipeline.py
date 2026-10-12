"""Streamlink and FFmpeg command builders for IndiaFOSS 2026 livestream backend.

ZERO TRANSCODING ENFORCEMENT:
FFmpeg MUST use stream copy (-c copy). Any attempt to pass encoding parameters,
codecs (libx264, aac, etc.), or video filters is strictly prohibited and structurally rejected.
"""

from typing import Sequence
from .url_sanitizer import canonicalize_youtube_url

# Standard ordered quality fallback list (prioritizing 1080p60 down to 720p and best)
DEFAULT_QUALITY_CHAIN = "1080p60,1080p,720p60,720p,best"

# Prohibited exact flags
FORBIDDEN_EXACT_FLAGS = {
    "-vf",
    "-af",
    "-filter_complex",
    "-filter:v",
    "-filter:a",
    "-s",  # scaling
    "-r",  # fps override
    "-b:v",
    "-b:a",
    "-crf",
    "-preset",
}

# Prohibited codec & filter substrings
FORBIDDEN_CODEC_SUBSTRINGS = {
    "libx264",
    "libx265",
    "nvenc",
    "vaapi",
    "qsv",
    "libvpx",
    "aac",
    "libmp3lame",
    "libopus",
    "scale=",
}


class TranscodingAttemptError(ValueError):
    """Raised when an attempt to introduce re-encoding or transcoding is detected."""


def normalize_quality_chain(quality_spec: str) -> str:
    """Ensure quality spec has safe fallback so Streamlink never crashes if a specific fps/res is absent."""
    q = (quality_spec or "").strip()
    if not q or q == "default":
        return DEFAULT_QUALITY_CHAIN

    # Reject unexpected shell characters
    for char in (";", "&", "|", "`", "$", " ", "\n", "\r"):
        if char in q:
            return DEFAULT_QUALITY_CHAIN

    # If already a comma-separated chain
    if "," in q:
        tokens = [t.strip() for t in q.split(",") if t.strip()]
        if "best" not in tokens:
            tokens.append("best")
        return ",".join(tokens)

    # Single target mapping with graceful fallbacks
    if q == "1080p60":
        return "1080p60,1080p,720p60,720p,best"
    elif q == "1080p":
        return "1080p,1080p60,720p,best"
    elif q == "720p60":
        return "720p60,720p,best"
    elif q == "720p":
        return "720p,best"
    elif q == "best":
        return "best"
    elif q == "worst":
        return "worst"
    else:
        return f"{q},best"


def build_streamlink_command(
    raw_youtube_url: str,
    quality_spec: str = DEFAULT_QUALITY_CHAIN,
    additional_flags: Sequence[str] | None = None,
    cookies_file: str | None = None,
    proxy: str | None = None,
) -> list[str]:
    """Construct a clean, argument-array Streamlink command.

    - Input URL is strictly canonicalized first to guarantee tracking parameters are stripped.
    - Quality is normalized into a safe fallback chain so Streamlink never crashes on resolution/fps mismatches.
    - Output is written strictly to stdout (--stdout) for piping to FFmpeg.
    - cookies_file (Netscape format) lets Streamlink pass YouTube's bot check from datacenter IPs.
    - proxy (e.g. socks5h://host:40000, a Cloudflare WARP proxy) routes all YouTube traffic, so
      YouTube sees the proxy's IP instead of the server's (datacenter IPs get bot-checked).
    """
    canonical_url = canonicalize_youtube_url(raw_youtube_url)
    safe_quality = normalize_quality_chain(quality_spec)

    cmd = [
        "streamlink",
        "--stdout",
        # Wait (indefinitely) for a scheduled broadcast to go live instead of exiting
        "--retry-streams",
        "5",
        "--retry-max",
        "0",
        "--retry-open",
        "3",
        # Exit when YouTube stops delivering data so the supervisor restarts the pipeline
        "--stream-timeout",
        "20",
        "--hls-live-edge",
        "3",
        "--stream-segment-threads",
        "2",
        # Live broadcasts only: a recording (progressive HTTP) would be pulled at full speed
        "--stream-types",
        "hls",
    ]
    if cookies_file:
        cmd.extend(["--http-cookies-file", cookies_file])
    if proxy:
        cmd.extend(["--http-proxy", proxy])
    cmd.extend([canonical_url, safe_quality])

    if additional_flags:
        for flag in additional_flags:
            if not any(bad in flag for bad in (";", "&", "|", "`", "$")):
                cmd.append(flag)

    return cmd


def build_ffmpeg_command(
    rtsp_target_url: str,
    custom_args: Sequence[str] | None = None,
) -> list[str]:
    """Construct a clean, argument-array FFmpeg command publishing RTSP over TCP.

    ZERO TRANSCODING MANDATE:
    Must contain "-c", "copy".
    Must never contain re-encoding flags or filters.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        "pipe:0",  # Read from stdin
        "-c",
        "copy",    # STRICT STREAM COPY ONLY — NO RE-ENCODING
        "-rtpflags",
        "latm",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        rtsp_target_url,
    ]

    if custom_args:
        cmd.extend(custom_args)

    # Structural verification: assert -c copy is present and no encoding arguments
    verify_zero_transcoding(cmd)
    return cmd


def verify_zero_transcoding(ffmpeg_cmd: list[str]) -> None:
    """Verify structurally that FFmpeg is strictly performing a copy-only stream remux."""
    has_copy = False
    for i, arg in enumerate(ffmpeg_cmd):
        if arg == "-c" and i + 1 < len(ffmpeg_cmd) and ffmpeg_cmd[i + 1] == "copy":
            has_copy = True
        elif arg == "-c:v" and i + 1 < len(ffmpeg_cmd) and ffmpeg_cmd[i + 1] != "copy":
            raise TranscodingAttemptError("Video codec is not copy.")
        elif arg == "-c:a" and i + 1 < len(ffmpeg_cmd) and ffmpeg_cmd[i + 1] != "copy":
            raise TranscodingAttemptError("Audio codec is not copy.")

        arg_lower = arg.lower()
        if arg_lower in FORBIDDEN_EXACT_FLAGS:
            raise TranscodingAttemptError(
                f"Forbidden encoding flag '{arg}' in FFmpeg arguments."
            )
        for sub in FORBIDDEN_CODEC_SUBSTRINGS:
            if sub in arg_lower:
                raise TranscodingAttemptError(
                    f"Forbidden encoding token '{sub}' in FFmpeg argument: {arg}"
                )

    if not has_copy:
        raise TranscodingAttemptError(
            "FFmpeg command missing structural '-c copy' stream copy argument."
        )
