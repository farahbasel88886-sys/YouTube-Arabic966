# Personal VPS Deployment (Ubuntu + Docker)

This guide is optimized for personal use only.

## 1) Recommended VPS specs (simple)

- Minimum: 2 vCPU, 4 GB RAM, 40 GB SSD
- Recommended: 4 vCPU, 8 GB RAM, 80+ GB SSD

Why:

- Transcription is API-based, so no local Whisper model RAM load.
- ffmpeg still needs CPU and some memory for long files.
- Storage grows with uploads and generated outputs.

## 2) Ubuntu setup

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```

Log out and log in once so docker group is active.

## 3) Project + data directories

```bash
sudo mkdir -p /opt/yt-arabic/data/outputs
sudo mkdir -p /opt/yt-arabic/data/temp
sudo chown -R $USER:$USER /opt/yt-arabic

cd /opt/yt-arabic
git clone https://github.com/farahbasel88886-sys/YouTube-Arabic966.git app
cd app
```

## 4) Environment variables

Create `.env` from example:

```bash
cp .env.example .env
```

Set at least:

```dotenv
ZAI_API_KEY=...
ZAI_BASE_URL=https://api.z.ai/v1
ZAI_MODEL=...

OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_TRANSCRIPTION_MODEL=whisper-1

TRANSCRIPTION_MODE=balanced
MAX_UPLOAD_SIZE_MB=512

OUTPUT_DIR=/app/outputs
TEMP_DIR=/app/.temp
```

For bigger files, increase `MAX_UPLOAD_SIZE_MB` (for example 512 or 1024).

## 5) Run with Docker Compose

```bash
docker compose -f docker-compose.vps.yml up -d --build
docker compose -f docker-compose.vps.yml ps
docker compose -f docker-compose.vps.yml logs -f --tail=100
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## 6) Persistent storage paths

Host paths:

- `/opt/yt-arabic/data/outputs`
- `/opt/yt-arabic/data/temp`

Container paths:

- `/app/outputs`
- `/app/.temp`

All uploaded media temp files and generated output files persist on host disk.

## 7) Update / redeploy

```bash
cd /opt/yt-arabic/app
git pull
docker compose -f docker-compose.vps.yml up -d --build
```

## 8) Optional domain + HTTPS reverse proxy (Nginx)

Keep app bound to localhost (`127.0.0.1:8000`) and proxy through Nginx.

Example Nginx server block:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 1024M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Then issue TLS cert with Certbot.

## 9) Personal-use tuning notes

- This app processes one job at a time by design.
- API transcription removes local model memory spikes.
- Larger files mostly impact:
  - upload transfer time
  - ffmpeg processing time
  - host disk usage
