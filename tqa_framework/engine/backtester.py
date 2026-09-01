"""Портфельный бэктестер — общий для всех стратегий.

Перебирает бары, вызывает detect() и tick() из стратегии.
Результаты сохраняет в PG (backtest.trades, equity_curve, summary).

Контракт стратегии:
    strategies/<name>/detect.py → detect(bars, config) → list[Signal]
        bars: list[dict] с ключами ts, open, high, low, close, volume
        config: dict (полный конфиг из YAML)

    strategies/<name>/tick.py → evaluate_position(position, price, config) → str
        position: Position (exchange_base)
        price: float (текущая цена)
        returns: 'hold' | 'sl' | 'tp' | 'trailing' | 'timeout'
"""
from __future__ import annotations

import importlib
import json
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

from tqa_framework.engine.exchange_base import Position, Signal
from tqa_framework.engine.detect import load_m1_from_ch, resample_bars, dedup_signals
from tqa_framework.engine.risk import calc_risk_mult, calc_contracts, sma_50_trend
from tqa_framework.engine.pg_state import PGState

logger = logging.getLogger(__name__)


class Backtester:
    """Универсальный бэктестер. Работает с любой стратегией."""

    def __init__(
        self,
        tickers: list[dict],
        days: int,
        risk_pct: float,
        tf_minutes: int,
        strategy_name: str = "",
        strategy_params: Optional[dict] = None,
        initial_equity: float = 100_000.0,
        pg: Optional[PGState] = None,
        ch_host: str = "",
        ch_db: str = "",
        max_conc: int = 6,
        strategy_path: str = "",
        strategy_engine_path: str = "",  # путь к .strategy.yaml
        market: str = "moex",
        point: Optional[dict] = None,
        pip_value: float = 10.0,
        commission_per_lot: float = 0.0,
        spread_points: Optional[dict] = None,
        swap_per_night: Optional[dict] = None,
        detect_every: int = 1,
        save_results: bool = True,
        end_time: str = "",
        slippage_ticks: int = 0,
    ):
        """Бэктестер.

        strategy_name — имя Python-стратегии (через importlib detect/tick)
        strategy_engine_path — путь к .strategy.yaml (альтернатива, YAML-declarative)

        market='moex'  — pnl = (exit-entry) × quantity (контракты, комиссия 8₽)
        market='forex' — pnl = (exit-entry)/point × pip_value × lot + комиссия + своп

        point: dict {symbol: point} — пункт MT5 (EURUSD=1e-5, USDJPY=0.001, XAU=0.01)
        pip_value: $ за пункт на 1 лот (AlfaForex: 10.0)
        commission_per_lot: $ за 1 лот round-trip
        spread_points: dict {symbol: spread в пунктах} — вычитается из pnl
        swap_per_night: dict {symbol: (long_swap, short_swap) в пунктах} — за ночь
        """
        self.tickers = tickers
        self.days = days
        self.risk_pct = risk_pct
        self.tf_minutes = tf_minutes
        self.strategy_name = strategy_name
        self.strategy_params = strategy_params or {}
        self._raw_yaml = None  # raw YAML dict (заполняется при загрузке через strategy_engine)
        # data_source ДО for-loop — чтобы --params мог переопределить
        self.data_source = self.strategy_params.get('data_source', 'bars')  # 'bars' | 'mt5_continuous'
        # Apply all strategy_params keys as attributes (для LSR-CROSS и др.)
        for k, v in self.strategy_params.items():
            setattr(self, k, v)
        self.pyramid_max = self.strategy_params.get("pyramid_max", 1)
        self.strategy_path = strategy_path
        self.initial_equity = initial_equity
        self.pg = pg or PGState()
        self.ch_host = ch_host
        self.ch_db = ch_db
        self.max_conc = max_conc
        self.market = market
        self.detect_every = detect_every
        self.save_results = save_results
        self.end_time_override = end_time
        self.slippage_ticks = slippage_ticks
        self.point = point or {}
        self.pip_value = pip_value
        self.commission_per_lot = commission_per_lot
        self.spread_points = spread_points or {}
        self.swap_per_night = swap_per_night or {}

        self._detect_fn: Optional[Callable] = None
        self._tick_fn: Optional[Callable] = None
        self.strategy_engine_path = strategy_engine_path
        self._engine_config = None
        # ГО контрактов (MOEX): {symbol: go} из ticker_cfg['go']
        self.ticker_go = {t.get('symbol'): t.get('go', 0) for t in tickers if t.get('go')}
        # Спецификации MOEX: {symbol: ms/sp} из ticker_cfg (min_step, step_price)
        self.ticker_ms = {t.get('symbol'): t.get('ms', 0) for t in tickers if t.get('ms')}
        self.ticker_sp = {t.get('symbol'): t.get('sp', 0) for t in tickers if t.get('sp')}

    def _load_strategy(self):
        """Загрузить detect/tick из стратегии через importlib.

        Если указан strategy_path — добавляет его в sys.path.
        Поддерживает два формата:
          1) path/strategies/<name>/detect.py (корень репозитория)
          2) path/<name>/detect.py              (папка strategies напрямую)
        """
        import os, sys
        path = getattr(self, 'strategy_path', None)
        if path and os.path.isdir(path):
            sys.path.insert(0, path)

        # Попробовать strategies.<name>.detect
        for mod_name in [f"strategies.{self.strategy_name}",
                          self.strategy_name]:
            try:
                detect_mod = importlib.import_module(f"{mod_name}.detect")
                self._detect_fn = detect_mod.detect
                tick_mod = importlib.import_module(f"{mod_name}.tick")
                self._tick_fn = tick_mod.evaluate_position
                return
            except ImportError:
                continue

        raise ImportError(
            f"Стратегия '{self.strategy_name}' не найдена. "
            f"Проверялись пути: strategies/{self.strategy_name} и {self.strategy_name}. "
            f"strategy_path={path}. "
            f"Ожидается: strategies/{self.strategy_name}/detect.py и tick.py"
        )

    def _load_strategy_engine(self):
        """Загрузить strategy_engine из YAML (v2 API).

        Использует parser.load_strategy() и runtime.evaluate().
        Приоритет: 1) PG (если name найден), 2) файл (если указан путь).
        """
        try:
            from tqa_framework.strategy_engine.parser import load_strategy, load_strategy_from_pg, load_strategy_from_string
            from tqa_framework.strategy_engine.runtime import evaluate
        except ImportError as e:
            logger.warning("strategy_engine не импортирован (%s) — fallback на importlib detect/tick", e)
            return

        try:
            path = self.strategy_engine_path
            if not path:
                raise ValueError("strategy_engine_path is empty")

            # 1) Попробовать PG (если имя без слешей и расширения .yaml)
            if "/" not in path and not path.endswith(".yaml"):
                pg_yaml = self.pg.load_strategy_yaml(path)
                if pg_yaml:
                    strategy = load_strategy_from_pg(path, pg_yaml)
                    logger.info("Strategy Engine загружен из PG: %s", path)
                    # Load close params + raw YAML from PG string too
                    import yaml as _yaml
                    self._raw_yaml = _yaml.safe_load(pg_yaml)
                    if isinstance(self._raw_yaml, dict) and 'close' in self._raw_yaml:
                        for _k, _v in self._raw_yaml['close'].items():
                            if _k not in self.strategy_params:
                                self.strategy_params[_k] = _v
                    # NEW: Load risk section (pyramiding, exit_timeout_hours)
                    if isinstance(self._raw_yaml, dict) and 'risk' in self._raw_yaml:
                        _risk = self._raw_yaml['risk']
                        # Flatten top-level risk keys (exit_timeout_hours, per_symbol_risk, etc.)
                        for _k, _v in _risk.items():
                            if _k not in self.strategy_params and not isinstance(_v, dict):
                                self.strategy_params[_k] = _v
                        # Flatten pyramiding sub-keys
                        if 'pyramiding' in _risk and isinstance(_risk['pyramiding'], dict):
                            for _pk, _pv in _risk['pyramiding'].items():
                                _pk_full = f"pyramiding_{_pk}"  # → pyramiding_trigger, pyramiding_add_multiplier
                                if _pk_full not in self.strategy_params:
                                    self.strategy_params[_pk_full] = _pv
                else:
                    raise FileNotFoundError(f"Стратегия '{path}' не найдена ни в PG, ни как файл")
            else:
                # 2) Попробовать как путь к файлу
                strategy = load_strategy(path)
                # Also load close params from raw YAML
                import yaml as _yaml
                self._raw_yaml = _yaml.safe_load(open(path))
                if isinstance(self._raw_yaml, dict) and 'close' in self._raw_yaml:
                    for _k, _v in self._raw_yaml['close'].items():
                        if _k not in self.strategy_params:
                            self.strategy_params[_k] = _v
                # NEW: Load risk section (pyramiding, exit_timeout_hours)
                if isinstance(self._raw_yaml, dict) and 'risk' in self._raw_yaml:
                    _risk = self._raw_yaml['risk']
                    # Flatten top-level risk keys (exit_timeout_hours, per_symbol_risk, etc.)
                    for _k, _v in _risk.items():
                        if _k not in self.strategy_params and not isinstance(_v, dict):
                            self.strategy_params[_k] = _v
                    # Flatten pyramiding sub-keys
                    if 'pyramiding' in _risk and isinstance(_risk['pyramiding'], dict):
                        for _pk, _pv in _risk['pyramiding'].items():
                            _pk_full = f"pyramiding_{_pk}"  # → pyramiding_trigger, pyramiding_add_multiplier
                            if _pk_full not in self.strategy_params:
                                self.strategy_params[_pk_full] = _pv
        except Exception as e:
            logger.warning("Ошибка загрузки YAML (%s) — fallback на importlib detect/tick", e)
            return

        self._engine_config = strategy
        strategy_name = strategy.name

        def engine_detect_fn(bars, config):
            """Детект через runtime.evaluate — только сигналы с последнего бара."""
            state = {
                "symbol": config.get("symbol", "?"),
                "positions": [],
                "ts": bars[-1].get("ts", "") if bars else "",
            }
            last_ts = bars[-1].get("ts", "") if bars else ""
            symbol = config.get("symbol", "?")
            ext_idx = getattr(self, 'sym_ext_idx', {})
            current_ext_signals = ext_idx.get(symbol, {}).get(last_ts, [])
            signals = evaluate(bars, strategy, state, external_signals=current_ext_signals, last_bar_only=True)
            result = []
            from tqa_framework.engine.exchange_base import Signal as BTSignal
            for sig in signals:
                if sig.action == "open_long" and sig.timestamp == last_ts:
                    result.append(BTSignal(
                        symbol=state["symbol"],
                        direction="LONG",
                        price=sig.price,
                        timestamp=sig.timestamp,
                        strategy=strategy_name,
                        score=1.0,
                    ))
                elif sig.action == "open_short" and sig.timestamp == last_ts:
                    result.append(BTSignal(
                        symbol=state["symbol"],
                        direction="SHORT",
                        price=sig.price,
                        timestamp=sig.timestamp,
                        strategy=strategy_name,
                        score=1.0,
                    ))
            return result

        def engine_tick_fn(pos, price, config, bar_time=None):
            """Тик через runtime.evaluate — проверяет close-сигналы.
            
            Использует полное окно баров символа из sym_bars/sym_idx,
            чтобы exit-условия (zscore, bars_held, sma) работали корректно.
            """
            sym = getattr(pos, "symbol", "?")
            bars = getattr(self, 'sym_bars', {}).get(sym, [])
            idx = getattr(self, 'sym_idx', {}).get(sym, {}).get(bar_time)
            if idx is not None and idx < len(bars):
                window = bars[:idx + 1]
            else:
                window = [{"close": price, "ts": bar_time or ""}]
            state = {
                "symbol": sym,
                "positions": [pos] if hasattr(pos, 'direction') else [],
                "ts": bar_time or "",
            }
            sigs = evaluate(window, strategy, state, last_bar_only=True)
            for sig in sigs:
                pos_dir = getattr(pos, "direction", "")
                if sig.action == "close_all":
                    return "tp"
                if sig.action == "close_long" and pos_dir == "LONG":
                    return "tp"
                if sig.action == "close_short" and pos_dir == "SHORT":
                    return "tp"
            return "hold"

        self._detect_fn = engine_detect_fn
        self._tick_fn = engine_tick_fn
        self.strategy_name = strategy_name
        logger.info("Strategy Engine загружен: %s (%s)", strategy_name, self.strategy_engine_path)

    def run(self) -> dict:
        """Запустить бэктест для всех тикеров.

        Returns:
            dict с ключами:
                summary — сводка
                trades — список сделок
                equity_curve — кривая капитала
        """
        if self.strategy_engine_path:
            self._load_strategy_engine()
            if self._engine_config is None:
                # graceful degradation не удался — ошибка
                raise RuntimeError(
                    f"strategy_engine не загрузился: {self.strategy_engine_path}. "
                    "Убедитесь что файл существует и strategy_engine установлен."
                )
        else:
            self._load_strategy()
        self.pg.ensure_schemas()
        self.pg.ensure_tables_backtest()

        # Зафиксировать конец периода — последний бар в CH (ИЗ ТОГО ЖЕ ИСТОЧНИКА, что бары!)
        import requests as _req
        src = getattr(self, 'data_source', 'bars')
        if self.ch_db == "forex":
            _sym0 = self.tickers[0]["symbol"]
            _q = (f"SELECT max(time) FROM forex.bars WHERE symbol='{_sym0}' FORMAT TabSeparated")
        elif self.ch_db == "crypto":
            _sym0 = self.tickers[0]["symbol"]
            _q = f"SELECT max(timestamp) FROM crypto.klines WHERE symbol='{_sym0}' AND interval='5m' FORMAT TabSeparated"
        elif src == "mt5_continuous":
            _q = f"SELECT max(bt) FROM {self.ch_db}.mt5_continuous FORMAT TabSeparated"
        elif src == "mt5_futures_d1":
            _q = f"SELECT max(bt) FROM {self.ch_db}.mt5_futures WHERE tf='D1' FORMAT TabSeparated"
        elif src == "mt5_futures_h1":
            _q = f"SELECT max(bt) FROM {self.ch_db}.mt5_futures WHERE tf='H1' FORMAT TabSeparated"
        else:
            _q = f"SELECT max(bt) FROM {self.ch_db}.bars FORMAT TabSeparated"
        _r = _req.get(self.ch_host, params={"query": _q}, timeout=15)
        _r.raise_for_status()
        ch_latest = _r.text.strip()
        end_time = self.end_time_override if self.end_time_override else (ch_latest if ch_latest else "")
        if end_time:
            logger.info("CH последний бар: %s", end_time)
        else:
            logger.warning("Не удалось определить последний бар, используется now()")

        equity = self.initial_equity
        peak = equity
        dd = 0.0
        self._realized = 0.0
        all_trades: list[dict] = []
        all_equity: list[dict] = []
        positions: list[dict] = []  # открытые позиции

        # ── ПОРТФЕЛЬ: загружаем ВСЕ символы, синхронизируем по времени ──
        sym_bars = {}   # symbol -> list[dict] M1
        sym_tf = {}     # symbol -> resampled bars
        sym_rpct = {}   # symbol -> risk_pct
        for ticker_cfg in self.tickers:
            symbol = ticker_cfg["symbol"]
            tf = ticker_cfg.get("tf", self.tf_minutes)
            rpct = ticker_cfg.get("risk_pct", self.risk_pct)

            logger.info("Загрузка %s за %d дней до %s...",
                        symbol, self.days, end_time or "now()")
            bars = load_m1_from_ch(
                symbol, self.days * 24,
                self.ch_host, self.ch_db,
                end_time=end_time,
                source=getattr(self, 'data_source', 'bars'),
            )
            if not bars:
                logger.warning("Нет данных для %s", symbol)
                continue
            tf_bars = resample_bars(bars, tf)
            logger.info("  → %d баров на TF=%d", len(tf_bars), tf)
            sym_bars[symbol] = tf_bars
            sym_tf[symbol] = tf
            sym_rpct[symbol] = rpct

        if not sym_bars:
            logger.error("Нет данных ни по одному символу")
            return {"summary": {}, "trades": [], "equity_curve": []}

        # Загрузить внешние LSR сигналы
        all_external_signals = []
        if self.ch_db == "crypto":
            for symbol in sym_bars.keys():
                # +720h warmup для z-score rolling window (как в Python lsr_cross.py)
                signals = self.load_lsr_data(symbol, hours=self.days * 24 + 720, end_time=end_time)
                if signals:
                    all_external_signals.extend(signals)
                    logger.info(f"Загружено {len(signals)} LSR сигналов для {symbol}")

        # ── Apply exclude syms/hours/DOW from YAML (по ИСХОДНОМУ ts события!) ──
        # Live detect.py фильтрует по ts события кросса (до ремапа на tf-бар).
        # Ремап ниже сдвигает ts на следующий tf-бар → фильтр по ремапленному ts
        # терял бы пограничные сигналы (10:56–10:59 → 11:00-бар отсекался ошибочно).
        if self._engine_config and self.strategy_engine_path:
            try:
                import yaml as _yl
                if getattr(self, '_raw_yaml', None):
                    _raw_yaml = self._raw_yaml
                else:
                    with open(self.strategy_engine_path) as _f:
                        _raw_yaml = _yl.safe_load(_f)
                _excl = _raw_yaml.get("exclude", {})
                _excl_syms = set(_excl.get("syms", []) or [])
                _excl_hours = set(_excl.get("hours", []) or [])
                _excl_dow = set(_excl.get("dow", []) or [])
                if _excl_syms or _excl_hours or _excl_dow:
                    _before = len(all_external_signals)
                    _filtered = []
                    for _sig in all_external_signals:
                        if _sig.get("symbol") in _excl_syms:
                            continue
                        _ts = _sig.get("ts", "")
                        try:
                            from datetime import datetime as _dt
                            _d = _dt.fromisoformat(_ts.replace("T", " ").split(".")[0])
                            if _d.hour in _excl_hours:
                                continue
                            if _d.weekday() in _excl_dow:
                                continue
                        except (ValueError, AttributeError):
                            pass
                        _filtered.append(_sig)
                    all_external_signals = _filtered
                    logger.info("Exclude syms=%s hours=%s DOW=%s: %d → %d сигналов",
                                _excl_syms, _excl_hours, _excl_dow, _before, len(all_external_signals))
            except Exception as _ex:
                logger.warning("Не удалось применить exclude фильтры из YAML: %s", _ex)

        # ── REMAP: LSR event timestamps → next 5m bar timestamp ──
        # Python simulate_trade() uses np.searchsorted(px_t5, ev_ts, side='right')
        # to find the FIRST 5m bar AFTER each LSR cross event.
        # YAML engine_detect_fn needs external signal ts to match a bar ts.
        # CRITICAL: normalize timestamps to same format — resample_bars uses 'T'
        # format (2026-08-06T00:00:00), but LSR event timestamps use space
        # format (2026-08-06 00:00:00.000). String comparison treats 'T' > ' ',
        # causing ALL signals on a day to map to the midnight bar. Fix: normalize.
        import bisect
        for sig in all_external_signals:
            sym = sig.get("symbol")
            ts = sig.get("ts")
            if sym and ts and sym in sym_bars:
                bar_times = [b["ts"] for b in sym_bars[sym]]
                # Normalize to 'T' format AND strip millis for consistent string comparison
                # LSR: "2026-08-06 12:35:00.000" → "2026-08-06T12:35:00.000" → "2026-08-06T12:35:00"
                ts_norm = ts.replace(" ", "T")
                if "." in ts_norm:
                    ts_norm = ts_norm.split(".")[0]
                idx = bisect.bisect_right(bar_times, ts_norm)
                if idx < len(bar_times):
                    sig["ts"] = bar_times[idx]
                    sig["timestamp"] = bar_times[idx]

        # Индекс сигналов по времени
        sym_ext_idx = {}
        for sig in all_external_signals:
            sym = sig.get("symbol")
            ts = sig.get("ts")
            if sym and ts:
                if sym not in sym_ext_idx:
                    sym_ext_idx[sym] = {}
                if ts not in sym_ext_idx[sym]:
                    sym_ext_idx[sym][ts] = []
                sym_ext_idx[sym][ts].append(sig)

        self.sym_ext_idx = sym_ext_idx
        self.sym_bars = sym_bars

        # ── LSR-CROSS pre-computed mode: detect by YAML action ──
        if self._raw_yaml and isinstance(self._raw_yaml, dict):
            _signals = self._raw_yaml.get('signals', [])
            if any(s.get('then') == 'lsr_execute' for s in _signals):
                logger.info("LSR-CROSS mode detected (action=lsr_execute), running pre-compute + 1m MTM")
                self._run_lsr_mode(sym_bars, end_time, all_external_signals)
                if hasattr(self, '_lsr_result') and self._lsr_result:
                    logger.info(f"LSR mode complete: {self._lsr_result['summary']['total_return']}% / {self._lsr_result['summary']['mdd']}% DD / {self._lsr_result['summary']['total_trades']} trades")
                    return self._lsr_result

        # Общий временной индекс: union всех bar_time (отсортированный)
        all_times = sorted({b["ts"] for bars in sym_bars.values() for b in bars})
        # индекс бара по времени для каждого символа
        sym_idx = {}
        for symbol, bars in sym_bars.items():
            sym_idx[symbol] = {b["ts"]: i for i, b in enumerate(bars)}

        self.sym_idx = sym_idx

        detect_lookback = getattr(self, "detect_lookback", 600)
        _last_prog = 0
        # ── ЕДИНЫЙ ПОРТФЕЛЬНЫЙ ЦИКЛ: каждый bar_time обрабатываем ВСЕ символы ──
        for ti, bar_time in enumerate(all_times):
            if ti // 50000 > _last_prog:
                _last_prog = ti // 50000
                print(f"  портфель: бар {ti}/{len(all_times)}", flush=True)

            # текущие цены всех символов на этом bar_time
            prices = {}
            for symbol, bars in sym_bars.items():
                idx = sym_idx[symbol].get(bar_time)
                if idx is not None and idx < len(bars):
                    prices[symbol] = bars[idx]["close"]

            # ── 1. Тик: проверка ВСЕХ открытых позиций (по текущим ценам) ──
            # Ролл контракта: бар с roll_gap=1 — цена сменилась на НОВЫЙ контракт.
            # Открытые позиции закрываем по ПРЕДЫДУЩЕЙ цене (до гэпа) — ролл не трогает PnL.
            # NEW: Hold timeout check
            _timeout_hours = getattr(self, 'exit_timeout_hours', 0) or self.strategy_params.get('exit_timeout_hours', 0)
            for pos_dict in [p for p in positions if not p.get("closed")]:
                sym = pos_dict["symbol"]
                price = prices.get(sym)
                # Track bars held for timeout check
                pos_dict['bars_held'] = pos_dict.get('bars_held', 0) + 1
                if _timeout_hours > 0 and price is not None and pos_dict['bars_held'] * self.tf_minutes >= _timeout_hours * 60:
                    self._close_position(pos_dict, price, bar_time, "timeout", all_trades)
                    continue
                idx_now = sym_idx[sym].get(bar_time)
                bar_is_roll = False
                prev_price = None
                if idx_now is not None and idx_now < len(sym_bars[sym]):
                    bar_is_roll = bool(sym_bars[sym][idx_now].get("roll_gap"))
                    if idx_now > 0:
                        prev_price = sym_bars[sym][idx_now - 1]["close"]
                if bar_is_roll and prev_price is not None:
                    # Закрыть по предыдущей цене (без ролл-гэпа)
                    self._close_position(pos_dict, prev_price, bar_time, "roll_gap", all_trades)
                    continue
                price = prices.get(sym)
                if price is None:
                    continue
                pos = Position(
                    symbol=sym,
                    direction=pos_dict["direction"],
                    entry_price=pos_dict["entry_price"],
                    current_price=price,
                    quantity=pos_dict["quantity"],
                    sl_price=pos_dict.get("sl_price"),
                    tp_price=pos_dict.get("tp_price"),
                    trail_activation=pos_dict.get("trail_activation"),
                    trail_distance=pos_dict.get("trail_distance", 0.0),
                    entry_time=pos_dict.get("entry_time"),
                )
                # Прокидываем day_net текущего бара в tick (для OI exit_thr)
                cur_dn = None
                bidx = sym_idx[sym].get(bar_time)
                if bidx is not None and 0 <= bidx < len(sym_bars[sym]):
                    cur_dn = sym_bars[sym][bidx].get('day_net')
                pos.__dict__['day_net'] = cur_dn
                pos.__dict__['bar_time'] = bar_time
                # Прокидываем накопленное состояние позиции (bars_held, peak_fav и т.п.)
                for k in ('bars_held', 'peak_fav', 'dow_peak', 'pyra_added'):
                    if k in pos_dict:
                        pos.__dict__[k] = pos_dict[k]
                try:
                    reason = self._tick_fn(pos, price, self.strategy_params, bar_time)
                except TypeError:
                    # совместимость: tick с 3 аргументами (FX TOP1 и др.)
                    reason = self._tick_fn(pos, price, self.strategy_params)

                # ── SL/TP/Trail check (из YAML close params) ──
                if reason == "hold":
                    reason = self._check_sl_tp_trail(pos_dict, price)

                # Сохраняем состояние обратно в pos_dict (Position пересоздаётся каждый тик)
                for k in ('bars_held', 'peak_fav', 'dow_peak', 'pyra_added', 'quantity'):
                    if hasattr(pos, k) and k in pos.__dict__:
                        pos_dict[k] = pos.__dict__[k]
                if reason != "hold":
                    self._close_position(pos_dict, price, bar_time, reason, all_trades)

            # ── 2. Детект: ВСЕ символы на этом баре ──
            if ti >= 10 and (ti % self.detect_every == 0 or ti == len(all_times) - 1):
                for symbol, bars in sym_bars.items():
                    idx = sym_idx[symbol].get(bar_time)
                    if idx is None or idx < 10:
                        continue
                    # Fast-path: skip if no external signals for this symbol·bar
                    ext_sigs_at_bar = getattr(self, 'sym_ext_idx', {}).get(symbol, {}).get(bar_time, [])
                    if not ext_sigs_at_bar and getattr(self, 'sym_ext_idx', {}):
                        continue
                    window = bars[max(0, idx - detect_lookback + 1):idx + 1]
                    signals = self._detect_fn(window, {
                        **self.strategy_params,
                        "symbol": symbol,
                        "tf": sym_tf[symbol],
                        "risk_pct": sym_rpct[symbol],
                    })
                    for sig in signals:
                        if len([p for p in positions if not p.get("closed")]) >= self.max_conc:
                            break
                        # Пирамидинг: до pyramid_max позиций на символ (одного направления)
                        open_sym = [p for p in positions
                                    if not p.get("closed") and p["symbol"] == sig.symbol]
                        if len(open_sym) >= self.pyramid_max:
                            continue
                        if open_sym and any(p["direction"] != sig.direction for p in open_sym):
                            continue  # не открываем противоположное направление на тот же символ
                        if not open_sym and any(not p.get("closed") and p["symbol"] == sig.symbol
                                                for p in positions):
                            # обычный режим (pyramid_max=1): одна позиция на символ
                            if self.pyramid_max == 1:
                                continue
                        # DEDUP (опционально, только если YAML просит): skip if position
                        # already opened for this symbol on current trading day.
                        # Включается параметром `dedup_per_day: true` — не влияет на
                        # другие стратегии (stop_hunt, lsr_cross), у которых флаг выкл.
                        if self.strategy_params.get("dedup_per_day", False):
                            cur_date = bar_time[:10] if bar_time else ""
                            if cur_date:
                                has_today = any(
                                    p["symbol"] == sig.symbol and p.get("entry_time", "").startswith(cur_date)
                                    for p in positions
                                )
                                if has_today:
                                    continue
                        # КОНКУРЕНЦИЯ ЗА КАПИТАЛ
                        active = [p for p in positions if not p.get("closed")]
                        used_risk = sum(p.get("_risk_amount", 0.0) for p in active)
                        free_equity = max(equity - used_risk, equity * 0.1)
                        pos = self._open_position(sig, free_equity, sym_rpct[symbol], bar_time,
                                                  dd_pct=dd, positions=positions)
                        if pos:
                            positions.append(pos)

            # ── 3. Equity: MTM всех позиций по текущим ценам ──
            active = [p for p in positions if not p.get("closed")]
            unrealized = 0.0
            for p in active:
                price = prices.get(p["symbol"])
                if price is not None:
                    unrealized += self._mtm(p, price)
                else:
                    unrealized += self._mtm_last(p)
            cash_equity = self.initial_equity + self._realized
            mtm_equity = cash_equity + unrealized
            equity = mtm_equity
            peak = max(peak, equity)
            dd = (equity - peak) / peak * 100 if peak > 0 else 0

            all_equity.append({
                "strategy": self.strategy_name,
                "bar_time": bar_time,
                "equity": round(mtm_equity, 2),
                "cash_equity": round(cash_equity, 2),
                "drawdown": round(dd, 2),
            })

        # Закрыть оставшиеся позиции по последней цене
        if all_equity:
            last_bar_time = all_equity[-1]["bar_time"]
        else:
            last_bar_time = ""
        for pos in positions:
            if not pos.get("closed"):
                last_price = pos.get("_last_price", pos["entry_price"])
                self._close_position(pos, last_price, last_bar_time, "timeout", all_trades)

        # --- Итоги ---
        summary = self._calc_summary(all_trades, all_equity, equity)

        # Сохранить в PG (summary первой, чтобы получить id)
        if getattr(self, "save_results", True):
            summary_id = self.pg.save_summary(summary)
            self.pg.save_trades_batch(all_trades, summary_id)
            self.pg.save_equity_points(all_equity, summary_id)
        else:
            summary_id = 0

        return {"summary": summary, "summary_id": summary_id,
                "trades": all_trades, "equity_curve": all_equity}

    # --- Внутренние методы ---

    def _open_position(
        self, signal: Signal, equity: float, risk_pct: float, bar_time: str,
        dd_pct: float = 0.0, positions: Optional[list] = None,
    ) -> Optional[dict]:
        """Открыть позицию по сигналу."""
        risk_mult = calc_risk_mult(equity)
        # MOEX: risk_mult=1 (для форекс он душит: 24000/eq → 0.2 при eq>120K)
        if self.market == 'moex':
            risk_mult = 1.0
        # DD-factor: риск снижается при просадке (защита капитала)
        dd_factor = 1.0
        if dd_pct > 10:
            dd_factor = 0.6
        if dd_pct > 15:
            dd_factor = 0.4
        risk_mult *= dd_factor
        # Новостной буст: score сигнала масштабирует риск (score=2 → риск ×2)
        risk_mult *= getattr(signal, "score", 1.0)
        # NEW: Pyramiding by unrealized PnL
        _pyr_trigger = self.strategy_params.get('pyramiding_trigger', 0.0)
        _pyr_add = self.strategy_params.get('pyramiding_add_multiplier', 0.0)
        _pyra_level = 0
        if _pyr_trigger > 0 and _pyr_add > 0 and positions:
            for _p in positions:
                if not _p.get('closed') and _p['symbol'] == signal.symbol and _p['direction'] == signal.direction:
                    _entry = _p['entry_price']
                    _cur = _p.get('_last_price', _entry)
                    _pnl_pct = (_cur - _entry) / _entry if _entry else 0.0
                    if signal.direction == 'SHORT':
                        _pnl_pct = -_pnl_pct
                    if _pnl_pct >= _pyr_trigger:
                        _pyra_level += 1
            if _pyra_level > 0:
                risk_mult *= (1.0 + _pyr_add * _pyra_level)
        sl_pips = max(self.strategy_params.get("sl_pips", 100.0), 10.0)
        if self.market == "forex":
            # Лот от риска: риск $ = equity × risk_pct; SL в пипсах → лот
            rp = risk_pct
            # Dynamic risk: 100% на старте → 75% по мере роста (skill dynamic-risk-sizing)
            dr = self.strategy_params.get("dynamic_risk", 0.0)
            if dr > 0 and self.initial_equity > 0:
                base = risk_pct * 0.75
                rp = base + (risk_pct - base) * math.exp(-dr * (equity / self.initial_equity - 1))
            qty = max(0.01, round(equity * rp * risk_mult / (sl_pips * self.pip_value), 2))
        else:
            # MOEX: qty = risk×eq/ГО (ГО контракта из ticker_cfg['go'])
            # sizing_eq_cap: капитал для расчёта лотов ограничен (как в live)
            eq_for_sizing = equity
            cap = self.strategy_params.get("sizing_eq_cap", 0)
            if cap and cap > 0:
                eq_for_sizing = min(equity, float(cap))
            go_c = self.ticker_go.get(signal.symbol, signal.price)
            qty = calc_contracts(eq_for_sizing, risk_pct * risk_mult, signal.price, go_c)
            # Фьючерсы: целые лоты
            qty = max(1, int(qty))
        if qty <= 0:
            return None
        # риск в $: qty × SL × pip_value
        risk_amount = qty * sl_pips * self.pip_value
        # Slippage (MOEX): штраф = slippage_ticks × min_step (в худшую сторону)
        entry_px = signal.price
        if self.slippage_ticks > 0:
            ms_c = self.ticker_ms.get(signal.symbol, 0.01)
            slip = self.slippage_ticks * ms_c
            entry_px = entry_px + slip if signal.direction == "LONG" else entry_px - slip

        # YAML close params (sl_pct, tp_pct, trail_act, trail_dist, trail_lock)
        sl_pct = self.strategy_params.get("sl_pct")
        tp_pct = self.strategy_params.get("tp_pct")
        if sl_pct is not None and signal.direction == "LONG":
            sl_price = entry_px * (1.0 - float(sl_pct))
        elif sl_pct is not None:
            sl_price = entry_px * (1.0 + float(sl_pct))
        else:
            sl_price = None
        if tp_pct is not None and signal.direction == "LONG":
            tp_price = entry_px * (1.0 + float(tp_pct))
        elif tp_pct is not None:
            tp_price = entry_px * (1.0 - float(tp_pct))
        else:
            tp_price = None
        trail_act = self.strategy_params.get("trail_act")
        trail_dist = self.strategy_params.get("trail_dist", 0.0)
        trail_lock = self.strategy_params.get("trail_lock", 0.0)
        trail_activation_px = (entry_px * (1.0 + float(trail_act))) if trail_act is not None else None

        return {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry_price": entry_px,
            "quantity": qty,
            "_risk_amount": risk_amount,
            "entry_time": bar_time,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "trail_activation": trail_activation_px,
            "trail_distance": trail_dist or 0.0,
            "trail_lock_pct": trail_lock if trail_lock is not None else 0.0,
            "peak_fav": entry_px,  # for trailing peak tracking
            "strategy": self.strategy_name,
            "closed": False,
            "exit_price": None,
            "exit_time": None,
            "_last_price": entry_px,
            "_entry_dt": bar_time,
            "_swap_acc": 0.0,
            "_day_net": getattr(signal, 'day_net', None),
        }

    def load_lsr_data(self, symbol: str, hours: int = 24, end_time: str = "", z_score_threshold: float = 2.0):
        """Загрузить данные LSR для символа из ClickHouse.

        Возвращает список external_signals: [{ts, symbol, direction, zscore, price_at_event}]
        """
        try:
            import requests as _r

            # Сначала загрузить LSR данные
            lsr_query = f"""
            SELECT timestamp, ratio
            FROM crypto.long_short_ratio
            WHERE symbol='{symbol}' AND source='bybit_global'
            AND timestamp >= toDateTime64('{end_time}', 3, 'UTC') - INTERVAL {hours} HOUR
            AND timestamp <= toDateTime64('{end_time}', 3, 'UTC')
            ORDER BY timestamp
            FORMAT JSONEachRow
            """
            resp = _r.get(self.ch_host, params={"query": lsr_query}, timeout=30)
            resp.raise_for_status()
            raw = resp.text.strip()
            if not raw:
                return []

            lsr_data = []
            for line in raw.split('\n'):
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                lsr_data.append({
                    'timestamp': row['timestamp'],
                    'ratio': row['ratio']
                })

            if not lsr_data:
                return []

            # Вычислить z-score (running window — O(1) per point)
            from collections import deque
            window = 720  # 720 часов = 30 дней
            win_vals = deque()
            win_sum = 0.0
            win_sum_sq = 0.0
            zscores = []
            min_periods = 100
            for val in [r['ratio'] for r in lsr_data]:
                win_vals.append(val)
                win_sum += val
                win_sum_sq += val * val
                if len(win_vals) > window:
                    old = win_vals.popleft()
                    win_sum -= old
                    win_sum_sq -= old * old
                n = len(win_vals)
                if n >= min_periods:
                    mu = win_sum / n
                    var = win_sum_sq / n - mu * mu
                    sigma = var ** 0.5 if var > 1e-12 else 0.001
                    z = (val - mu) / sigma
                    zscores.append(z)
                else:
                    zscores.append(0.0)

            # Обработать события пересечения
            signals = []
            for i in range(1, len(zscores)):
                zprev = zscores[i-1]
                zcurr = zscores[i]

                # Кросс-условия
                if zprev < z_score_threshold and zcurr >= z_score_threshold:
                    direction = 'SHORT'
                elif zprev > -z_score_threshold and zcurr <= -z_score_threshold:
                    direction = 'LONG'
                else:
                    continue

                event_ts = lsr_data[i]['timestamp']

                # Получить цену на момент события (5m close)
                price_query = f"""
                SELECT close
                FROM crypto.klines
                WHERE symbol='{symbol}' AND interval='5m'
                AND timestamp <= toDateTime64('{event_ts}', 3, 'UTC')
                ORDER BY timestamp DESC
                LIMIT 1
                FORMAT JSONEachRow
                """
                resp = _r.get(self.ch_host, params={"query": price_query}, timeout=30)
                resp.raise_for_status()
                raw = resp.text.strip()
                price_result = []
                if raw:
                    for line in raw.split('\n'):
                        line = line.strip()
                        if line:
                            price_result.append(json.loads(line))

                if price_result:
                    price_at_event = price_result[0]['close']
                else:
                    price_at_event = 0.0

                signals.append({
                    'ts': event_ts,
                    'timestamp': event_ts,
                    'symbol': symbol,
                    'direction': direction,
                    'zscore': zcurr,
                    'zprev': zprev,
                    'price_at_event': price_at_event,
                    'action': 'signal'
                })

            return signals

        except Exception as e:
            logger.warning(f"Ошибка загрузки LSR данных для {symbol}: {e}")
            return []

    def _check_sl_tp_trail(self, pos_dict: dict, price: float) -> str:
        """Проверить SL/TP/Trail позиции (из YAML close params).

        Returns 'sl', 'tp', 'trailing', or 'hold'.
        Обновляет pos_dict['sl_price'] при трейлинге.
        """
        direction = pos_dict["direction"]
        entry = pos_dict["entry_price"]
        sl = pos_dict.get("sl_price")
        tp = pos_dict.get("tp_price")
        trail_act = pos_dict.get("trail_activation")
        trail_dist = pos_dict.get("trail_distance", 0.0)
        trail_lock = pos_dict.get("trail_lock_pct", 0.0)
        peak = pos_dict.get("peak_fav", entry)

        if direction == "LONG":
            # Trailing
            if trail_act is not None and price >= trail_act:
                if price > peak:
                    peak = price
                    pos_dict["peak_fav"] = peak
                new_sl = peak * (1.0 - trail_dist)
                if trail_lock > 0:
                    new_sl = max(new_sl, entry * (1.0 + trail_lock))
                sl = max(sl if sl is not None else 0, new_sl)
                pos_dict["sl_price"] = sl
            # SL check
            if sl is not None and price <= sl:
                return "sl"
            # TP check
            if tp is not None and price >= tp:
                return "tp"
        else:  # SHORT
            if trail_act is not None and price <= trail_act:
                if price < peak:
                    peak = price
                    pos_dict["peak_fav"] = peak
                new_sl = peak * (1.0 + trail_dist)
                if trail_lock > 0:
                    new_sl = min(new_sl, entry * (1.0 - trail_lock))
                sl = min(sl if sl is not None else float('inf'), new_sl)
                pos_dict["sl_price"] = sl
            if sl is not None and price >= sl:
                return "sl"
            if tp is not None and price <= tp:
                return "tp"

        return "hold"

    def _close_position(
        self, pos: dict, price: float, bar_time: str,
        reason: str, trades: list[dict],
    ):
        """Закрыть позицию и записать сделку."""
        direction = pos["direction"]
        if self.market == "forex":
            sym = pos["symbol"].lower()
            pt = self.point.get(sym, 1e-5)
            pip = 10 * pt  # pip = 10 × point (AlfaForex)
            pnl = (price - pos["entry_price"]) / pip * self.pip_value * pos["quantity"]
            if direction == "SHORT":
                pnl = -pnl
            # спред (в пунктах) — платим при входе+выходе
            spr = self.spread_points.get(sym, 0.0)
            # спред: spread_points в $ на лот (Rann: EURUSD 6 = $6/лот round-trip)
            pnl -= spr * pos["quantity"]
            # комиссия
            pnl -= self.commission_per_lot * pos["quantity"]
            # свопы за ночь
            pnl -= pos.get("_swap_acc", 0.0)
            pnl_pct = pnl / (pos["entry_price"] * pos["quantity"]) * 100
        else:
            pnl = (price - pos["entry_price"]) * pos["quantity"]
            # MOEX фьючерсы: pnl = (Δprice / ms) × sp × qty
            sym = pos["symbol"]
            ms_c = self.ticker_ms.get(sym)
            sp_c = self.ticker_sp.get(sym)
            if ms_c and sp_c and ms_c > 0:
                pnl = (price - pos["entry_price"]) / ms_c * sp_c * pos["quantity"]
            if direction == "SHORT":
                pnl = -pnl
            # Комиссия (MOEX: fee из strategy_params, round-trip)
            fee = float(self.strategy_params.get('fee_rub', 0.0))
            if fee > 0:
                pnl -= fee * 2 * pos["quantity"]
            pnl_pct = pnl / (pos["entry_price"] * pos["quantity"]) * 100

        trades.append({
            "strategy": self.strategy_name,
            "ticker": pos["symbol"],
            "direction": pos["direction"],
            "entry_time": pos["entry_time"],
            "exit_time": bar_time,
            "entry_price": pos["entry_price"],
            "exit_price": price,
            "quantity": round(pos["quantity"], 6),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "exit_reason": reason,
            "tags": '[]',
        })
        self._realized = getattr(self, "_realized", 0.0) + pnl
        pos["closed"] = True
        pos["exit_price"] = price
        pos["exit_time"] = bar_time

    def _mtm(self, pos: dict, price: float) -> float:
        """MTM по текущей цене + обновление лучшей цены для трейлинга."""
        pos["_last_price"] = price
        direction = pos["direction"]
        # обновляем trail_activation (лучшая цена)
        if direction == "LONG":
            if pos.get("trail_activation") is None or price > pos["trail_activation"]:
                pos["trail_activation"] = price
        else:
            if pos.get("trail_activation") is None or price < pos["trail_activation"]:
                pos["trail_activation"] = price
        if self.market == "forex":
            sym = pos["symbol"].lower()
            pt = self.point.get(sym, 1e-5)
            pip = 10 * pt  # pip = 10 × point (AlfaForex)
            pnl = (price - pos["entry_price"]) / pip * self.pip_value * pos["quantity"]
            if direction == "SHORT":
                pnl = -pnl
            # накопление свопа (грубо: каждый тик = ночь, только для дневных баров)
            return pnl - pos.get("_swap_acc", 0.0)
        pnl = (price - pos["entry_price"]) * pos["quantity"]
        # MOEX фьючерсы: pnl = (Δprice / ms) × sp × qty (ms=min_step, sp=step_price)
        sym = pos["symbol"]
        ms_c = self.ticker_ms.get(sym)
        sp_c = self.ticker_sp.get(sym)
        if ms_c and sp_c and ms_c > 0:
            pnl = (price - pos["entry_price"]) / ms_c * sp_c * pos["quantity"]
        if direction == "SHORT":
            pnl = -pnl
        return pnl

    def _mtm_last(self, pos: dict) -> float:
        """MTM по последней известной цене."""
        price = pos.get("_last_price", pos["entry_price"])
        return self._mtm(pos, price)

    def _run_lsr_mode(self, sym_bars, end_time, all_external_signals):
        """Pre-compute LSR trades with 1m hi/lo, run 1m MTM portfolio loop.

        1:1 с LsrCrossBacktester.run() — per-event simulate_trade + 1m equity.
        """
        import json, yaml as _yl, logging
        from collections import OrderedDict
        from tqa_framework.engine.detect import load_m1_from_ch
        from tqa_framework.strategy_engine.actions import get_action
        from datetime import datetime, timezone

        logger = logging.getLogger(__name__)
        self.pg.ensure_schemas()
        self.pg.ensure_tables_backtest()

        # ── 1. 5m bars already in sym_bars; load 1m bars ──
        k5_cache = dict(sym_bars)
        k1_cache = {}
        for sym in list(sym_bars.keys()):
            bars_1m = load_m1_from_ch(sym, self.days * 24, self.ch_host, self.ch_db,
                                       end_time=end_time,
                                       source=getattr(self, 'data_source', 'bars'), interval='1m')
            k1_cache[sym] = bars_1m if bars_1m else sym_bars[sym]
            logger.info(f"  {sym}: {len(k1_cache[sym])} 1m баров")

        # ── 2. Get action params from YAML ──
        _raw = self._raw_yaml or {}
        _act_params = {}
        for sc in _raw.get('signals', []):
            if sc.get('then') == 'lsr_execute':
                _act_params.update(sc.get('params', {}))
        _close = _raw.get('close', {})
        for _ck in ('sl_pct', 'tp_pct', 'trail_act', 'trail_dist', 'trail_lock', 'hold_h'):
            if _ck in _close:
                _act_params[_ck] = _close[_ck]
        _risk = _raw.get('risk', {}) or {}
        _pyr = _risk.get('pyramiding', {})
        _act_params['pyr_trigger'] = float(_pyr.get('trigger', 0.05))
        _act_params['pyr_add'] = float(_pyr.get('add', 0.5))

        # Live sizing (tick.py:160,238): risk = base_risk * sym_risk[sym] * lev,
        # pnl_usd = eq_open * risk * lev * pnl_pct. pnl_eff from _lsr_execute
        # already carries the pyramiding factor, so _rr() must be base*sym*lev.
        # ✅ sym_risk/leverage живут в секции `risk:` (lsr_cross.strategy.yaml —
        # top-level ключи = strategy/version/metrics/signals/exclude/risk/data_sources).
        # Fallback на top-level sym_risk/params.leverage (config.yaml) — совместимость.
        _sym_risk = _risk.get('sym_risk') or _raw.get('sym_risk') or {}
        _risk_lev = _risk.get('leverage')
        if _risk_lev is not None:
            _lev = float(_risk_lev or 1.0)
        else:
            _lev = float((_raw.get('params') or {}).get('leverage', 1.0) or 1.0)
        # base_risk из YAML переопределяет CLI --risk-pct (live: 0.08),
        # CLI --risk-pct больше НЕ перекрывает YAML молча.
        _base_risk = _risk.get('base_risk')
        _rr_base = float(_base_risk) if _base_risk is not None else self.risk_pct
        if _base_risk is not None and abs(float(_base_risk) - self.risk_pct) > 1e-9:
            logger.info("base_risk из YAML risk.base_risk=%.4f переопределяет CLI risk_pct=%.4f",
                        float(_base_risk), self.risk_pct)
        # comm/slip из YAML risk.comission/risk.slippage → в параметры action
        _comm_y = _risk.get('comission')
        if _comm_y is not None:
            _act_params['comm'] = float(_comm_y)
        _slip_y = _risk.get('slippage')
        if _slip_y is not None:
            _act_params['slip'] = float(_slip_y)
        # DD-stop: adverse-only MTM, порог из YAML risk.dd_stop_pct (live 0.25)
        _dd_stop_pct = float(_risk.get('dd_stop_pct', 0.25) or 0.25)
        # exclude.syms — дополнительный фильтр на прекомпуте (паритет detect.py)
        _excl_syms = set((_raw.get('exclude', {}) or {}).get('syms', []) or [])

        def _rr(sym):
            _m = _sym_risk.get(sym)
            return _rr_base * (float(_m) if _m else 1.0) * _lev

        lsr_exec = get_action('lsr_execute')

        # ── 3. Pre-compute trades from external signals ──
        precomputed = []
        for sig in all_external_signals:
            sym = sig.get('symbol')
            if sym in _excl_syms:
                continue  # exclude.syms (SOLUSDT) — паритет detect.py
            if sym not in k5_cache or sym not in k1_cache:
                continue
            state = {
                'k5_cache': k5_cache[sym],
                'k1_cache': k1_cache[sym],
                'external_signal': sig,
                'symbol': sym,
                'signal_id': 'lsr_execute',
            }
            cfg = dict(_act_params)
            cfg['direction'] = 'LONG' if sig.get('direction') == 'LONG' else 'SHORT'
            try:
                signals = lsr_exec(sym, 0.0, cfg, state)
                for sig_out in signals:
                    p = sig_out.params
                    precomputed.append({
                        'entry_ts': sig_out.timestamp,
                        'exit_ts': p['exit_ts'],
                        'symbol': sym,
                        'direction_int': int(p['direction_int']),
                        'entry_px': p['entry_px'],
                        'exit_px': p['exit_px'],
                        'pnl_eff': p['pnl_eff'],
                        'mtm_dd_pct': p['mtm_dd_pct'],
                        'base_risk': p.get('base_risk', 1.0),
                    })
            except Exception as e:
                logger.warning(f"lsr_execute error {sym}: {e}")
        logger.info(f"Pre-computed {len(precomputed)} trades")

        # ── 4. Build 1m price_map and portfolio loop ──
        def _parse_ts(val):
            if isinstance(val, datetime):
                return val if val.tzinfo is None else val.replace(tzinfo=None)
            return datetime.fromisoformat(str(val).replace('Z', '+00:00')).replace(tzinfo=None)

        price_map = {}
        for sym, bars in k1_cache.items():
            pm = {}
            for b in bars:
                pm[_parse_ts(b['ts'])] = b['close']
            price_map[sym] = pm

        positions_list = []
        for p in precomputed:
            et = _parse_ts(p['entry_ts'])
            xt = _parse_ts(p['exit_ts'])
            positions_list.append({
                'entry_ts': et, 'exit_ts': xt,
                'symbol': p['symbol'], 'direction_int': p['direction_int'],
                'entry_px': p['entry_px'], 'exit_px': p['exit_px'],
                'base_risk': p['base_risk'], 'pnl_eff': p['pnl_eff'],
                'mtm_dd_pct': p['mtm_dd_pct'],
                'eq_at_entry': None, 'entry_equity': None, 'opened': False,
            })

        eq = float(self.initial_equity)
        cash = eq
        peak = eq
        max_dd = 0.0
        equity_curve = []
        # Adverse-only MTM DD (live tick.py:253-292): eq_mtm = equity − equity×Σ risk×lev×max(0,adverse).
        # equity здесь реализованная (cash), как в live (eq_open = realized equity).
        peak_adv = eq
        # Порог DD-stop по adverse-кривой
        dd_stop_pct = _dd_stop_pct
        # Символы, закрытые <7 дней назад (паритет detect.py: recent_closed)
        recent_closed_at = {}
        # Одна позиция на символ (паритет detect.py: open_syms)
        _max_pos = int(_risk.get('max_pos') or self.max_conc or 6)

        all_ts = sorted(set().union(*[price_map[s].keys() for s in price_map]))
        if not all_ts:
            logger.error("Нет 1m данных")
            return

        next_pos_idx = 0
        active = []
        positions_list.sort(key=lambda p: p['entry_ts'])
        sample_every = max(1, len(all_ts) // 1000)

        def _close_p(p, pnl_dollars, reason, exit_ts, exit_px):
            """Запомнить реализованный pnl позиции для trades-построителя."""
            p['_pnl_dollars'] = pnl_dollars
            p['_exit_reason'] = reason
            p['exit_ts'] = exit_ts
            p['exit_px'] = exit_px
            p['eq_at_entry'] = None

        for bi, bar_ts in enumerate(all_ts):
            # ── Открытие позиций (с ограничениями портфеля, паритет detect.py) ──
            while next_pos_idx < len(positions_list):
                p = positions_list[next_pos_idx]
                if p['entry_ts'] <= bar_ts:
                    _skip = None
                    if getattr(self, '_lsr_dd_stopped', False):
                        _skip = 'dd_stopped'
                    elif len(active) >= _max_pos:
                        _skip = 'max_pos'
                    elif any(a['symbol'] == p['symbol'] for a in active):
                        _skip = 'one_per_symbol'
                    elif p['symbol'] in recent_closed_at and \
                            (bar_ts - recent_closed_at[p['symbol']]).total_seconds() < 7 * 86400:
                        _skip = 'recent_closed_7d'
                    if _skip:
                        p['_skipped'] = True
                        p['_skip_reason'] = _skip
                    else:
                        # eq_open = реализованная equity (cash), как live tick.py
                        p['eq_at_entry'] = float(cash)
                        p['entry_equity'] = float(cash)
                        p['opened'] = True
                        active.append(p)
                    next_pos_idx += 1
                else:
                    break

            # ── Закрытие по прекомпутованному exit (SL/TP/trail/timeout) ──
            still_active = []
            for p in active:
                if p['exit_ts'] <= bar_ts:
                    pnl_dollars = p['eq_at_entry'] * _rr(p['symbol']) * p['pnl_eff']
                    cash += pnl_dollars
                    recent_closed_at[p['symbol']] = bar_ts
                    _close_p(p, pnl_dollars,
                             'sl_hit' if p['pnl_eff'] < 0 else 'tp_hit',
                             p['exit_ts'], p['exit_px'])
                else:
                    still_active.append(p)
            active = still_active
            if cash > peak_adv:
                peak_adv = cash  # реализованный рост equity (live: peak после закрытий)

            # ── Signed MTM: портфельная стоимость ──
            port_value = cash
            for p in active:
                mp = price_map[p['symbol']].get(bar_ts)
                if mp is not None:
                    if p['direction_int'] == 1:
                        mtm_pnl = (mp - p['entry_px']) / p['entry_px']
                    else:
                        mtm_pnl = (p['entry_px'] - mp) / p['entry_px']
                    # pos_value с пирамидальным множителем (live: p_risk включает pyr ×1.5)
                    pos_value = p['eq_at_entry'] * _rr(p['symbol']) * mtm_pnl * p['base_risk']
                    port_value += pos_value

            eq = port_value
            if port_value > peak:
                peak = port_value
            dd = (peak - port_value) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

            # ── Adverse-only MTM + DD-stop (live tick.py:253-292) ──
            if active and not getattr(self, '_lsr_dd_stopped', False):
                mtm_dd_sum = 0.0
                for p in active:
                    mp = price_map[p['symbol']].get(bar_ts)
                    if mp is None:
                        continue
                    entry = p['entry_px']
                    if p['direction_int'] == 1:
                        adverse = max(0.0, (entry - mp) / entry)
                    else:
                        adverse = max(0.0, (mp - entry) / entry)
                    mtm_dd_sum += _rr(p['symbol']) * adverse * p['base_risk']
                eq_mtm = cash - cash * mtm_dd_sum
                if eq_mtm > peak_adv:
                    peak_adv = eq_mtm
                dd_adv = (peak_adv - eq_mtm) / peak_adv * 100 if peak_adv > 0 else 0.0
                if dd_adv > dd_stop_pct * 100.0:
                    logger.warning("LSR DD-STOP: adverse MTM DD %.2f%% > %.0f%% — закрываю все %d позиций",
                                   dd_adv, dd_stop_pct * 100.0, len(active))
                    _cs = float(_act_params.get('comm', 0.001)) + float(_act_params.get('slip', 0.0005))
                    for p in active:
                        mp = price_map[p['symbol']].get(bar_ts)
                        if mp is not None:
                            entry = p['entry_px']
                            if p['direction_int'] == 1:
                                mkt_move = (mp - entry) / entry
                            else:
                                mkt_move = (entry - mp) / entry
                            # рынок по последнему close, comm/slip как при обычном закрытии
                            mkt_eff = (mkt_move - _cs) * p['base_risk']
                            pnl_dollars = p['eq_at_entry'] * _rr(p['symbol']) * mkt_eff
                            cash += pnl_dollars
                            recent_closed_at[p['symbol']] = bar_ts
                            _close_p(p, pnl_dollars, 'dd_stop', bar_ts, float(mp))
                        else:
                            p['eq_at_entry'] = None
                    self._lsr_dd_stopped = True
                    active = []  # все закрыты; новые входы заблокированы

            if bi % sample_every == 0 or bi == len(all_ts) - 1:
                equity_curve.append({
                    "strategy": self.strategy_name,
                    "bar_time": str(bar_ts),
                    "equity": round(port_value, 2),
                    "cash_equity": round(cash, 2),
                    "drawdown": round(max_dd, 2),
                })

        # Close remaining (позиции, не закрытые до конца окна)
        for p in positions_list:
            if p['opened'] and p['eq_at_entry'] is not None:
                pnl_dollars = p['eq_at_entry'] * _rr(p['symbol']) * p['pnl_eff']
                cash += pnl_dollars
                _close_p(p, pnl_dollars,
                         'sl_hit' if p['pnl_eff'] < 0 else 'tp_hit',
                         p['exit_ts'], p['exit_px'])

        # Build trades
        all_trades = []
        for p in positions_list:
            if not p['opened'] or p.get('_skipped'):
                continue
            ee = p['entry_equity'] or self.initial_equity
            pnl_dollars = p.get('_pnl_dollars', ee * _rr(p['symbol']) * p['pnl_eff'])
            qty = ee * _rr(p['symbol']) / p['entry_px'] if p['entry_px'] else 0
            all_trades.append({
                "strategy": self.strategy_name,
                "ticker": p['symbol'],
                "direction": 'LONG' if p['direction_int'] == 1 else 'SHORT',
                "entry_price": p['entry_px'],
                "exit_price": p['exit_px'],
                "entry_time": str(p['entry_ts']),
                "exit_time": str(p['exit_ts']),
                "quantity": qty,
                "pnl": pnl_dollars,
                "pnl_pct": p['pnl_eff'] * 100,
                "exit_reason": p.get('_exit_reason') or ("sl_hit" if p['pnl_eff'] < 0 else "tp_hit"),
                "tags": "{}",
            })

        total_return = (cash - self.initial_equity) / self.initial_equity * 100
        wins = sum(1 for t in all_trades if t["pnl"] > 0)
        win_rate = wins / len(all_trades) * 100 if all_trades else 0
        gross_win = sum(t["pnl"] for t in all_trades if t["pnl"] > 0) if wins > 0 else 0
        gross_loss = sum(abs(t["pnl"]) for t in all_trades if t["pnl"] < 0) if len(all_trades) - wins > 0 else 0
        pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
        calmar = total_return / max_dd if max_dd > 0 else 0.0

        summary = {
            "strategy": self.strategy_name,
            "tickers": list(dict.fromkeys(t["ticker"] for t in all_trades)),
            "tf": self.tf_minutes,
            "days": self.days,
            "start_equity": self.initial_equity,
            "end_equity": round(cash, 2),
            "total_return": round(total_return, 2),
            "mdd": round(max_dd, 2),
            "win_rate": round(win_rate, 1),
            "profit_factor": round(pf, 2),
            "total_trades": len(all_trades),
            "calmar_ratio": round(calmar, 2),
            "params": json.dumps({k: v for k, v in self.strategy_params.items()
                                  if k != "dom_series"}, default=str),
        }

        self._lsr_result = {"summary": summary, "trades": all_trades, "equity_curve": equity_curve}

        if getattr(self, "save_results", True):
            summary_id = self.pg.save_summary(summary)
            self.pg.save_trades_batch(all_trades, summary_id)
            self.pg.save_equity_points(equity_curve, summary_id)

    def _calc_summary(
        self, trades: list[dict], equity_curve: list[dict],
        final_equity: float,
    ) -> dict:
        """Рассчитать итоговую сводку."""
        total_return = (final_equity - self.initial_equity) / self.initial_equity * 100
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        total_trades = len(trades)
        win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        mdd = min((p["drawdown"] for p in equity_curve), default=0)
        calmar = total_return / abs(mdd) if mdd != 0 else 0

        return {
            "strategy": self.strategy_name,
            "tickers": [t.get("symbol", "") for t in self.tickers],
            "tf": self.tf_minutes,
            "days": self.days,
            "start_equity": self.initial_equity,
            "end_equity": round(final_equity, 2),
            "total_return": round(total_return, 2),
            "mdd": round(abs(mdd), 2),
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "total_trades": total_trades,
            "calmar_ratio": round(calmar, 2),
            "params": json.dumps({k: v for k, v in self.strategy_params.items()
                                  if k != "dom_series"}, default=str),
        }
