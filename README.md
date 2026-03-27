# YouTube → Arabic Transcription Engine

A local Python CLI pipeline that takes a YouTube URL and produces:

| File                 | Description                             |
| -------------------- | --------------------------------------- |
| `raw_transcript.txt` | Raw Whisper output                      |
| `transcript_ar.md`   | Cleaned and formatted Arabic transcript |
| `summary_tldr.md`    | TL;DR — up to 5 key points              |
| `twitter_thread.md`  | Arabic Twitter/X thread (6–10 tweets)   |
| `faq.md`             | Arabic FAQ (5–8 Q&A pairs)              |
| `metadata.json`      | Video metadata and run info             |

---

## Requirements

| Dependency     | Purpose                            |
| -------------- | ---------------------------------- |
| Python 3.11+   | Runtime                            |
| ffmpeg         | Audio extraction and normalisation |
| yt-dlp         | YouTube audio download             |
| faster-whisper | Local Arabic transcription         |
| Z.ai API key   | Post-processing and generation     |

---

## Setup

### 1. Clone and enter the project

```bash
git clone <repo-url>
cd youtube-arabic-transcriber
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Install ffmpeg

- **Windows**: Download from https://ffmpeg.org/download.html and add the `bin/` folder to PATH.
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg`

### 5. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```
ZAI_API_KEY=your_actual_key
ZAI_BASE_URL=https://api.z.ai/v1
ZAI_MODEL=your_model_name
```

---

## Usage

```bash
python run.py process "<youtube_url>"
```

**Example:**

```bash
python run.py process "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Output files are written to `outputs/<video_title>/`.

### Help

```bash
python run.py --help
python run.py process --help
```

---

## Web Application

### Run the web server

```bash
uvicorn app.web.app:app --reload
# or
python -m app.web.app
```

Then open **http://localhost:8000** in your browser.

### API endpoints

| Method | Path                 | Description                                                       |
| ------ | -------------------- | ----------------------------------------------------------------- |
| `GET`  | `/`                  | Arabic web UI                                                     |
| `POST` | `/process`           | `{"youtube_url": "…"}` → runs pipeline (or returns cached result) |
| `GET`  | `/result/{video_id}` | Fetch a previously-processed video by its sanitized folder name   |
| `GET`  | `/health`            | Liveness check                                                    |

The server processes one video at a time (semaphore). A second request while a pipeline is running returns **503 Busy**. Timeout is 2 hours.

---

## Project Structure

```
app/
  main.py          # Typer CLI commands
  config.py        # Pydantic settings (reads .env)
  pipeline.py      # Pipeline orchestrator
  schemas.py       # Pydantic data models
  services/
    downloader.py  # yt-dlp audio download
    audio.py       # ffmpeg normalisation
    transcriber.py # faster-whisper transcription
    zai_client.py  # Z.ai API adapter (isolated)
    generators.py  # Content generation helpers
  prompts/
    cleanup_arabic.txt
    tldr.txt
    twitter_thread.txt
    faq.txt
  utils/
    files.py       # Filesystem helpers, prompt loader
    logger.py      # Rich logging setup
  web/
    app.py         # FastAPI app, routes, error handling
    service.py     # Cache check + pipeline wrapper
    templates/
      index.html   # Arabic RTL web UI
    static/
      styles.css   # Dark theme CSS
      script.js    # Fetch + render + copy + collapse
tests/
  test_utils.py
outputs/           # Generated output directories
run.py             # Entry point
.env.example
requirements.txt
```

---

## Configuration Reference

| Variable        | Default               | Description                          |
| --------------- | --------------------- | ------------------------------------ |
| `ZAI_API_KEY`   | _(required)_          | Z.ai bearer token                    |
| `ZAI_BASE_URL`  | `https://api.z.ai/v1` | Z.ai API base URL                    |
| `ZAI_MODEL`     | _(required)_          | Model identifier                     |
| `WHISPER_MODEL` | `small`               | Whisper model size                   |
| `OUTPUT_DIR`    | `outputs`             | Where outputs are saved              |
| `TEMP_DIR`      | `.temp`               | Temporary download/processing folder |

---

## Error Handling

| Scenario             | Behaviour                                     |
| -------------------- | --------------------------------------------- |
| Invalid YouTube URL  | `DownloadError` raised, pipeline aborts       |
| Unavailable video    | `DownloadError` from yt-dlp, clear message    |
| ffmpeg not found     | `AudioProcessingError` with installation hint |
| Empty transcription  | `TranscriptionError` with diagnostic message  |
| Z.ai HTTP 429        | Retried up to 3× with exponential back-off    |
| Z.ai 5xx error       | `ZAIError` raised                             |
| Missing `.env` / key | Config validation error at startup            |

---

## Running Tests

```bash
python -m pytest tests/ -v
```
