"""Audio downloader using yt-dlp."""

from pathlib import Path
import yt_dlp

from app.utils.logger import get_logger

logger = get_logger(__name__)


class DownloadError(Exception):
    pass


def validate_url(url: str) -> bool:
    """Return True if the URL looks like a YouTube link."""
    return "youtube.com" in url or "youtu.be" in url


def download_audio(url: str, output_dir: Path) -> tuple[Path, dict]:
    """
    Download the best available audio stream from a YouTube URL.

    Returns:
        (audio_path, metadata_dict) where audio_path is the downloaded file.

    Raises:
        DownloadError: on invalid URL, unavailable video, or yt-dlp failure.
    """
    if not validate_url(url):
        raise DownloadError(f"Invalid YouTube URL: {url!r}")

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
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Fetching info for: {url}")
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
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
