"""Audio downloader using yt-dlp."""

import base64
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Fixed path — no tempfile, survives the entire request lifetime.
_COOKIES_PATH = "/tmp/yt_cookies.txt"


# ---------------------------------------------------------------------------
# Cookies helpers
# ---------------------------------------------------------------------------

def _prepare_cookies() -> str | None:
    """
    Decode YTDLP_COOKIES (base64-encoded Netscape cookies.txt) and write it
    to /tmp/yt_cookies.txt.

    Returns the path on success, None on any failure or when env var is absent.
    Uses print(..., flush=True) so output is always visible in Render stdout.
    """
    raw_b64 = os.environ.get("YTDLP_COOKIES", "").strip()

    if not raw_b64:
        print("[yt-dlp] YTDLP_COOKIES not set — running without cookies", flush=True)
        logger.info("YTDLP_COOKIES not set — running without cookies")
        return None

    try:
        decoded = base64.b64decode(raw_b64)
    except Exception as exc:
        print(f"[yt-dlp] YTDLP_COOKIES base64 decode failed: {exc} — running without cookies", flush=True)
        logger.warning(f"YTDLP_COOKIES base64 decode failed: {exc}")
        return None

    try:
        Path(_COOKIES_PATH).write_bytes(decoded)
    except OSError as exc:
        print(f"[yt-dlp] Cannot write {_COOKIES_PATH}: {exc} — running without cookies", flush=True)
        logger.warning(f"Cannot write cookies file: {exc}")
        return None

    print(f"[yt-dlp] Using cookies: {_COOKIES_PATH} ({len(decoded)} bytes)", flush=True)
    logger.info(f"Using cookies: {_COOKIES_PATH} ({len(decoded)} bytes)")
    return _COOKIES_PATH


def _run_ydl(ydl_opts: dict, url: str, cookies_path: str | None) -> dict:
    """
    Run yt-dlp with the given options. If bot-detection triggers and cookies
    were supplied, retry once without cookies. Returns the info dict.

    Raises DownloadError on unrecoverable failure.
    """
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Fetching: {url}  cookies={'yes' if cookies_path else 'no'}")
            return ydl.extract_info(url, download=True)

    except yt_dlp.utils.DownloadError as exc:
        err_msg = str(exc)
        is_bot = "Sign in to confirm" in err_msg or "bot" in err_msg.lower()

        if is_bot and cookies_path:
            # Cookies didn't help — try once more without them.
            print("[yt-dlp] Bot-detection despite cookies — retrying without cookies", flush=True)
            logger.warning("Bot-detection despite cookies — retrying without cookies")
            fallback_opts = {k: v for k, v in ydl_opts.items() if k != "cookiefile"}
            try:
                with yt_dlp.YoutubeDL(fallback_opts) as ydl2:
                    return ydl2.extract_info(url, download=True)
            except yt_dlp.utils.DownloadError as exc2:
                raise DownloadError(f"yt-dlp failed (with and without cookies): {exc2}") from exc2

        if is_bot:
            print(
                "[yt-dlp] Bot-detection triggered. "
                "Set YTDLP_COOKIES env var (base64 cookies.txt) to fix this.",
                flush=True,
            )
            logger.error(
                "YouTube bot-detection triggered. "
                "Set YTDLP_COOKIES env var (base64-encoded cookies.txt)."
            )

        raise DownloadError(f"yt-dlp failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class DownloadError(Exception):
    pass


def normalize_youtube_url(url: str) -> str:
    """
    Normalize supported YouTube URL shapes into:
    https://www.youtube.com/watch?v=<video_id>

    Supported inputs:
    - youtu.be/<id>
    - youtube.com/watch?v=<id>
    - youtube.com/shorts/<id>
    - m.youtube.com/watch?v=<id>
    """
    raw = (url or "").strip()
    if not raw:
        raise DownloadError("Invalid YouTube URL: empty input.")

    candidate = raw
    parsed = urlparse(candidate)
    if not parsed.scheme:
        candidate = f"https://{raw}"
        parsed = urlparse(candidate)

    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    path_parts = [p for p in parsed.path.split("/") if p]
    video_id = ""

    if host == "youtu.be":
        if path_parts:
            video_id = path_parts[0]
    elif host in {"youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            query = parse_qs(parsed.query)
            video_id = (query.get("v") or [""])[0]
        elif len(path_parts) >= 2 and path_parts[0] == "shorts":
            video_id = path_parts[1]

    video_id = (video_id or "").strip().split("?")[0].split("&")[0]
    if not video_id:
        raise DownloadError(f"Invalid YouTube URL: {url!r}")

    return f"https://www.youtube.com/watch?v={video_id}"


def validate_url(url: str) -> bool:
    """Return True if URL can be normalized to a supported YouTube watch URL."""
    try:
        normalize_youtube_url(url)
        return True
    except Exception:
        return False


def download_audio(url: str, output_dir: Path) -> tuple[Path, dict]:
    """
    Download the best available audio stream from a YouTube URL.

    Returns:
        (audio_path, metadata_dict)

    Raises:
        DownloadError: invalid URL, unavailable video, or yt-dlp failure.
    """
    original_url = url
    normalized_url = normalize_youtube_url(url)
    logger.info(f"YouTube URL normalized: {original_url} -> {normalized_url}")
    url = normalized_url

    output_dir.mkdir(parents=True, exist_ok=True)

    downloaded_files: list[Path] = []

    def _on_progress(d: dict) -> None:
        if d.get("status") == "finished":
            downloaded_files.append(Path(d["filename"]))

    ydl_opts: dict = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": False,
        "progress_hooks": [_on_progress],
    }

    # Inject cookies if available.
    cookies_path = _prepare_cookies()
    if cookies_path:
        ydl_opts["cookiefile"] = cookies_path
        print(f"[yt-dlp] cookiefile set: {cookies_path}", flush=True)

    info = _run_ydl(ydl_opts, url, cookies_path)

    if info is None:
        raise DownloadError("yt-dlp returned no video info.")

    # Prefer the path from the progress hook; fall back to directory scan.
    if downloaded_files:
        audio_path = downloaded_files[-1]
    else:
        candidates = [
            p for p in output_dir.iterdir()
            if p.is_file() and p.suffix.lower() != ".json"
        ]
        if not candidates:
            raise DownloadError("No audio file found after download.")
        audio_path = candidates[0]

    metadata = {
        "title": info.get("title", "Unknown"),
        "url": url,
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "upload_date": info.get("upload_date"),
    }

    logger.info(f"Downloaded: {audio_path.name}")
    return audio_path, metadata
