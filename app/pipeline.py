"""
Full pipeline orchestrator.

Steps:
1. Download best audio from YouTube (yt-dlp)
2. Normalize to WAV mono 16 kHz (ffmpeg)
3. Transcribe locally (faster-whisper)
4. Save transcript-first outputs to outputs/<sanitized_title>/
"""

import shutil
from pathlib import Path
from typing import Callable
from datetime import datetime

from app.config import Settings
from app.schemas import GeneratedContent, PipelineResult, TranscriptionResult, VideoMetadata
from app.services import audio, downloader, transcriber
from app.utils.files import ensure_dir, sanitize_title, save_json, save_text
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _finalize_transcription(
    *,
    input_media_path: Path,
    output_dir: Path,
    temp_dir: Path,
    provider: str,
    transcription_mode: str,
    metadata_overrides: dict | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    total_steps: int = 5,
    start_step: int = 2,
    fallback_model: str = "small",
) -> tuple[TranscriptionResult, str]:
    """Shared audio normalize + transcribe + save logic."""

    def notify(step: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(step, total_steps, message)

    notify(start_step, "Normalizing audio")
    logger.info("[bold]Step 2/5 — Normalizing audio[/bold]")
    normalized_path = audio.normalize_audio(input_media_path, temp_dir)

    notify(start_step + 1, "Transcribing with Whisper (local)")
    logger.info("[bold]Step 3/5 — Transcribing[/bold]")
    selected_mode = transcriber.normalize_transcription_mode(transcription_mode)
    whisper_model = transcriber.resolve_whisper_model(
        selected_mode,
        fallback_model=fallback_model,
    )
    logger.info(
        f"Transcription mode selected: {selected_mode} (model={whisper_model})"
    )
    transcription: TranscriptionResult = transcriber.transcribe_audio(
        normalized_path,
        model_name=whisper_model,
    )

    save_text(output_dir / "raw_transcript.txt", transcription.raw_text)
    logger.info("Saved: raw_transcript.txt")
    save_text(output_dir / "transcript_ar.md", transcription.raw_text)
    logger.info("Saved: transcript_ar.md")

    notify(start_step + 2, "Saving transcript and metadata")
    logger.info("[bold]Step 4/5 — Saving metadata[/bold]")

    metadata_payload = {
        "title": metadata_overrides.get("title") if metadata_overrides else "Uploaded media",
        "url": metadata_overrides.get("url") if metadata_overrides else str(input_media_path),
        "duration": metadata_overrides.get("duration") if metadata_overrides else None,
        "uploader": metadata_overrides.get("uploader") if metadata_overrides else None,
        "upload_date": metadata_overrides.get("upload_date") if metadata_overrides else None,
        "transcription_mode": selected_mode,
        "whisper_model": whisper_model,
        "language_detected": transcription.language,
        "llm_provider": provider,
        "output_dir": str(output_dir),
    }
    if metadata_overrides:
        metadata_payload.update(metadata_overrides)

    save_json(output_dir / "metadata.json", metadata_payload)

    notify(start_step + 3, "Completed")
    logger.info("[bold]Step 5/5 — Completed[/bold]")

    logger.info(f"All outputs saved to: {output_dir}")
    return transcription, whisper_model


def run_pipeline(
    url: str,
    settings: Settings,
    provider: str = "zai",
    transcription_mode: str | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> PipelineResult:
    """
    Execute the complete pipeline for a YouTube URL.

    Args:
        url:      A valid YouTube video URL.
        settings: Validated Settings instance.

    Returns:
        PipelineResult containing metadata, transcription, generated content,
        and the output directory path.

    Raises:
        Various service-specific exceptions on failure (propagated to the CLI).
    """
    temp_dir = Path(settings.TEMP_DIR)
    output_base = Path(settings.OUTPUT_DIR)
    total_steps = 5

    def notify(step: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(step, total_steps, message)

    # ------------------------------------------------------------------
    # Step 1: Download audio
    # ------------------------------------------------------------------
    notify(1, "Downloading audio from YouTube")
    logger.info("[bold]Step 1/5 — Downloading audio[/bold]")
    temp_dir.mkdir(parents=True, exist_ok=True)

    raw_audio_path, meta_dict = downloader.download_audio(url, temp_dir)

    sanitized = sanitize_title(meta_dict["title"])
    metadata = VideoMetadata(
        title=meta_dict["title"],
        url=url,
        duration=meta_dict.get("duration"),
        uploader=meta_dict.get("uploader"),
        upload_date=meta_dict.get("upload_date"),
        sanitized_title=sanitized,
    )

    output_dir = ensure_dir(output_base / sanitized)
    logger.info(f"Output directory: {output_dir}")

    transcription, whisper_model = _finalize_transcription(
        input_media_path=raw_audio_path,
        output_dir=output_dir,
        temp_dir=temp_dir,
        provider=provider,
        transcription_mode=transcription_mode or settings.TRANSCRIPTION_MODE,
        metadata_overrides={
            "title": metadata.title,
            "url": metadata.url,
            "duration": metadata.duration,
            "uploader": metadata.uploader,
            "upload_date": metadata.upload_date,
        },
        progress_callback=progress_callback,
        total_steps=5,
        start_step=2,
        fallback_model=settings.WHISPER_MODEL,
    )

    # Clean up temp directory
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass  # Non-fatal

    return PipelineResult(
        metadata=metadata,
        transcription=transcription,
        generated=GeneratedContent(
            transcript_ar=transcription.raw_text,
            summary_tldr="",
            twitter_thread="",
            faq="",
        ),
        output_dir=str(output_dir),
    )


def run_pipeline_from_media(
    media_path: Path,
    settings: Settings,
    *,
    original_filename: str,
    media_type: str,
    provider: str = "zai",
    transcription_mode: str | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> PipelineResult:
    """Execute transcript-first pipeline from an uploaded local media file."""
    temp_dir = Path(settings.TEMP_DIR)
    output_base = Path(settings.OUTPUT_DIR)

    def notify(step: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(step, 4, message)

    notify(1, "Processing uploaded file")
    logger.info("[bold]Step 1/4 — Processing uploaded file[/bold]")

    sanitized_name = sanitize_title(Path(original_filename).stem)
    run_suffix = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_dir = ensure_dir(output_base / f"upload_{sanitized_name}_{run_suffix}")

    metadata = VideoMetadata(
        title=Path(original_filename).name,
        url=f"upload://{Path(original_filename).name}",
        duration=None,
        uploader="Uploaded file",
        upload_date=None,
        sanitized_title=output_dir.name,
    )

    transcription, whisper_model = _finalize_transcription(
        input_media_path=media_path,
        output_dir=output_dir,
        temp_dir=temp_dir,
        provider=provider,
        transcription_mode=transcription_mode or settings.TRANSCRIPTION_MODE,
        metadata_overrides={
            "title": metadata.title,
            "url": metadata.url,
            "uploader": metadata.uploader,
            "original_filename": Path(original_filename).name,
            "file_type": media_type,
            "input_kind": "upload",
        },
        progress_callback=progress_callback,
        total_steps=4,
        start_step=1,
        fallback_model=settings.WHISPER_MODEL,
    )

    try:
        if media_path.exists():
            media_path.unlink(missing_ok=True)
    except Exception:
        pass

    return PipelineResult(
        metadata=metadata,
        transcription=transcription,
        generated=GeneratedContent(
            transcript_ar=transcription.raw_text,
            summary_tldr="",
            twitter_thread="",
            faq="",
        ),
        output_dir=str(output_dir),
    )
