"""Audio downloader using yt-dlp."""

import base64
import contextlib
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp

from app.utils.logger import get_logger

logger = get_logger(__name__)


@contextlib.contextmanager
def _cookies_file():
    """
    Yield a path to a cookies.txt file if YTDLP_COOKIES env var is set
    (base64-encoded Netscape cookies), otherwise yield None.

    The temp file is deleted on exit regardless of success or failure.
    """
    raw_b64 = os.environ.get("YTDLP_COOKIES", "").strip()
    if not raw_b64:
        logger.debug("YTDLP_COOKIES not set — running without cookies")
        yield None
        return

    try:
        decoded = base64.b64decode(raw_b64)
    except Exception as exc:
        logger.warning(f"YTDLP_COOKIES is set but could not be decoded: {exc} — running without cookies")
        yield None
        return

    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb")
    try:
        tmp.write(decoded)
        tmp.flush()
        tmp.close()
        logger.info(f"Using cookies file: {tmp.name} ({len(decoded)} bytes)")
        yield tmp.name
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp.name)


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

    try:
        with _cookies_file() as cookies_path:
            if cookies_path:
                ydl_opts["cookiefile"] = cookies_path
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Fetching info for: {url}")
                info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        err_msg = str(exc)
        if "Sign in to confirm" in err_msg or "bot" in err_msg.lower():
            logger.error(
                "YouTube bot-detection triggered. "
                "Set YTDLP_COOKIES env var with base64-encoded cookies.txt to fix this on cloud deployments."
            )
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
