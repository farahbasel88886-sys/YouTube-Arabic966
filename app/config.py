from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ZAI_API_KEY: str
    ZAI_BASE_URL: str = "https://api.z.ai/v1"
    ZAI_MODEL: str
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4.1-mini"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_TRANSCRIPTION_MODEL: str = "whisper-1"
    TRANSCRIPTION_MODE: str = "balanced"
    MAX_UPLOAD_SIZE_MB: int = 100
    OUTPUT_DIR: str = "outputs"
    TEMP_DIR: str = ".temp"
    # Optional: base64-encoded contents of a YouTube cookies.txt (Netscape format).
    # Set this env var on Render to bypass bot-detection on cloud IPs.
    # Export via: base64 -w0 cookies.txt
    YTDLP_COOKIES: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
