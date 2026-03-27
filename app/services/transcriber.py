"""Speech-to-text transcription via external API (OpenAI-compatible)."""

from pathlib import Path

import httpx

from app.schemas import TranscriptionResult
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MODE_TO_MODEL = {
    "fast": "whisper-1",
    "balanced": "whisper-1",
    "quality": "whisper-1",
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


def transcribe_audio(
    audio_path: Path,
    model_name: str = "whisper-1",
    *,
    api_key: str | None,
    base_url: str,
) -> TranscriptionResult:
    """
    Transcribe an audio file using an OpenAI-compatible audio API.

    Args:
        audio_path: Path to a WAV (or other supported) audio file.
        model_name: Transcription model id (default whisper-1).

    Returns:
        TranscriptionResult with the full text and per-segment data.

    Raises:
        TranscriptionError: if the file is missing, the API call fails,
                            or transcription produces an empty result.
    """
    if not audio_path.exists():
        raise TranscriptionError(f"Audio file not found: {audio_path}")

    if not api_key:
        raise TranscriptionError(
            "OPENAI_API_KEY is required for API-based transcription."
        )

    endpoint = f"{base_url.rstrip('/')}/audio/transcriptions"
    logger.info(f"Transcribing via API: {audio_path.name} (model={model_name})")
    try:
        with audio_path.open("rb") as audio_file:
            files = {
                "file": (audio_path.name, audio_file, "audio/mpeg"),
            }
            data = {
                "model": model_name,
                "language": "ar",
                "response_format": "json",
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
            }
            with httpx.Client(timeout=300.0) as client:
                resp = client.post(endpoint, headers=headers, data=data, files=files)
                resp.raise_for_status()
                payload = resp.json()

        full_text = str(payload.get("text") or "").strip()
        language = payload.get("language")

        segment_list = []
        for seg in payload.get("segments") or []:
            segment_list.append(
                {
                    "start": seg.get("start"),
                    "end": seg.get("end"),
                    "text": str(seg.get("text") or "").strip(),
                }
            )
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500] if exc.response is not None else str(exc)
        raise TranscriptionError(
            f"OpenAI transcription API error ({exc.response.status_code}): {body}"
        ) from exc
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"OpenAI transcription request failed: {exc}") from exc
    except Exception as exc:
        raise TranscriptionError(f"Transcription failed: {exc}") from exc

    if not full_text:
        raise TranscriptionError(
            "Transcription produced an empty result. "
            "Check that the audio contains audible speech."
        )

    logger.info(
        f"Transcription complete — language: {language}, "
        f"segments: {len(segment_list)}"
    )

    return TranscriptionResult(
        raw_text=full_text,
        language=language,
        segments=segment_list,
    )
