"""
Web service layer — wraps the pipeline with caching.

process_video(url) checks whether outputs already exist for the resolved
video title.  If they do, the files are read and returned directly without
re-running Whisper or Z.ai.  If not, the full pipeline is executed.
"""

import json
from pathlib import Path
from typing import Callable

import yt_dlp

from app.config import Settings
from app.pipeline import run_pipeline, run_pipeline_from_media
from app.services.downloader import DownloadError, normalize_youtube_url
from app.services import generators
from app.utils.files import sanitize_title
from app.utils.logger import get_logger

logger = get_logger(__name__)

_REQUIRED_FILES = {
    "transcript": "transcript_ar.md",
    "metadata": "metadata.json",
}

_OPTIONAL_FILES = {
    "tldr": "summary_tldr.md",
    "thread": "twitter_thread.md",
    "faq": "faq.md",
}

_GENERATE_TARGETS: dict[str, tuple[str, str]] = {
    "clean": ("transcript_ar.md", "clean"),
    "tldr": ("summary_tldr.md", "tldr"),
    "thread": ("twitter_thread.md", "thread"),
    "faq": ("faq.md", "faq"),
}


def _resolve_title(url: str) -> str | None:
    """Fetch video title from YouTube without downloading audio."""
    try:
        url = normalize_youtube_url(url)
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title") if info else None
    except (DownloadError, Exception):
        return None


def _load_outputs(output_dir: Path) -> dict:
    """Read all output files from an existing output directory."""
    data: dict = {}

    for key, filename in _REQUIRED_FILES.items():
        p = output_dir / filename
        if not p.exists():
            raise FileNotFoundError(f"Expected output file missing: {filename}")
        if filename.endswith(".json"):
            data[key] = json.loads(p.read_text(encoding="utf-8"))
        else:
            data[key] = p.read_text(encoding="utf-8")

    for key, filename in _OPTIONAL_FILES.items():
        p = output_dir / filename
        data[key] = p.read_text(encoding="utf-8") if p.exists() else ""

    return data


def _find_existing(settings: Settings, sanitized: str) -> Path | None:
    """Return the output directory path if all required files exist, else None."""
    candidate = Path(settings.OUTPUT_DIR) / sanitized
    if not candidate.is_dir():
        return None
    base_required = ["raw_transcript.txt", *_REQUIRED_FILES.values()]
    if all((candidate / fn).exists() for fn in base_required):
        return candidate
    return None


def _cache_matches_request(
    output_dir: Path,
    provider: str,
    transcription_mode: str | None,
) -> bool:
    """Return True if cached metadata matches requested transcription mode."""
    meta_file = output_dir / "metadata.json"
    if not meta_file.exists():
        return False

    try:
        metadata = json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception:
        return False

    if transcription_mode is None:
        return True

    cached_mode = str(metadata.get("transcription_mode", "balanced")).lower()
    return cached_mode == transcription_mode.lower()


def _resolve_output_dir(
    settings: Settings,
    *,
    video_id: str | None = None,
    output_dir: str | None = None,
) -> Path:
    """Resolve output directory from either video_id or output_dir input."""
    if video_id:
        candidate = Path(settings.OUTPUT_DIR) / video_id
    elif output_dir:
        candidate = Path(output_dir)
        if not candidate.is_absolute():
            candidate = Path(settings.OUTPUT_DIR) / output_dir
    else:
        raise ValueError("Provide either video_id or output_dir.")

    if not candidate.is_dir():
        raise FileNotFoundError(f"Output directory not found: {candidate}")
    return candidate


def _load_source_transcript(output_dir: Path) -> str:
    """Load transcript source of truth from outputs directory."""
    raw_file = output_dir / "raw_transcript.txt"
    transcript_file = output_dir / "transcript_ar.md"

    if raw_file.exists():
        text = raw_file.read_text(encoding="utf-8").strip()
        if text:
            return text

    if transcript_file.exists():
        text = transcript_file.read_text(encoding="utf-8").strip()
        if text:
            return text

    raise FileNotFoundError(
        "Transcript source not found. Expected raw_transcript.txt or transcript_ar.md"
    )


