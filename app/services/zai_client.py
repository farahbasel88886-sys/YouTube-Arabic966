"""
Z.ai API client — isolated adapter layer.

Z.ai exposes an OpenAI-compatible REST API.  All network calls to Z.ai are
made exclusively through this module so that changes to the API contract only
require edits here.

Endpoint used:
    POST {ZAI_BASE_URL}/chat/completions

Authentication:
    Bearer token via the Authorization header.

Response shape assumed:
    {
      "choices": [
        {"message": {"content": "<text>"}}
      ]
    }

Retry policy (tenacity):
    - Up to 3 attempts
    - Exponential back-off: 4 s → 8 s → 16 s (capped at 30 s)
    - Retried on: rate-limit (429), timeout, transport errors
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

_REQUEST_TIMEOUT = 300  # seconds — increased for large Arabic text generation

# Persistent client — reuses TCP/TLS connections across calls.
_client = httpx.Client(timeout=_REQUEST_TIMEOUT)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class ZAIError(Exception):
    """Non-retryable Z.ai API error."""


class ZAIRateLimitError(ZAIError):
    """HTTP 429 — retryable."""


class ZAIServerError(ZAIError):
    """HTTP 5xx — retryable (transient server error)."""


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


@retry(
    retry=retry_if_exception_type(
        (ZAIRateLimitError, ZAIServerError, httpx.TimeoutException, httpx.TransportError)
    ),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=4, max=30),
    reraise=True,
)
def complete(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """
    Send a prompt to Z.ai and return the assistant's reply text.

    Args:
        user_prompt:   The user-role message content.
        api_key:       Z.ai bearer token (never logged).
        base_url:      API root, e.g. "https://api.z.ai/v1".
        model:         Model identifier string.
        system_prompt: Optional system-role instruction prepended to the
                       conversation.  Omitted from the request when None.
        temperature:   Sampling temperature (default 0.3).
        max_tokens:    Maximum tokens to generate (default 4096).

    Returns:
        The assistant message content as a plain string.

    Raises:
        ZAIRateLimitError:      on HTTP 429 (retried automatically).
        ZAIError:               on other 4xx/5xx or malformed response.
        httpx.TimeoutException: on request timeout (retried automatically).
    """
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
        # Disable the chain-of-thought reasoning mode so tokens are spent on
        # the actual response content rather than an extended thinking block.
        "thinking": {"type": "disabled"},
    }

    logger.debug(
        f"Z.ai request → model={model!r} "
        f"system={'yes' if system_prompt else 'no'} "
        f"temperature={temperature} max_tokens={max_tokens}"
    )

    try:
        response = _client.post(endpoint, json=payload, headers=headers)
    except httpx.TimeoutException:
        logger.warning(f"Z.ai request timed out (model={model!r}) — will retry.")
        raise
    except httpx.TransportError as exc:
        logger.warning(f"Z.ai transport error (model={model!r}): {exc} — will retry.")
        raise

    if response.status_code == 429:
        logger.warning(f"Z.ai rate limit hit (model={model!r}) — will retry.")
        raise ZAIRateLimitError("Rate limit exceeded (HTTP 429).")

    if response.status_code >= 500:
        logger.warning(f"Z.ai server error HTTP {response.status_code} (model={model!r}) — will retry.")
        raise ZAIServerError(
            f"Z.ai server error HTTP {response.status_code} "
            f"(model={model!r}): {response.text[:500]}"
        )

    if response.status_code >= 400:
        raise ZAIError(
            f"Z.ai client error HTTP {response.status_code} "
            f"(model={model!r}): {response.text[:500]}"
        )

    try:
        data = response.json()
        msg = data["choices"][0]["message"]
        # For reasoning models, content may be empty while reasoning_content holds the
        # actual response if thinking mode was not fully disabled.  Accept either.
        content: str = msg.get("content") or msg.get("reasoning_content") or ""
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ZAIError(
            f"Unexpected Z.ai response shape (model={model!r}): {exc}\n"
            f"Raw: {response.text[:500]}"
        ) from exc

    if not content or not content.strip():
        raise ZAIError(f"Z.ai returned empty content (model={model!r}).")

    return content.strip()
