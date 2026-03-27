"""
FastAPI web application — exposes the YouTube → Arabic pipeline via HTTP.

Run:
    uvicorn app.web.app:app --reload
    python -m app.web.app
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from time import time

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

from app.config import Settings
from app.services.transcriber import normalize_transcription_mode
from app.services.downloader import DownloadError, normalize_youtube_url
from app.utils.logger import get_logger
from app.web.service import (
    _find_existing,
    _load_outputs,
    _resolve_title,
    generate_from_transcript,
    process_uploaded_media,
    process_video,
)
from app.utils.files import sanitize_title

logger = get_logger(__name__)

BASE_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Web server starting up")
    yield
    logger.info("Web server shutting down")


app = FastAPI(
    title="YouTube → Arabic Transcription Engine",
    description="Convert YouTube videos to cleaned Arabic transcripts, TL;DR, Twitter threads, and FAQ.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Shared settings instance (loaded once from .env)
_settings = Settings()

# Semaphore: only one pipeline run at a time (Whisper is CPU-heavy)
_pipeline_lock = asyncio.Semaphore(1)

# Timeout for the full pipeline in seconds (48-min video takes ~90 min on CPU)
_PIPELINE_TIMEOUT = 7200
_LOCK_ACQUIRE_TIMEOUT = 5
_PROGRESS_STALE_TIMEOUT = 180
_PROGRESS_RESET_DELAY = 15
_MAX_UPLOAD_SIZE_BYTES = _settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

_UPLOAD_FILE_TYPES = {
    ".mp4": "video",
    ".mov": "video",
    ".mkv": "video",
    ".webm": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".ogg": "audio",
}

_progress_state = {
    "status": "idle",
    "percent": 0,
    "message": "Idle",
    "updated_at": None,
}
_progress_reset_task: asyncio.Task | None = None


def _set_progress(status: str, percent: int, message: str) -> None:
    _progress_state["status"] = status
    _progress_state["percent"] = max(0, min(100, percent))
    _progress_state["message"] = message
    _progress_state["updated_at"] = int(time())


def _is_progress_stale() -> bool:
    if _progress_state.get("status") != "processing":
        return False
    updated = _progress_state.get("updated_at")
    if not updated:
        return False
    return (int(time()) - int(updated)) > _PROGRESS_STALE_TIMEOUT


def _schedule_progress_reset() -> None:
    global _progress_reset_task

    if _progress_reset_task and not _progress_reset_task.done():
        _progress_reset_task.cancel()

    async def _reset_later() -> None:
        try:
            await asyncio.sleep(_PROGRESS_RESET_DELAY)
            if _progress_state.get("status") != "processing":
                _set_progress("idle", 0, "Idle")
        except asyncio.CancelledError:
            return

    _progress_reset_task = asyncio.create_task(_reset_later())


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ProcessRequest(BaseModel):
    youtube_url: str
    provider: str = "zai"
    transcription_mode: str = "balanced"

    @field_validator("provider")
    @classmethod
    def must_be_provider(cls, v: str) -> str:
        p = (v or "zai").strip().lower()
        if p not in {"zai", "openai"}:
            raise ValueError("provider must be 'zai' or 'openai'")
        return p

    @field_validator("transcription_mode")
    @classmethod
    def must_be_valid_mode(cls, v: str) -> str:
        return normalize_transcription_mode(v)


class ProcessResponse(BaseModel):
    status: str
    cached: bool
    data: dict


class GenerateRequest(BaseModel):
    video_id: str | None = None
    output_dir: str | None = None
    provider: str = "zai"

    @field_validator("provider")
    @classmethod
    def must_be_provider(cls, v: str) -> str:
        p = (v or "zai").strip().lower()
        if p not in {"zai", "openai"}:
            raise ValueError("provider must be 'zai' or 'openai'")
        return p

    @field_validator("video_id", "output_dir")
    @classmethod
    def strip_optional(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        return s or None


def _safe_upload_name(filename: str) -> str:
    raw_name = Path(filename or "uploaded_file").name
    stem = sanitize_title(Path(raw_name).stem)
    suffix = Path(raw_name).suffix.lower()
    return f"{stem or 'uploaded_file'}{suffix}"


def _validate_upload_file(file: UploadFile, request: Request) -> tuple[str, str]:
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename.")

    ext = Path(filename).suffix.lower()
    media_type = _UPLOAD_FILE_TYPES.get(ext)
    if not media_type:
        allowed = ", ".join(sorted(_UPLOAD_FILE_TYPES.keys()))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed types: {allowed}",
        )

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_UPLOAD_SIZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File is too large. Max upload size is {_settings.MAX_UPLOAD_SIZE_MB} MB.",
                )
        except ValueError:
            pass

    return ext, media_type


def _save_upload_to_temp(upload: UploadFile, settings: Settings) -> Path:
    temp_dir = Path(settings.TEMP_DIR)
    temp_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_upload_name(upload.filename or "uploaded_file")
    target = temp_dir / f"upload_{int(time())}_{safe_name}"

    written = 0
    chunk_size = 1024 * 1024
    try:
        with target.open("wb") as out:
            while True:
                chunk = upload.file.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > _MAX_UPLOAD_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File is too large. Max upload size is {_settings.MAX_UPLOAD_SIZE_MB} MB.",
                    )
                out.write(chunk)
    except HTTPException:
        if target.exists():
            target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if target.exists():
            target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {exc}")

    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    return target


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.post("/process", response_model=ProcessResponse)
async def process(body: ProcessRequest):
    global _pipeline_lock

    original_url = body.youtube_url
    try:
        url = normalize_youtube_url(original_url)
    except DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    logger.info(f"Web URL normalized: {original_url} -> {url}")
    provider = body.provider
    transcription_mode = body.transcription_mode
    _set_progress("processing", 2, "Resolving video info")

    # Cache check is fast — do it outside the lock
    title = _resolve_title(url)
    if title:
        existing = _find_existing(_settings, sanitize_title(title))
        if existing:
            try:
                meta = _load_outputs(existing).get("metadata", {})
                if str(meta.get("transcription_mode", "balanced")).lower() != transcription_mode:
                    existing = None
            except Exception:
                existing = None
        if existing:
            try:
                data = _load_outputs(existing)
                _set_progress("completed", 100, "Loaded from cache")
                _schedule_progress_reset()
                return {"status": "success", "cached": True, "data": data}
            except FileNotFoundError as exc:
                logger.warning(f"Partial cache at {existing}: {exc}")

    # Pipeline is CPU-heavy — gate it with a semaphore.
    acquired = False
    try:
        await asyncio.wait_for(_pipeline_lock.acquire(), timeout=_LOCK_ACQUIRE_TIMEOUT)
        acquired = True
    except asyncio.TimeoutError:
        if _is_progress_stale():
            logger.warning("Detected stale processing state; resetting lock and progress")
            _pipeline_lock = asyncio.Semaphore(1)
            _set_progress("idle", 0, "Idle")
            try:
                await asyncio.wait_for(_pipeline_lock.acquire(), timeout=_LOCK_ACQUIRE_TIMEOUT)
                acquired = True
            except asyncio.TimeoutError:
                pass

    if not acquired:
        raise HTTPException(
            status_code=503,
            detail="A pipeline run is already in progress. Please try again shortly.",
        )

    async def _run():
        loop = asyncio.get_event_loop()

        def _progress_callback(step: int, total: int, message: str) -> None:
            percent = int((step / total) * 100)
            _set_progress("processing", percent, message)

        return await loop.run_in_executor(
            None,
            process_video,
            url,
            provider,
            transcription_mode,
            _settings,
            _progress_callback,
        )

    try:
        _set_progress("processing", 5, "Starting pipeline")
        result = await asyncio.wait_for(_run(), timeout=_PIPELINE_TIMEOUT)
        _set_progress("completed", 100, "Completed")
        _schedule_progress_reset()
    except asyncio.TimeoutError:
        _set_progress("failed", _progress_state["percent"], "Pipeline timed out")
        _schedule_progress_reset()
        raise HTTPException(status_code=504, detail="Pipeline timed out.")
    except FileNotFoundError as exc:
        _set_progress("failed", _progress_state["percent"], str(exc))
        _schedule_progress_reset()
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Pipeline failed")
        _set_progress("failed", _progress_state["percent"], f"Pipeline error: {exc}")
        _schedule_progress_reset()
        # Surface a clean message; hide the stack trace
        msg = str(exc)
        if "ZAI" in type(exc).__name__ or "zai" in msg.lower():
            raise HTTPException(status_code=502, detail=f"Z.ai API error: {msg}")
        if "DownloadError" in type(exc).__name__:
            raise HTTPException(status_code=400, detail=f"Download failed: {msg}")
        raise HTTPException(status_code=500, detail=f"Pipeline error: {msg}")
    finally:
        if acquired:
            try:
                _pipeline_lock.release()
            except ValueError:
                pass

    return result


@app.post("/upload", response_model=ProcessResponse)
async def upload_media(
    request: Request,
    media_file: UploadFile = File(...),
    provider: str = "zai",
    transcription_mode: str = "balanced",
):
    global _pipeline_lock

    p = (provider or "zai").strip().lower()
    if p not in {"zai", "openai"}:
        raise HTTPException(status_code=422, detail="provider must be 'zai' or 'openai'")

    mode = normalize_transcription_mode(transcription_mode)

    _, media_type = _validate_upload_file(media_file, request)
    saved_path = _save_upload_to_temp(media_file, _settings)

    _set_progress("processing", 2, "Uploading file...")

    acquired = False
    try:
        await asyncio.wait_for(_pipeline_lock.acquire(), timeout=_LOCK_ACQUIRE_TIMEOUT)
        acquired = True
    except asyncio.TimeoutError:
        if _is_progress_stale():
            logger.warning("Detected stale processing state; resetting lock and progress")
            _pipeline_lock = asyncio.Semaphore(1)
            _set_progress("idle", 0, "Idle")
            try:
                await asyncio.wait_for(_pipeline_lock.acquire(), timeout=_LOCK_ACQUIRE_TIMEOUT)
                acquired = True
            except asyncio.TimeoutError:
                pass

    if not acquired:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=503,
            detail="A pipeline run is already in progress. Please try again shortly.",
        )

    async def _run_upload():
        loop = asyncio.get_event_loop()

        def _progress_callback(step: int, total: int, message: str) -> None:
            percent = int((step / total) * 100)
            _set_progress("processing", percent, message)

        return await loop.run_in_executor(
            None,
            process_uploaded_media,
            saved_path,
            media_file.filename or saved_path.name,
            media_type,
            p,
            mode,
            _settings,
            _progress_callback,
        )

    try:
        _set_progress("processing", 8, "Processing file...")
        result = await asyncio.wait_for(_run_upload(), timeout=_PIPELINE_TIMEOUT)
        _set_progress("completed", 100, "Completed")
        _schedule_progress_reset()
    except asyncio.TimeoutError:
        _set_progress("failed", _progress_state["percent"], "Pipeline timed out")
        _schedule_progress_reset()
        raise HTTPException(status_code=504, detail="Pipeline timed out.")
    except FileNotFoundError as exc:
        _set_progress("failed", _progress_state["percent"], str(exc))
        _schedule_progress_reset()
        raise HTTPException(status_code=500, detail=str(exc))
    except HTTPException:
        _schedule_progress_reset()
        raise
    except Exception as exc:
        logger.exception("Upload pipeline failed")
        _set_progress("failed", _progress_state["percent"], f"Pipeline error: {exc}")
        _schedule_progress_reset()
        msg = str(exc)
        raise HTTPException(status_code=500, detail=f"Upload processing failed: {msg}")
    finally:
        try:
            await media_file.close()
        except Exception:
            pass
        if acquired:
            try:
                _pipeline_lock.release()
            except ValueError:
                pass

    return result


@app.get("/result/{video_id}")
async def get_result(video_id: str):
    """Return cached outputs for a previously processed video (by sanitized title)."""
    output_dir = Path(_settings.OUTPUT_DIR) / video_id
    if not output_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"No results found for: {video_id!r}")

    try:
        data = _load_outputs(output_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"status": "success", "cached": True, "data": data}


def _validate_generate_request(body: GenerateRequest) -> None:
    if not body.video_id and not body.output_dir:
        raise HTTPException(status_code=422, detail="Provide either video_id or output_dir")


@app.post("/generate/clean")
async def generate_clean(body: GenerateRequest):
    _validate_generate_request(body)
    try:
        return generate_from_transcript(
            target="clean",
            provider=body.provider,
            settings=_settings,
            video_id=body.video_id,
            output_dir=body.output_dir,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Generate clean failed")
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}")


@app.post("/generate/tldr")
async def generate_tldr(body: GenerateRequest):
    _validate_generate_request(body)
    try:
        return generate_from_transcript(
            target="tldr",
            provider=body.provider,
            settings=_settings,
            video_id=body.video_id,
            output_dir=body.output_dir,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Generate TLDR failed")
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}")


@app.post("/generate/thread")
async def generate_thread(body: GenerateRequest):
    _validate_generate_request(body)
    try:
        return generate_from_transcript(
            target="thread",
            provider=body.provider,
            settings=_settings,
            video_id=body.video_id,
            output_dir=body.output_dir,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Generate thread failed")
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}")


@app.post("/generate/faq")
async def generate_faq(body: GenerateRequest):
    _validate_generate_request(body)
    try:
        return generate_from_transcript(
            target="faq",
            provider=body.provider,
            settings=_settings,
            video_id=body.video_id,
            output_dir=body.output_dir,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Generate FAQ failed")
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/progress")
async def progress():
    return _progress_state


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.web.app:app", host="0.0.0.0", port=8000, reload=True)
