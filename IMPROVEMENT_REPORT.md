# Technical Improvement Report — YouTube → Arabic Transcription Engine

## Codebase Inventory Analyzed

| Layer    | Files                                                                                                                                                                             |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Entry    | `run.py`, `app/main.py`                                                                                                                                                           |
| Config   | `app/config.py`, `.env`, `.env.example`                                                                                                                                           |
| Pipeline | `app/pipeline.py`                                                                                                                                                                 |
| Services | `app/services/downloader.py`, `app/services/audio.py`, `app/services/transcriber.py`, `app/services/generators.py`, `app/services/zai_client.py`, `app/services/openai_client.py` |
| Web      | `app/web/app.py`, `app/web/service.py`, `app/web/templates/index.html`, `app/web/static/script.js`                                                                                |
| Schemas  | `app/schemas.py`                                                                                                                                                                  |
| Utils    | `app/utils/files.py`, `app/utils/logger.py`                                                                                                                                       |
| Prompts  | 4 `.txt` template files                                                                                                                                                           |
| Tests    | `tests/test_generators.py`, `tests/test_utils.py`                                                                                                                                 |
| Recovery | `recover_zai.py`                                                                                                                                                                  |

---

## 1. Top 5 High-Impact Improvements

### 1.1 🔴 CRITICAL — API Keys Are Committed in Plain Text

`.env` contains live Z.ai and OpenAI API keys. There is **no `.gitignore` file** in the project. If this repo is ever pushed, those keys are instantly compromised.

**Fix (5 minutes):**

```
# Create .gitignore
.env
.venv/
__pycache__/
outputs/
.temp/
*.pyc
```

Then rotate both API keys immediately if this repo was ever shared.

---

### 1.2 Parallelize the 4 LLM Generation Calls

Currently in `app/pipeline.py` (lines 107–119), the 4 generation tasks (`clean_transcript`, `generate_tldr`, `generate_twitter_thread`, `generate_faq`) run **sequentially**. Each takes 10–30s. Total: 40–120s.

All 4 are independent — they all take the same `raw_text` input and produce independent outputs.

**Fix:** Use `concurrent.futures.ThreadPoolExecutor` to run all 4 in parallel:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

with ThreadPoolExecutor(max_workers=4) as executor:
    futures = {
        executor.submit(generators.clean_transcript, raw_text, **llm_kwargs): "clean",
        executor.submit(generators.generate_tldr, raw_text, **llm_kwargs): "tldr",
        executor.submit(generators.generate_twitter_thread, raw_text, **llm_kwargs): "thread",
        executor.submit(generators.generate_faq, raw_text, **llm_kwargs): "faq",
    }
    results = {}
    for future in as_completed(futures):
        results[futures[future]] = future.result()
```

**Expected impact:** Generation phase drops from ~90s to ~30s (the slowest single call).

---

### 1.3 Eliminate httpx Client Re-Creation Per Request

Both `app/services/zai_client.py` (lines 114–116) and `app/services/openai_client.py` (lines 72–73) create a **new `httpx.Client()` inside every `complete()` call**:

```python
with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
    response = client.post(endpoint, json=payload, headers=headers)
