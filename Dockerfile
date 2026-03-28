FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Runtime deps:
# - ffmpeg: audio normalization/transcoding
# - nodejs: JS runtime support for yt-dlp extractor edge cases
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && ffmpeg -version

COPY app ./app
COPY run.py ./
COPY README.md ./

RUN mkdir -p outputs .temp

VOLUME ["/app/outputs", "/app/.temp"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,sys,urllib.request;port=os.environ.get('PORT','8000');u='http://127.0.0.1:'+port+'/health';sys.exit(0 if urllib.request.urlopen(u, timeout=3).status==200 else 1)"

CMD ["sh", "-c", "uvicorn app.web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
