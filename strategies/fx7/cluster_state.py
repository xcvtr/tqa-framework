"""FX-7 проект: персистентное состояние кластеров в PG (multi_state.clusters).

Перенос JSON-состояния кластеров TQA-FX-TOP `engine/state/clusters.json` в PG
(колонка `clusters jsonb` таблицы `{schema}.multi_state`), чтобы live- и
rolling-backtest контуры делили ОДНО состояние между циклами (риск R3):
иначе разная частота детекции → разная плотность входов.

Движковый `pg_state.py` — IMMUTABLE, поэтому хранитель живёт в проекте fx7.
Логика update() повторяет `engine/cluster_manager.py` (JSON → PG), чтение/запись
через колонку multi_state.clusters.
"""
from __future__ import annotations

import json

import pandas as pd

DEFAULT_SCHEMA = "fx_top"  # live; dry-run → strategies_replay

DDL_MULTI_STATE_CLUSTERS = """
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.multi_state (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    equity NUMERIC DEFAULT 500.0,
    peak NUMERIC DEFAULT 500.0,
    balance NUMERIC DEFAULT 500.0,
    positions JSONB DEFAULT '[]'::jsonb,
    pending_signals JSONB DEFAULT '[]'::jsonb,
    strategy_config JSONB DEFAULT '{{}}'::jsonb,
    clusters JSONB DEFAULT '{{}}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE {schema}.multi_state ADD COLUMN IF NOT EXISTS clusters JSONB DEFAULT '{{}}'::jsonb;
"""

CLOSE_AFTER_HOURS = 36.0  # закрыть кластер, не виденный >36h


def _conn(conn=None):
    """Вернуть переданное подключение или открыть движковый PGState.conn."""
    if conn is not None:
        return conn
    from tqa_framework.engine.pg_state import PGState

    return PGState().conn


def ensure_multi_state_clusters(schema: str = DEFAULT_SCHEMA, conn=None):
    """DDL хранителя: схема + multi_state с колонкой clusters + seed-строка id=1."""
    c = _conn(conn)
    with c.cursor() as cur:
        cur.execute(DDL_MULTI_STATE_CLUSTERS.format(schema=schema))
        cur.execute(
            f"INSERT INTO {schema}.multi_state "
            f"(id, equity, peak, balance, positions, pending_signals, strategy_config, clusters) "
            f"VALUES (1, 500, 500, 500, '[]', '[]', '{{}}', '{{}}') "
            f"ON CONFLICT (id) DO NOTHING"
        )


def load_clusters(schema: str = DEFAULT_SCHEMA, conn=None) -> dict:
    """Загрузить все кластеры {id: data} из multi_state.clusters."""
    ensure_multi_state_clusters(schema, conn)
    c = _conn(conn)
    with c.cursor() as cur:
        cur.execute(f"SELECT clusters FROM {schema}.multi_state WHERE id = 1")
        row = cur.fetchone()
    if row and row[0]:
        return row[0]
    return {}


def save_clusters(clusters: dict, schema: str = DEFAULT_SCHEMA, conn=None) -> None:
    """Сохранить кластеры {id: data} в multi_state.clusters."""
    ensure_multi_state_clusters(schema, conn)
    c = _conn(conn)
    with c.cursor() as cur:
        cur.execute(
            f"UPDATE {schema}.multi_state SET clusters = %s, updated_at = NOW() WHERE id = 1",
            (json.dumps(clusters, default=str),),
        )


def _next_id(clusters: dict) -> str:
    if clusters:
        try:
            return str(max(int(k) for k in clusters) + 1)
        except (TypeError, ValueError):
            pass
    return "1"


def update(bar_time, centroids, zr: float = 0.8,
           schema: str = DEFAULT_SCHEMA, conn=None) -> dict:
    """Сопоставить центроиды с существующими кластерами, создать новые (как
    cluster_manager.update). Возвращает обновлённый dict кластеров (уже в PG)."""
    clusters = load_clusters(schema, conn)
    bar_str = str(bar_time)[:19]
    matched_ids = set()

    for centroid, ctype, vol in centroids:
        found = False
        for cid, c in list(clusters.items()):
            if c.get("status") != "active":
                continue
            if c["type"] != ctype:
                continue
            if abs(float(c["level"]) - float(centroid)) > zr * 0.5:
                continue
            c["last_seen"] = bar_str
            c["current_volume"] = float(vol)
            c["peak_volume"] = max(float(c.get("peak_volume", 0) or 0), float(vol))
            if c.get("first_seen") is None:
                c["first_seen"] = bar_str
            matched_ids.add(cid)
            found = True
            break

        if not found:
            cid = _next_id(clusters)
            clusters[cid] = {
                "id": cid, "level": float(centroid), "type": ctype,
                "first_seen": bar_str, "last_seen": bar_str,
                "entry_price": None, "peak_volume": float(vol),
                "current_volume": float(vol), "status": "active",
            }
            matched_ids.add(cid)

    # Закрыть кластеры, не виденные >CLOSE_AFTER_HOURS.
    current_dt = pd.Timestamp(bar_str)
    if current_dt.tz is not None:
        current_dt = current_dt.tz_localize(None)
    for cid, c in list(clusters.items()):
        if c.get("status") != "active":
            continue
        if cid in matched_ids:
            continue
        last_dt = pd.Timestamp(c["last_seen"])
        if last_dt.tz is not None:
            last_dt = last_dt.tz_localize(None)
        hours_since = (current_dt - last_dt).total_seconds() / 3600
        if hours_since > CLOSE_AFTER_HOURS:
            c["status"] = "closed"
            c["closed_at"] = bar_str

    save_clusters(clusters, schema, conn)
    return clusters


def migrate_from_json(json_path: str, schema: str = DEFAULT_SCHEMA, conn=None) -> int:
    """Мигрировать JSON-файл кластеров ({'clusters': {id: ...}}) → PG.

    Возвращает число перенесённых кластеров. Исходный файл не трогается.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    clusters = data.get("clusters", {}) if isinstance(data, dict) else {}
    save_clusters(clusters, schema, conn)
    return len(clusters)
