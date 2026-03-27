"""
OpenAI API client — OpenAI-compatible adapter.

Endpoint used:
    POST {OPENAI_BASE_URL}/chat/completions
"""

from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.utils.logger import get_logger

logger = get_logger(__name__)

_REQUEST_TIMEOUT = 300

# Persistent client — reuses TCP/TLS connections across calls.
_client = httpx.Client(timeout=_REQUEST_TIMEOUT)


class OpenAIError(Exception):
    """Non-retryable OpenAI API error."""


class OpenAIRateLimitError(OpenAIError):
    """HTTP 429 — retryable."""


class OpenAIServerError(OpenAIError):
    """HTTP 5xx — retryable."""


@retry(
    retry=retry_if_exception_type(
        (OpenAIRateLimitError, OpenAIServerError, httpx.TimeoutException, httpx.TransportError)
    ),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=10, max=60),
    reraise=True,
)
def complete(
    user_prompt: str,
    *,
    api_key: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    system_prompt: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Send a prompt to OpenAI and return assistant content text."""
    endpoint = f"{base_url.rstrip('/')}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        response = _client.post(endpoint, json=payload, headers=headers)
    except httpx.TimeoutException:
        logger.warning("OpenAI request timed out — will retry.")
        raise
    except httpx.TransportError as exc:
        logger.warning(f"OpenAI transport error: {exc} — will retry.")
        raise

    if response.status_code == 429:
        logger.warning("OpenAI rate limit hit — will retry.")
        raise OpenAIRateLimitError("Rate limit exceeded (HTTP 429).")

    if response.status_code >= 500:
        logger.warning(f"OpenAI server error HTTP {response.status_code} — will retry.")
        raise OpenAIServerError(f"OpenAI server error HTTP {response.status_code}: {response.text[:500]}")

    if response.status_code >= 400:
        raise OpenAIError(f"OpenAI client error HTTP {response.status_code}: {response.text[:500]}")

    try:
        data = response.json()
        content: str = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise OpenAIError(
            f"Unexpected OpenAI response shape: {exc}. Raw: {response.text[:500]}"
        ) from exc

    if not content or not content.strip():
        raise OpenAIError("OpenAI returned empty content.")

    return content.strip()
