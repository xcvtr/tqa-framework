# Checkpoint 009: CI Pipeline — Docker + CI-скрипты

## Создано
- `docker/Dockerfile` — python:3.11-slim, pip wheels (без gcc/apt)
- `docker/compose.yml` — добавлен `app` сервис (network_mode: host, PG/CH через localhost)
- `docker/ci/run.sh` — CI-раннер: git sync → build → backtest → regression check → Matrix alert
- `docker/ci/baseline.json` — эталон метрик LSR-CROSS (287.7%/11.2%/Calmar 25.6)
- `requirements.txt` — 13 зависимостей

## Баги, найденные и исправленные в CI-прогоне
1. Dockerfile: `musl-dev` недоступен на Debian slim + `apt-get update` падает (сеть не достаёт deb.debian.org) → убрал gcc, все пакеты — wheels
2. YAML схема: `name:` → `strategy:` (парсер ждёт `strategy` на top-level, не `name`)
3. `_run_lsr_mode` summary: нет `params` → KeyError → добавлен
4. `_run_lsr_mode` trades: нет `tags` → KeyError → добавлен `tags: "{}"`

## CI Pipeline
```
git pull → docker build → pg.sh start → compose run app → compare с baseline → alert при регрессии >5%
```

## Статус
Докер-образ билдится, бэктест в контейнере работает. Жду полный прогон (365d, 5 tickers, risk=0.08) для верификации.

## Git
commit и push после верификации полного прогона.