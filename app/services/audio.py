"""Audio normalization using ffmpeg."""

import shutil
import subprocess
from pathlib import Path

from app.utils.logger import get_logger

logger = get_logger(__name__)


class AudioProcessingError(Exception):
    pass


def check_ffmpeg() -> None:
    """Raise AudioProcessingError if ffmpeg is not on PATH."""
    if shutil.which("ffmpeg") is None:
        raise AudioProcessingError(
            "ffmpeg not found. Install ffmpeg and make sure it is available on PATH."
        )


def normalize_audio(input_path: Path, output_dir: Path) -> Path:
    """
    Convert audio to WAV mono 16 kHz using ffmpeg.

    Args:
        input_path: Path to the downloaded audio file.
        output_dir: Directory where the normalized file will be written.

    Returns:
        Path to the normalized WAV file.

    Raises:
        AudioProcessingError: if ffmpeg is missing or conversion fails.
    """
    check_ffmpeg()

    output_path = output_dir / "audio_normalized.wav"

    cmd = [
        "ffmpeg",
        "-y",                    # overwrite without asking
        "-i", str(input_path),
        "-ac", "1",              # mono
        "-ar", "16000",          # 16 kHz sample rate
        "-acodec", "pcm_s16le",  # 16-bit PCM
        str(output_path),
    ]

    logger.info(f"Normalizing audio → {output_path.name}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise AudioProcessingError(
            f"ffmpeg exited with code {result.returncode}.\n"
            f"stderr: {result.stderr[-1000:]}"
        )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise AudioProcessingError("ffmpeg produced an empty or missing output file.")

    logger.info(f"Normalized audio saved: {output_path.name}")
    return output_path
