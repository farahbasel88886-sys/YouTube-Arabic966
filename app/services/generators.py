"""Content generators with provider-aware completion and fallback."""

from typing import Literal

from app.services import openai_client, zai_client
from app.utils.files import load_prompt
from app.utils.logger import get_logger

logger = get_logger(__name__)

# System prompt shared by all Arabic-generation tasks.
_ARABIC_SYSTEM = "أنت محرر ومحلل محتوى عربي متخصص. أجب دائماً باللغة العربية الفصحى الواضحة."
Provider = Literal["zai", "openai"]


def _render(template: str, transcript: str) -> str:
    """Replace the {{transcript}} placeholder with actual content."""
    return template.replace("{{transcript}}", transcript)


def _postprocess_arabic(text: str) -> str:
    """Light post-processing: remove repeated consecutive phrases and broken fragments."""
    import re
    # Remove runs of 2+ identical words (e.g. "اه اه اه" → "")
    text = re.sub(r'\b(\S+)(\s+\1){2,}\b', '', text)
    # Collapse 3+ consecutive whitespace/newlines into two newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove lines that are only punctuation or single characters (broken fragments)
    lines = [ln for ln in text.splitlines() if not re.fullmatch(r'[\W\s]{0,3}', ln.strip())]
    return '\n'.join(lines).strip()


def _normalize_provider(provider: str) -> Provider:
    p = (provider or "zai").strip().lower()
    if p not in {"zai", "openai"}:
        raise ValueError(f"Unsupported provider: {provider!r}")
    return p  # type: ignore[return-value]


def _complete_with_provider(
    user_prompt: str,
    *,
    provider: str,
    zai_api_key: str,
    zai_base_url: str,
    zai_model: str,
    openai_api_key: str | None,
    openai_model: str,
    openai_base_url: str,
    system_prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    p = _normalize_provider(provider)

    if p == "openai":
        if not openai_api_key:
            raise openai_client.OpenAIError(
                "OPENAI_API_KEY is missing. Set it in .env to use OpenAI provider."
            )
        return openai_client.complete(
            user_prompt,
            api_key=openai_api_key,
            base_url=openai_base_url,
            model=openai_model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # Default provider: Z.ai
    transient = (zai_client.ZAIRateLimitError, zai_client.ZAIServerError)
    try:
        return zai_client.complete(
            user_prompt,
            api_key=zai_api_key,
            base_url=zai_base_url,
            model=zai_model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except transient as first_exc:
        logger.warning(f"Z.ai transient error; retrying once before fallback: {first_exc}")
        try:
            return zai_client.complete(
                user_prompt,
                api_key=zai_api_key,
                base_url=zai_base_url,
                model=zai_model,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except transient as second_exc:
            logger.warning(f"Z.ai failed again; switching to OpenAI fallback: {second_exc}")
            if not openai_api_key:
                raise
            return openai_client.complete(
                user_prompt,
                api_key=openai_api_key,
                base_url=openai_base_url,
                model=openai_model,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )


def clean_transcript(
    transcript: str,
    *,
    provider: str = "zai",
    zai_api_key: str,
    zai_base_url: str,
    zai_model: str,
    openai_api_key: str | None = None,
    openai_model: str = "gpt-4.1-mini",
    openai_base_url: str = "https://api.openai.com/v1",
) -> str:
    """Return a cleaned, properly formatted Arabic transcript."""
    logger.info(f"Cleaning Arabic transcript via provider={provider}…")
    user_prompt = _render(load_prompt("cleanup_arabic"), transcript)
    result = _complete_with_provider(
        user_prompt,
        provider=provider,
        zai_api_key=zai_api_key,
        zai_base_url=zai_base_url,
        zai_model=zai_model,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        openai_base_url=openai_base_url,
        system_prompt=_ARABIC_SYSTEM,
        temperature=0.1,
        max_tokens=4096,
    )
    return _postprocess_arabic(result)


def generate_tldr(
    transcript: str,
    *,
    provider: str = "zai",
    zai_api_key: str,
    zai_base_url: str,
    zai_model: str,
    openai_api_key: str | None = None,
    openai_model: str = "gpt-4.1-mini",
    openai_base_url: str = "https://api.openai.com/v1",
) -> str:
    """Return a concise Arabic TL;DR with key points."""
    logger.info(f"Generating TL;DR via provider={provider}…")
    _kwargs = dict(
        provider=provider,
        zai_api_key=zai_api_key,
        zai_base_url=zai_base_url,
        zai_model=zai_model,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        openai_base_url=openai_base_url,
        system_prompt=_ARABIC_SYSTEM,
        temperature=0.2,
        max_tokens=600,
    )
    try:
        return _complete_with_provider(_render(load_prompt("tldr"), transcript), **_kwargs)
    except (zai_client.ZAIError, openai_client.OpenAIError) as exc:
        logger.warning(f"TL;DR failed on full transcript ({exc}); retrying with truncated input…")
        truncated = transcript[:3000]
        return _complete_with_provider(_render(load_prompt("tldr"), truncated), **_kwargs)


def generate_twitter_thread(
    transcript: str,
    *,
    provider: str = "zai",
    zai_api_key: str,
    zai_base_url: str,
    zai_model: str,
    openai_api_key: str | None = None,
    openai_model: str = "gpt-4.1-mini",
    openai_base_url: str = "https://api.openai.com/v1",
) -> str:
    """Return an Arabic Twitter/X thread derived from the transcript."""
    logger.info(f"Generating Twitter thread via provider={provider}…")
    user_prompt = _render(load_prompt("twitter_thread"), transcript)
    return _complete_with_provider(
        user_prompt,
        provider=provider,
        zai_api_key=zai_api_key,
        zai_base_url=zai_base_url,
        zai_model=zai_model,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        openai_base_url=openai_base_url,
        system_prompt=_ARABIC_SYSTEM,
        temperature=0.5,
        max_tokens=1800,
    )


def generate_faq(
    transcript: str,
    *,
    provider: str = "zai",
    zai_api_key: str,
    zai_base_url: str,
    zai_model: str,
    openai_api_key: str | None = None,
    openai_model: str = "gpt-4.1-mini",
    openai_base_url: str = "https://api.openai.com/v1",
) -> str:
    """Return an Arabic FAQ derived from the transcript."""
    logger.info(f"Generating FAQ via provider={provider}…")
    user_prompt = _render(load_prompt("faq"), transcript)
    return _complete_with_provider(
        user_prompt,
        provider=provider,
        zai_api_key=zai_api_key,
        zai_base_url=zai_base_url,
        zai_model=zai_model,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        openai_base_url=openai_base_url,
        system_prompt=_ARABIC_SYSTEM,
        temperature=0.2,
        max_tokens=1600,
    )
