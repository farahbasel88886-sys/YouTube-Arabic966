import re
import json
from pathlib import Path


def sanitize_title(title: str) -> str:
    """Convert a video title to a safe filesystem directory name."""
    # Replace characters that are unsafe on Windows/Linux/macOS
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title)
    # Collapse runs of whitespace/dots/dashes into a single underscore
    sanitized = re.sub(r"[\s._-]+", "_", sanitized.strip())
    # Remove leading/trailing underscores
    sanitized = sanitized.strip("_")
    # Limit length to avoid OS path limits
    return sanitized[:100] if sanitized else "video"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_prompt(prompt_name: str) -> str:
    """Load a prompt template from app/prompts/<prompt_name>.txt."""
    prompts_dir = Path(__file__).parent.parent / "prompts"
    prompt_path = prompts_dir / f"{prompt_name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")