def generate_from_transcript(
    *,
    target: str,
    provider: str = "zai",
    settings: Settings | None = None,
    video_id: str | None = None,
    output_dir: str | None = None,
) -> dict:
    """Generate a single output artifact from an existing transcript."""
    if settings is None:
        settings = Settings()

    normalized_target = (target or "").strip().lower()
    if normalized_target not in _GENERATE_TARGETS:
        raise ValueError("target must be one of: clean, tldr, thread, faq")

    resolved_dir = _resolve_output_dir(
        settings,
        video_id=video_id,
        output_dir=output_dir,
    )

    transcript = _load_source_transcript(resolved_dir)
    filename, kind = _GENERATE_TARGETS[normalized_target]

    llm_kwargs = dict(
        provider=provider,
        zai_api_key=settings.ZAI_API_KEY,
        zai_base_url=settings.ZAI_BASE_URL,
        zai_model=settings.ZAI_MODEL,
        openai_api_key=settings.OPENAI_API_KEY,
        openai_model=settings.OPENAI_MODEL,
        openai_base_url=settings.OPENAI_BASE_URL,
    )

    if kind == "clean":
        content = generators.clean_transcript(transcript, **llm_kwargs)
    elif kind == "tldr":
        content = generators.generate_tldr(transcript, **llm_kwargs)
    elif kind == "thread":
        content = generators.generate_twitter_thread(transcript, **llm_kwargs)
    else:
        content = generators.generate_faq(transcript, **llm_kwargs)

    (resolved_dir / filename).write_text(content, encoding="utf-8")
    logger.info(f"Generated {kind} and saved: {resolved_dir / filename}")

    return {
        "status": "success",
        "target": normalized_target,
        "output_dir": str(resolved_dir),
        "content": content,
    }


def process_video(
    url: str,
    provider: str = "zai",
    transcription_mode: str | None = None,
    settings: Settings | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict:
    """
    Main entry point for the web layer.

    Returns:
        {
            "status": "success",
            "cached": bool,
            "data": {
                "transcript": str,
                "tldr": str,
                "thread": str,
                "faq": str,
                "metadata": dict,
            }
        }
    """
    if settings is None:
        settings = Settings()

    original_url = url
    url = normalize_youtube_url(url)
    logger.info(f"Service URL normalized: {original_url} -> {url}")

    # --- Cache check: resolve title without downloading audio ---
    title = _resolve_title(url)
    if title:
        sanitized = sanitize_title(title)
        existing = _find_existing(settings, sanitized)
        if existing and _cache_matches_request(existing, provider, transcription_mode):
            logger.info(f"Cache hit — returning existing outputs from: {existing}")
            if progress_callback is not None:
                progress_callback(9, 9, "Loaded from cache")
            data = _load_outputs(existing)
            return {"status": "success", "cached": True, "data": data}

    # --- Full pipeline ---
    logger.info(f"Cache miss — running full pipeline for: {url} (provider={provider})")
    result = run_pipeline(
        url,
        settings,
        provider=provider,
        transcription_mode=transcription_mode,
        progress_callback=progress_callback,
    )

    output_dir = Path(result.output_dir)
    data = _load_outputs(output_dir)
    return {"status": "success", "cached": False, "data": data}


def process_uploaded_media(
    media_path: Path,
    original_filename: str,
    media_type: str,
    provider: str = "zai",
    transcription_mode: str | None = None,
    settings: Settings | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Process an uploaded media file and return transcript-first outputs."""
    if settings is None:
        settings = Settings()

    result = run_pipeline_from_media(
        media_path,
        settings,
        original_filename=original_filename,
        media_type=media_type,
        provider=provider,
        transcription_mode=transcription_mode,
        progress_callback=progress_callback,
    )

    output_dir = Path(result.output_dir)
    data = _load_outputs(output_dir)
    return {"status": "success", "cached": False, "data": data}
