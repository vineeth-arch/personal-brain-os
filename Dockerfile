# Brain Cockpit — one image, both processes (API + watcher loop).
# Multi-stage: whisper.cpp is built from source, so the same Dockerfile works
# on amd64 and arm64 (Pi 5, NAS, old laptop):
#   docker buildx build --platform linux/amd64,linux/arm64 -t brain-cockpit .
#
# All state lives in the /data volume (config.json, events.db, heartbeat,
# whisper models) and in the vault/inbox mounts — the image is stateless and
# rebuildable. API keys come from the environment only (compose env_file).

# ---- stage 1: whisper.cpp, static build ---------------------------------------
FROM debian:bookworm-slim AS whisper
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# pin a release tag so builds are reproducible
RUN git clone --depth 1 --branch v1.7.4 https://github.com/ggml-org/whisper.cpp /src
WORKDIR /src
# static libs → one self-contained whisper-cli binary, no .so scavenger hunt
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
    && cmake --build build --config Release -j"$(nproc)" \
    && strip build/bin/whisper-cli

# ---- stage 2: the cockpit frontend ---------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- stage 3: runtime ------------------------------------------------------------
FROM python:3.12-slim AS runtime
# ffmpeg converts captures for whisper; git powers the vault backup commits
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ api/
COPY pipeline/ pipeline/
COPY checks.json config.example.json ./
COPY --from=whisper /src/build/bin/whisper-cli /usr/local/bin/whisper-cli
COPY --from=web /web/dist web/dist
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# /data volume: config.json, events.db, .watcher-heartbeat, backups/, models/
ENV BRAIN_COCKPIT_ROOT=/data
VOLUME /data
EXPOSE 8000

# stdlib only — no curl dependency for the healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]

ENTRYPOINT ["/entrypoint.sh"]
