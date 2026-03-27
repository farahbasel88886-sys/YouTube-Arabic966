"""
Tests for generators.py — verifies task-specific Z.ai parameters without
making real network calls.  Uses unittest.mock to intercept zai_client.complete.
"""

from unittest.mock import MagicMock, patch


_ZAI_KWARGS = dict(api_key="test-key", base_url="https://api.z.ai/v1", model="test-model")
_FAKE_RESPONSE = "نتيجة اختبار"


def _patch_complete():
    return patch("app.services.zai_client.complete", return_value=_FAKE_RESPONSE)


class TestGeneratorParameters:
    """Each generator must call zai_client.complete with its correct params."""

    def test_clean_transcript_params(self):
        from app.services.generators import clean_transcript

        with _patch_complete() as mock_complete:
            result = clean_transcript("نص", **_ZAI_KWARGS)

        assert result == _FAKE_RESPONSE
        _kw = mock_complete.call_args.kwargs
        assert _kw["temperature"] == 0.1
        assert _kw["max_tokens"] == 4096
        assert _kw["system_prompt"] is not None

    def test_generate_tldr_params(self):
        from app.services.generators import generate_tldr

        with _patch_complete() as mock_complete:
            result = generate_tldr("نص", **_ZAI_KWARGS)

        assert result == _FAKE_RESPONSE
        _kw = mock_complete.call_args.kwargs
        assert _kw["temperature"] == 0.2
        assert _kw["max_tokens"] == 1200

    def test_generate_twitter_thread_params(self):
        from app.services.generators import generate_twitter_thread

        with _patch_complete() as mock_complete:
            result = generate_twitter_thread("نص", **_ZAI_KWARGS)

        assert result == _FAKE_RESPONSE
        _kw = mock_complete.call_args.kwargs
        assert _kw["temperature"] == 0.5
        assert _kw["max_tokens"] == 1800

    def test_generate_faq_params(self):
        from app.services.generators import generate_faq

        with _patch_complete() as mock_complete:
            result = generate_faq("نص", **_ZAI_KWARGS)

        assert result == _FAKE_RESPONSE
        _kw = mock_complete.call_args.kwargs
        assert _kw["temperature"] == 0.2
        assert _kw["max_tokens"] == 1600

    def test_all_tasks_share_system_prompt(self):
        """All generators must send the Arabic system prompt."""
        from app.services import generators

        tasks = [
            generators.clean_transcript,
            generators.generate_tldr,
            generators.generate_twitter_thread,
            generators.generate_faq,
        ]
        for fn in tasks:
            with _patch_complete() as mock_complete:
                fn("نص", **_ZAI_KWARGS)
            system = mock_complete.call_args.kwargs.get("system_prompt")
            assert system and len(system) > 5, f"{fn.__name__} missing system_prompt"