```

With retries × 4 generation tasks × fallback attempts, this creates 8–40 TCP connections with fresh TLS handshakes.

**Fix:** Use a module-level persistent client or pass a shared session:

```python
_client = httpx.Client(timeout=_REQUEST_TIMEOUT, http2=True)
```

**Expected impact:** ~200ms saved per call (TLS overhead), enables HTTP/2 connection multiplexing for parallel requests.

---

### 1.4 Background Job Queue for Web Layer

`app/web/app.py` (lines 138–142): the `/process` endpoint uses `run_in_executor(None, ...)` which blocks the default thread pool. The semaphore caps to 1 concurrent run, meaning the **entire server is functionally single-threaded** during processing.

Problems:

- Health checks still work, but any second `/process` returns 503
- A 90-minute pipeline ties up the thread pool's default 5 workers (one blocked, rest sleeping on `wait_for`)
- No job persistence — server restart = lost work

**Fix (incremental):**

1. **Phase 1:** Replace `run_in_executor` with a dedicated `ProcessPoolExecutor(max_workers=1)` to isolate CPU-heavy Whisper from the async event loop
2. **Phase 2:** Add a simple job queue (e.g. `asyncio.Queue` + background task) that accepts jobs and returns a `job_id` immediately. Client polls `/job/{id}/status`.

---

### 1.5 Massive Parameter Duplication in Generators

Every generator function in `app/services/generators.py` (lines 113–230) repeats the **exact same 7 keyword parameters**:

```python
provider, zai_api_key, zai_base_url, zai_model,
openai_api_key, openai_model, openai_base_url
```

This is repeated 4 times (clean, tldr, thread, faq) plus once in `_complete_with_provider`. The `recover_zai.py` script has its own incompatible calling convention. Adding a new provider means editing 5+ function signatures.

**Fix:** Create a `LLMConfig` dataclass:

```python
@dataclass
class LLMConfig:
    provider: str
    zai_api_key: str
    zai_base_url: str
    zai_model: str
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
```

Then every generator takes `(transcript: str, *, llm: LLMConfig)` — DRY and extensible.

---

## 2. Quick Wins (< 1 hour each)

| #   | Fix                                                   | Where                                                                                                                              | Impact                                                       |
| --- | ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| 1   | **Create `.gitignore`**                               | Project root                                                                                                                       | Prevents key leakage                                         |
| 2   | **Add `TRANSCRIPTION_MODE` to `.env`**                | `.env` — currently missing the new field                                                                                           | Backend fallback to `balanced` works, but explicit is better |
| 3   | **Fix test assertion mismatch**                       | `tests/test_generators.py` line 29: asserts `max_tokens == 1200` but `generators.py` sends `600`. Tests will fail.                 | Test correctness                                             |
| 4   | **Update `recover_zai.py` to use provider-aware API** | `recover_zai.py` lines 46–50: still uses old `api_key/base_url/model` kwargs that don't match current generator signatures         | Recovery tool is currently broken                            |
| 5   | **Add request timeout to frontend**                   | `app/web/static/script.js` line 30: `fetch("/process")` has no `AbortController` timeout — browser hangs indefinitely on slow runs | UX                                                           |
| 6   | **Pin dependency versions**                           | `requirements.txt` has no version pins — `pip install` could pull breaking changes                                                 | Reproducibility                                              |

---

## 3. Performance Fixes

| Bottleneck                                 | Where                                                                                                       | Current Cost                                   | Fix                                                                                 | Expected Gain                      |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------- | ---------------------------------- |
| **Sequential LLM calls**                   | `pipeline.py` lines 107–119                                                                                 | 4 × 15–30s = 60–120s                           | ThreadPoolExecutor parallel                                                         | ~70% reduction (→ ~30s)            |
| **httpx client re-creation**               | `zai_client.py` line 114, `openai_client.py` line 72                                                        | ~200ms TLS per call × 8+ calls                 | Shared persistent client                                                            | ~1.5s saved                        |
| **Z.ai retry backoff too aggressive**      | `zai_client.py` lines 67–71: `min=30, max=120` means first retry waits **30 seconds**                       | 30–120s wasted on transient errors             | Change to `min=4, max=30`                                                           | 26s saved per retry event          |
| **Whisper model loaded per call**          | `transcriber.py` lines 55–57: `WhisperModel()` is created fresh every transcription                         | ~5–15s model load time                         | Cache the model in a module-level singleton keyed by model name                     | ~10s saved on repeated runs        |
| **Full transcript sent to every LLM call** | `generators.py`: `{{transcript}}` inserts the entire raw text into 4 prompts                                | Token waste + slower inference for long videos | Send transcript once for cleanup; use cleaned+truncated version for TLDR/thread/FAQ | 30–50% token savings on generation |
| **Double cache-check on web**              | `app/web/app.py` lines 131–148 duplicates cache logic that `app/web/service.py` lines 111–128 also performs | `_resolve_title` calls YouTube API twice       | Remove duplication — let `service.py` handle cache exclusively                      | ~2s saved                          |

---

## 4. Architecture Improvements

### Current Architecture (Accurate)

```
run.py / uvicorn
  → app/main.py (CLI) or app/web/app.py (HTTP)
    → app/web/service.py (cache layer)
      → app/pipeline.py (orchestrator)
        → services/downloader.py  (yt-dlp)
        → services/audio.py       (ffmpeg subprocess)
        → services/transcriber.py (faster-whisper, CPU)
        → services/generators.py  (4× LLM calls)
          → services/zai_client.py   (httpx + tenacity)
          → services/openai_client.py (httpx + tenacity)
