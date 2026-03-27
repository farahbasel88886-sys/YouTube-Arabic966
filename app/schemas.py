from pydantic import BaseModel
from typing import Optional


class VideoMetadata(BaseModel):
    title: str
    url: str
    duration: Optional[int] = None
    uploader: Optional[str] = None
    upload_date: Optional[str] = None
    sanitized_title: str


class TranscriptionResult(BaseModel):
    raw_text: str
    language: Optional[str] = None
    segments: list = []


class GeneratedContent(BaseModel):
    transcript_ar: str
    summary_tldr: str
    twitter_thread: str
    faq: str


class PipelineResult(BaseModel):
    metadata: VideoMetadata
    transcription: TranscriptionResult
    generated: GeneratedContent
    output_dir: str
