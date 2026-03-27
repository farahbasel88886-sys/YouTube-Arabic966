"""
Basic tests for utility functions and URL validation.
These tests run without network access and without API keys.
"""

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# app.utils.files
# ---------------------------------------------------------------------------


class TestSanitizeTitle:
    from app.utils.files import sanitize_title

    def test_basic(self):
        from app.utils.files import sanitize_title
        result = sanitize_title("Hello World")
        assert " " not in result
        assert result == "Hello_World"

    def test_strips_special_chars(self):
        from app.utils.files import sanitize_title
        result = sanitize_title('Video: "Test" <2024>')
        assert ":" not in result
        assert '"' not in result
        assert "<" not in result
        assert ">" not in result

    def test_arabic_title_preserved(self):
        from app.utils.files import sanitize_title
        title = "مرحبا بالعالم"
        result = sanitize_title(title)
        # Arabic characters should be kept
        assert "مرحبا" in result or "مرحبا_بالعالم" in result

    def test_length_capped(self):
        from app.utils.files import sanitize_title
        long_title = "a" * 200
        assert len(sanitize_title(long_title)) <= 100

    def test_empty_string_returns_fallback(self):
        from app.utils.files import sanitize_title
        assert sanitize_title("") == "video"

    def test_only_special_chars_returns_fallback(self):
        from app.utils.files import sanitize_title
        result = sanitize_title('///:::<>')
        assert result == "video"


class TestSaveAndLoadHelpers:
    def test_save_text_creates_file(self, tmp_path):
        from app.utils.files import save_text
        p = tmp_path / "out.txt"
        save_text(p, "مرحبا")
        assert p.read_text(encoding="utf-8") == "مرحبا"

    def test_save_json_valid(self, tmp_path):
        from app.utils.files import save_json
        p = tmp_path / "meta.json"
        save_json(p, {"title": "اختبار", "count": 42})
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["title"] == "اختبار"
        assert data["count"] == 42

    def test_ensure_dir_creates_nested(self, tmp_path):
        from app.utils.files import ensure_dir
        nested = tmp_path / "a" / "b" / "c"
        result = ensure_dir(nested)
        assert result.exists()
        assert result.is_dir()


class TestLoadPrompt:
    def test_loads_cleanup_arabic(self):
        from app.utils.files import load_prompt
        content = load_prompt("cleanup_arabic")
        assert "{{transcript}}" in content
        assert len(content) > 20

    def test_loads_tldr(self):
        from app.utils.files import load_prompt
        content = load_prompt("tldr")
        assert "{{transcript}}" in content

    def test_loads_twitter_thread(self):
        from app.utils.files import load_prompt
        content = load_prompt("twitter_thread")
        assert "{{transcript}}" in content

    def test_loads_faq(self):
        from app.utils.files import load_prompt
        content = load_prompt("faq")
        assert "{{transcript}}" in content

    def test_missing_prompt_raises(self):
        from app.utils.files import load_prompt
        with pytest.raises(FileNotFoundError):
            load_prompt("nonexistent_prompt")


# ---------------------------------------------------------------------------
# app.services.downloader (URL validation only — no network)
# ---------------------------------------------------------------------------


class TestValidateUrl:
    def test_youtube_com(self):
        from app.services.downloader import validate_url
        assert validate_url("https://www.youtube.com/watch?v=abc123") is True

    def test_youtu_be(self):
        from app.services.downloader import validate_url
        assert validate_url("https://youtu.be/abc123") is True

    def test_invalid_url(self):
        from app.services.downloader import validate_url
        assert validate_url("https://vimeo.com/123456") is False

    def test_empty_string(self):
        from app.services.downloader import validate_url
        assert validate_url("") is False


# ---------------------------------------------------------------------------
# app.schemas
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_video_metadata(self):
        from app.schemas import VideoMetadata
        m = VideoMetadata(title="Test", url="https://youtu.be/x", sanitized_title="Test")
        assert m.title == "Test"
        assert m.duration is None

    def test_transcription_result(self):
        from app.schemas import TranscriptionResult
        r = TranscriptionResult(raw_text="مرحبا", language="ar")
        assert r.language == "ar"
        assert r.segments == []

    def test_generated_content(self):
        from app.schemas import GeneratedContent
        g = GeneratedContent(
            transcript_ar="نص",
            summary_tldr="ملخص",
            twitter_thread="خيط",
            faq="أسئلة",
        )
        assert g.transcript_ar == "نص"
