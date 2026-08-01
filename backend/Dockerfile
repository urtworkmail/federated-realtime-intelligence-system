# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Cache mount persists pip's downloaded wheels (torch et al.) across builds
# even when requirements.txt changes and invalidates this layer — without
# it (and without --no-cache-dir), every dependency change re-downloads
# every package from scratch, including the ~500MB torch wheel.
RUN --mount=type=cache,target=/root/.cache/pip \
    for i in 1 2 3; do \
      pip install --timeout=300 -r requirements.txt && break || sleep 15; \
    done

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
