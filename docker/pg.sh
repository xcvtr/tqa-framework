#!/usr/bin/env bash
# Утилиты для тестового PG
# Использование:
#   ./docker/pg.sh start    — запустить тестовый PG
#   ./docker/pg.sh stop     — остановить
#   ./docker/pg.sh reset    — удалить все данные и пересоздать
#   ./docker/pg.sh psql     — открыть psql
#   ./docker/pg.sh url      — показать строку подключения

set -e
cd "$(dirname "$0")"

case "${1:-help}" in
  start)
    docker compose -f compose.yml up -d pg
    echo "OK. Test PG on localhost:5433"
    echo "  URL: postgresql://postgres:***@localhost:5433/tqa"
    echo "  To stop: ./pg.sh stop"
    ;;
  stop)
    docker compose -f compose.yml down
    echo "Test PG stopped."
    ;;
  reset)
    docker compose -f compose.yml down -v
    docker compose -f compose.yml up -d pg
    echo "OK. Test PG reset (fresh)."
    ;;
  psql)
    docker compose -f compose.yml exec pg psql -U postgres -d tqa "${@:2}"
    ;;
  url)
    echo "postgresql://postgres:tqa@localhost:5433/tqa"
    ;;
  *)
    echo "Usage: ./pg.sh {start|stop|reset|psql|url}"
    ;;
esac