```

**Style:** Layered pipeline, clear separation of concerns. Good for a v1.

### Issues Found

| Issue                                           | Evidence                                                                                                  | Severity                                                         |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| **No abstraction boundary between LLM clients** | `zai_client.py` and `openai_client.py` are 95% identical code with different class names                  | Medium — adding a third provider means copy-pasting a third file |
| **Pipeline is a single monolithic function**    | `pipeline.py`: `run_pipeline()` is 150 lines handling download, normalize, transcribe, generate, and save | Medium — hard to test individual stages                          |
| **Web cache logic split across two files**      | Cache checking in both `app.py` AND `service.py`                                                          | Medium — bug risk from inconsistent checks                       |
| **`recover_zai.py` bypasses the pipeline**      | Uses its own incompatible calling convention, doesn't pass `provider` kwarg                               | Low — recovery tool is already broken                            |
| **No dependency injection**                     | `Settings()` is constructed inside functions; hard to test pipeline without `.env`                        | Low — works for current scale                                    |

### Recommended Refactors

1. **Extract `BaseLLMClient` protocol/ABC** — both clients implement `complete(user_prompt, *, api_key, model, ...)`. Define a protocol, instantiate the right client once based on provider choice.

2. **Split pipeline into stage functions** — each stage returns a typed result; stages can be tested independently and composed differently (e.g. skip download for local files).

3. **Consolidate cache logic** — single `CacheManager` class that both CLI and web call.

---

## 5. Product Features (High ROI)

| Feature                           | Effort | Value  | Description                                                                                                                                  |
| --------------------------------- | ------ | ------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **Local file input**              | 2–3h   | High   | Accept `.mp3`/`.mp4` paths — skip download step. Already 40% of the pipeline is post-download.                                               |
| **Batch URL processing**          | 3–4h   | High   | Accept a `.txt` file of URLs, process sequentially or in parallel. The pipeline already works per-URL.                                       |
| **Export to DOCX/PDF**            | 2h     | Medium | Use `python-docx` to combine all outputs into a single branded document.                                                                     |
| **Configurable output templates** | 2h     | Medium | The prompt templates in `app/prompts/` are text files — expose a UI to edit them without code changes.                                       |
| **Processing time tracking**      | 1h     | Medium | Record `started_at`/`completed_at` per stage in metadata. Currently no timing data is persisted — debugging slow runs requires reading logs. |
| **Multi-language transcription**  | 1h     | Low    | `transcriber.py` hardcodes `language="ar"` (line 62). Making it configurable enables English/French videos.                                  |

---

## 6. Risks / Weak Points

| Risk                                      | Severity    | Evidence                                                                                                                                                                                                                                                                                                                                 |
| ----------------------------------------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **API keys in repo without `.gitignore`** | 🔴 Critical | `.env` contains live keys; no `.gitignore` exists anywhere in the project                                                                                                                                                                                                                                                                |
| **No input size limit**                   | 🟠 High     | A 6-hour video will produce a massive transcript that exceeds LLM context windows (the `clean_transcript` prompt sends the entire raw text). No video duration check exists.                                                                                                                                                             |
| **Subprocess injection possible**         | 🟠 High     | `audio.py` lines 44–52: `str(input_path)` is passed to `subprocess.run` as a list element, which is safe — but `input_path` originates from yt-dlp's `%(title)s` template, meaning a crafted video title with shell metacharacters goes through `Path()` first. Currently safe because `subprocess.run([...])` doesn't use `shell=True`. |
| **Z.ai retry burns 30s minimum**          | 🟡 Medium   | `zai_client.py` line 71: `wait_exponential(min=30)` means even a brief 429 costs 30 seconds before retry. This was likely set during debugging and never reduced.                                                                                                                                                                        |
| **No rate limiting on `/process`**        | 🟡 Medium   | Anyone can hammer the endpoint. The semaphore prevents parallel runs but not DoS attempts that queue up resolve-title calls.                                                                                                                                                                                                             |
| **`shutil.rmtree` on temp dir**           | 🟡 Medium   | `pipeline.py` line 170: if two pipeline runs share the same `.temp` directory concurrently (e.g. CLI + web), one could delete the other's working files                                                                                                                                                                                  |
| **Tests call wrong max_tokens**           | 🟡 Medium   | `test_generators.py` line 29: `assert _kw["max_tokens"] == 1200` but actual code sends `600` for TLDR. Tests are probably not being run.                                                                                                                                                                                                 |
| **No `.gitignore`**                       | 🟡 Medium   | `.venv/`, `__pycache__/`, `outputs/` would all get committed                                                                                                                                                                                                                                                                             |

---

## 7. Recommended Roadmap (3 Steps)

### Step 1: Security & Stability (Do Today)

- [ ] Create `.gitignore` (`.env`, `.venv/`, `__pycache__/`, `outputs/`, `.temp/`)
- [ ] Rotate both API keys if repo was ever pushed
- [ ] Fix Z.ai retry backoff from `min=30` to `min=4` in `zai_client.py`
- [ ] Fix broken test assertions in `test_generators.py`
- [ ] Add video duration guard (reject > 3 hours or warn)
- [ ] Add `TRANSCRIPTION_MODE=balanced` to `.env`
- [ ] Fix `recover_zai.py` to use current generator API

### Step 2: Performance (Do This Week)

- [ ] Parallelize the 4 LLM generation calls with ThreadPoolExecutor
- [ ] Cache Whisper model in a module-level dict by model name
- [ ] Share httpx clients (persistent connections with HTTP/2)
- [ ] Use the cleaned transcript (not raw) as input for TLDR/thread/FAQ — shorter, better quality, fewer tokens
- [ ] Add per-stage timing to metadata for observability

### Step 3: Product Value (Do Next)

- [ ] Accept local file input (skip download)
- [ ] Add batch processing mode (list of URLs)
- [ ] Extract `LLMConfig` dataclass to eliminate parameter duplication
- [ ] Consolidate cache logic into a single `CacheManager`
- [ ] Add `/process` rate limiting (e.g., 5 req/min per IP)
