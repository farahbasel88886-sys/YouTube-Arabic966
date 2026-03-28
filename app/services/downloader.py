"""Audio downloader using yt-dlp."""

import base64
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp

from app.utils.logger import get_logger

logger = get_logger(__name__)


_COOKIES_PATH = "/tmp/yt_cookies.txt"


def _prepare_cookies() -> str | None:
    """
    Read YTDLP_COOKIES env var (base64-encoded Netscape cookies.txt),
    decode it and write to a fixed path at /tmp/yt_cookies.txt.

    Returns the path string if successful, None otherwise.
    Both print() and logger are used so output is visible in Render stdout logs.
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
        print(f"[yt-dlp] Failed to write cookies to {_COOKIES_PATH}: {exc} — running without cookies", flush=True)
        logger.warning(f"Failed to write cookies file: {exc}")
        return None

    print(f"[yt-dlp] Using cookies: {_COOKIES_PATH} ({len(decoded)} bytes)", flush=True)
    logger.info(f"Using cookies: {_COOKIES_PATH} ({len(decoded)} bytes)")
    return _COOKIES_PATH


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
        (audio_path, metadata_dict) where audio_path is the downloaded file.

    Raises:
        DownloadError: on invalid URL, unavailable video, or yt-dlp failure.
    """
    original_url = url
    normalized_url = normalize_youtube_url(url)
    logger.info(f"YouTube URL normalized: {original_url} -> {normalized_url}")
    url = normalized_url

    output_dir.mkdir(parents=True, exist_ok=True)

    # yt-dlp writes the file; we capture info via the info_dict
    downloaded_files: list[Path] = []

    def _on_progress(d: dict) -> None:
        if d.get("status") == "finished":
            downloaded_files.append(Path(d["filename"]))

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": False,
        "progress_hooks": [_on_progress],
    }

    cookies_path = _prepare_cookies()
    if cookies_path:
        ydl_opts["cookiefile"] = cookies_path
        print(f"[yt-dlp] cookiefile option set: {ydl_opts['cookiefile']}", flush=True)

    info = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Fetching info for: {url} (cookies={'yes' if cookies_path else 'no'})")
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        err_msg = str(exc)
        is_bot = "Sign in to confirm" in err_msg or "bot" in err_msg.lower()
        if is_bot and cookies_path:
            # cookies were supplied but still failed — try once without
            print(f"[yt-dlp] Bot-detection despite cookies, retrying without cookies", flush=True)
            logger.warning("Bot-detection despite cookies — retrying without cookies")
            fallback_opts = {k: v for k, v in ydl_opts.items() if k != "cookiefile"}
            try:
                with yt_dlp.YoutubeDL(fallback_opts) as ydl2:
                    info = ydl2.extract_info(url, download=True)
            except yt_dlp.utils.DownloadError as exc2:
                raise DownloadError(f"yt-dlp failed (with and without cookies): {exc2}") from exc2
        elif is_bot:
            print(
                "[yt-dlp] Bot-detection triggered. "
                "Set YTDLP_COOKIES env var with base64-encoded cookies.txt.",
                flush=True,
            )
            logger.error(
                "YouTube bot-detection triggered. "
                "Set YTDLP_COOKIES env var with base64-encoded cookies.txt."
            )
            raise DownloadError(f"yt-dlp failed: {exc}") from exc
        else:
            raise DownloadError(f"yt-dlp failed: {exc}") from exc

    if info is None:
        raise DownloadError("yt-dlp returned no video info.")

    # Prefer the path captured by the progress hook; fall back to glob
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
