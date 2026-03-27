"""
Recovery script: re-run Steps 4-5 (Z.ai generation + save)
using an already-existing raw_transcript.txt.

Usage:
    python recover_zai.py <output_dir>

Example:
    python recover_zai.py "outputs/تحديث_OpenClaw_24_3_دعم_OpenAI_API_—_اربطه_بأي_تطبيق_تبيه!"
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.config import Settings
from app.services import generators
from app.utils.files import save_json, save_text
from app.utils.logger import get_logger

logger = get_logger(__name__)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python recover_zai.py <output_dir>")
        sys.exit(1)

    output_dir = Path(sys.argv[1])
    transcript_file = output_dir / "raw_transcript.txt"

    if not transcript_file.exists():
        print(f"ERROR: {transcript_file} not found.")
        sys.exit(1)

    settings = Settings()

    raw_text = transcript_file.read_text(encoding="utf-8")
    logger.info(f"Loaded transcript: {len(raw_text)} chars from {transcript_file}")

    zai_kwargs = dict(
        api_key=settings.ZAI_API_KEY,
        base_url=settings.ZAI_BASE_URL,
        model=settings.ZAI_MODEL,
    )

    logger.info(f"Using model: {settings.ZAI_MODEL} at {settings.ZAI_BASE_URL}")

    tasks = [
        ("transcript_ar.md", generators.clean_transcript),
        ("summary_tldr.md",  generators.generate_tldr),
        ("twitter_thread.md", generators.generate_twitter_thread),
        ("faq.md",           generators.generate_faq),
    ]

    for filename, fn in tasks:
        dest = output_dir / filename
        if dest.exists():
            logger.info(f"SKIP (already exists): {filename}")
            continue
        logger.info(f"Calling {fn.__name__} …")
        result = fn(raw_text, **zai_kwargs)
        save_text(dest, result)
        logger.info(f"Saved: {filename}")

    logger.info("Done! Output directory: " + str(output_dir))
    for f in ["transcript_ar.md", "summary_tldr.md", "twitter_thread.md", "faq.md"]:
        p = output_dir / f
        status = f"{p.stat().st_size} bytes" if p.exists() else "MISSING"
        print(f"  {f}: {status}")


if __name__ == "__main__":
    main()
