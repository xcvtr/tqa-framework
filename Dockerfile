# syntax=docker/dockerfile:1
# TQA-M tester/paper/live container — один образ для всех трёх стадий

FROM python:3.11-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── Production image ────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Копируем зависимости от builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Копируем код проекта
COPY . .

ENTRYPOINT ["python", "-m", "strategies.trend_sma.runner"]
CMD ["--tickers", "CM,AL,SV", "--start", "2024-08-01", "--end", "2026-11-14"]
