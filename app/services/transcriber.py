"""Local speech-to-text transcription using faster-whisper."""

from pathlib import Path

from faster_whisper import WhisperModel

from app.schemas import TranscriptionResult
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Module-level cache: avoids reloading the same Whisper model on every call.
_model_cache: dict[str, WhisperModel] = {}

_MODE_TO_MODEL = {
    "fast": "base",
    "balanced": "small",
    "quality": "medium",
}


class TranscriptionError(Exception):
    pass


def normalize_transcription_mode(mode: str | None) -> str:
    """Validate and normalize transcription mode."""
    normalized = (mode or "balanced").strip().lower()
    if normalized not in _MODE_TO_MODEL:
        raise TranscriptionError(
            "Invalid transcription mode. Use one of: fast, balanced, quality"
        )
    return normalized


def resolve_whisper_model(mode: str, fallback_model: str = "small") -> str:
    """Resolve the Whisper model name from a transcription mode."""
    normalized = normalize_transcription_mode(mode)
    return _MODE_TO_MODEL.get(normalized, fallback_model)


def transcribe_audio(audio_path: Path, model_name: str = "small") -> TranscriptionResult:
    """
    Transcribe an audio file with faster-whisper.

    The model runs entirely on CPU with int8 quantisation for portability.

    Args:
        audio_path: Path to a WAV (or other supported) audio file.
        model_name: Whisper model size (tiny/base/small/medium/large-v2/…).

    Returns:
        TranscriptionResult with the full text and per-segment data.

    Raises:
        TranscriptionError: if the file is missing, the model fails to load,
                            or transcription produces an empty result.
    """
    if not audio_path.exists():
        raise TranscriptionError(f"Audio file not found: {audio_path}")

    if model_name in _model_cache:
        logger.info(f"Reusing cached Whisper model: {model_name!r}")
        model = _model_cache[model_name]
    else:
        logger.info(f"Loading Whisper model: {model_name!r}")
        try:
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
        except Exception as exc:
            raise TranscriptionError(
                f"Failed to load Whisper model {model_name!r}: {exc}"
            ) from exc
        _model_cache[model_name] = model
        logger.info(f"Cached Whisper model: {model_name!r}")

    logger.info(f"Transcribing: {audio_path.name}")
    try:
        segments_iter, info = model.transcribe(
            str(audio_path),
            language="ar",
            beam_size=5,
            vad_filter=True,
        )

        segment_list = []
        text_parts = []

        for seg in segments_iter:
            text_parts.append(seg.text.strip())
            segment_list.append(
                {"start": seg.start, "end": seg.end, "text": seg.text.strip()}
            )

    except Exception as exc:
        raise TranscriptionError(f"Transcription failed: {exc}") from exc

    full_text = " ".join(text_parts).strip()

    if not full_text:
        raise TranscriptionError(
            "Transcription produced an empty result. "
            "Check that the audio contains audible Arabic speech."
        )

    logger.info(
        f"Transcription complete — language: {info.language}, "
        f"segments: {len(segment_list)}"
    )

    return TranscriptionResult(
        raw_text=full_text,
        language=info.language,
        segments=segment_list,
    )
