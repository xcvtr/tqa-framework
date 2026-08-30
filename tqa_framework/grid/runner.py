"""Grid sweep runner — параметрический sweep стратегий.

Поддерживает:
  - Python стратегии (--strategy + --params JSON с сетками)
  - YAML стратегии (--strategy-yaml + sweep секция в YAML)
"""
from __future__ import annotations

import itertools
import json
import logging
import sys
from typing import Optional

from tqa_framework.engine.backtester import Backtester
from tqa_framework.engine.pg_state import PGState

logger = logging.getLogger(__name__)


def sweep_python(
    pg: PGState,
    tickers: list[dict],
    days: int,
    risk_pct: float,
    tf_minutes: int,
    strategy_name: str,
    strategy_path: str,
    params_grid: dict,
    initial_equity: float = 100_000.0,
    ch_host: str = "",
    ch_db: str = "",
    **kwargs,
) -> dict:
    """Sweep параметров Python-стратегии (числовые сетки).

    params_grid: {"param": [val1, val2, ...], ...}
    Returns: {"best": {...}, "results": [{params, calmar, ...}, ...]}
    """
    keys = list(params_grid.keys())
    values_list = list(params_grid.values())

    best = {"calmar": -999.0, "params": {}}
    all_results = []

    total = 1
    for v in values_list:
        total *= len(v)

    print(f"Grid sweep: {total} комбинаций по {strategy_name}")
    print(f"  keys={keys}")
    print()

    for idx, combo in enumerate(itertools.product(*values_list)):
        params = dict(zip(keys, combo))
        print(f"  [{idx + 1}/{total}] {params}", end="", flush=True)

        bt = Backtester(
            tickers=tickers,
            days=days,
            risk_pct=risk_pct,
            tf_minutes=tf_minutes,
            strategy_name=strategy_name,
            strategy_params=params,
            initial_equity=initial_equity,
            pg=pg,
            ch_host=ch_host,
            ch_db=ch_db,
            strategy_path=strategy_path,
            save_results=False,
        )
        try:
            result = bt.run()
            calmar = result["summary"]["calmar_ratio"]
            ret = result["summary"]["total_return"]
            mdd = result["summary"]["mdd"]
            trades = result["summary"]["total_trades"]
        except Exception as e:
            print(f" → ERROR: {e}")
            continue

        print(f" → Return={ret:+.1f}% DD={mdd:.1f}% Trades={trades} Calmar={calmar:.2f}")

        entry = {"params": params, "calmar": calmar, "return": ret, "mdd": mdd, "trades": trades}
        all_results.append(entry)

        if calmar > best["calmar"]:
            best = {"calmar": calmar, "params": params}

    return {"best": best, "results": all_results}


def sweep_yaml(
    pg: PGState,
    tickers: list[dict],
    days: int,
    risk_pct: float,
    tf_minutes: int,
    yaml_path: str,
    initial_equity: float = 100_000.0,
    ch_host: str = "",
    ch_db: str = "",
    sweep_override: Optional[dict] = None,
    **kwargs,
) -> dict:
    """Sweep параметров YAML-стратегии из sweep секции.

    yaml_path: путь к .strategy.yaml
    sweep_override: если задан, переопределяет sweep params из YAML
    (позволяет задать JSON-сетку из CLI).
    """
    from tqa_framework.strategy_engine.parser import (
        load_sweep_config,
        build_sweep_combinations,
        template_yaml,
        load_strategy_from_string,
    )

    # Загрузить sweep конфиг
    sweep = load_sweep_config(yaml_path)

    # Если есть override из CLI, подменяем params
    if sweep_override:
        from tqa_framework.strategy_engine.models import SweepParam
        for p_name, p_def in sweep_override.items():
            if isinstance(p_def, list):
                sweep.params[p_name] = SweepParam(values=[float(v) for v in p_def])
            else:
                sweep.params[p_name] = SweepParam(values=[float(v) for v in [p_def]])

    combos = build_sweep_combinations(sweep)
    if not combos:
        print("Нет параметров для sweep (проверьте sweep: секцию в YAML)")
        return {"best": {"calmar": 0.0}, "results": []}

    # Загрузить raw YAML для темплейтинга
    raw_yaml = open(yaml_path).read()

    best = {"calmar": -999.0, "params": {}}
    all_results = []
    total = len(combos)

    print(f"Grid sweep: {total} комбинаций по YAML")
    print(f"  params={list(combos[0].keys())}")
    print()

    for idx, combo in enumerate(combos):
        print(f"  [{idx + 1}/{total}] {combo}", end="", flush=True)

        # Template YAML с текущей комбинацией
        yaml_str = template_yaml(raw_yaml, combo)

        # Записать во временный файл для передачи backtester'у
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".strategy.yaml", delete=False
        ) as f:
            f.write(yaml_str)
            tmp_path = f.name

        bt = Backtester(
            tickers=tickers,
            days=days,
            risk_pct=risk_pct,
            tf_minutes=tf_minutes,
            strategy_name="",
            strategy_params={},
            initial_equity=initial_equity,
            pg=pg,
            ch_host=ch_host,
            ch_db=ch_db,
            strategy_engine_path=tmp_path,
            save_results=False,
        )
        try:
            result = bt.run()
            calmar = result["summary"]["calmar_ratio"]
            ret = result["summary"]["total_return"]
            mdd = result["summary"]["mdd"]
            trades = result["summary"]["total_trades"]
        except Exception as e:
            print(f" → ERROR: {e}")
            import os
            os.unlink(tmp_path)
            continue

        import os
        os.unlink(tmp_path)

        print(f" → Return={ret:+.1f}% DD={mdd:.1f}% Trades={trades} Calmar={calmar:.2f}")

        entry = {"params": combo, "calmar": calmar, "return": ret, "mdd": mdd, "trades": trades}
        all_results.append(entry)

        if calmar > best["calmar"]:
            best = {"calmar": calmar, "params": combo}

    return {"best": best, "results": all_results}


def print_results(results: dict):
    """Вывести результаты sweep."""
    print("\n" + "=" * 60)
    best = results["best"]
    print(f"  Лучшая комбинация: {best['params']}")
    print(f"  Calmar:            {best['calmar']:.2f}")
    print("=" * 60)

    # Таблица топ-5
    all_r = sorted(results.get("results", []), key=lambda r: r["calmar"], reverse=True)
    if len(all_r) > 1:
        print(f"\n{'Top-5':>5} {'Параметры':>30} {'Return':>8} {'DD':>7} {'Trades':>7} {'Calmar':>7}")
        print("-" * 70)
        for i, r in enumerate(all_r[:5]):
            p_str = json.dumps(r["params"], ensure_ascii=False)
            print(f"{i + 1:>5} {p_str:>30} {r['return']:>+7.1f}% {r['mdd']:>6.1f}% "
                  f"{r['trades']:>7} {r['calmar']:>6.2f}")