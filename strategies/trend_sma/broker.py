"""Broker — Position + BrokerSim (trend_sma).

Реалистичная симуляция:
  • Стопы исполняются по рынку (close следующего бара + slippage)
  • Трейлинг: retracement от пика, а не от входа
  • Таймаут — по close бара + slippage
  • Проскальзывание на вход и выход (1 тик)
  • Ликвидность — позиция не должна превышать 50% объёма бара
"""

DEFAULT_ACTIVATION = 0.5   # %
DEFAULT_TRAIL = 0.3        # %
DEFAULT_TIMEOUT = 12       # bars
DEFAULT_STOP_LOSS = 0.7    # % hard stop loss from entry
DEFAULT_COMMISSION = 4     # RUB per contract round-trip
DEFAULT_SLIPPAGE_OUT = 1   # ticks on exit (market order)


class Position:
    def __init__(self, ticker, direction, entry_price, entry_bar, shares, strategy,
                 go=0, step_price=1.0, min_step=0.01, pct=1.0, trailing_params=None,
                 commission=DEFAULT_COMMISSION):
        self.ticker = ticker
        self.direction = direction
        self.entry_price = entry_price
        self.entry_bar = entry_bar
        self.shares = shares
        self.strategy = strategy
        self.go = go
        self.step_price = step_price
        self.min_step = min_step
        self.pct = pct
        self.commission = commission  # per-ticker round-trip комиссия (из PG fee_entry)
        self.trailing_params = trailing_params or {}
        self.best_price = 0.0          # макс fav % (движение в нашу сторону)
        self.best_abs = 0.0            # макс цена в нашу сторону (абс)
        self.trail_activated = False
        self.pnl = 0.0
        self.exit_reason = 'open'
        self.closed = False
        self.exit_price: float | None = None
        self._stop_triggered = False
        self.entry_time = None  # timestamp входа (для timeout_hours)
        self._pyred_n = 0           # trend_sma: число ступеней пирамидинга (0..2)
        self._trend_arm = False     # trend_sma: трейл вооружён (движение >=arm_pct от входа)
        self._base_shares = 0       # trend_sma: базовый размер позы для пирамидинга (фикс. на 1-м вызове)
        self._si_done = 0           # trend_sma: число scale-in доливок (0..scale_levels)
        self._si_extra = 0          # trend_sma: накопленные scale-in доливки ПОВЕРХ pyr-позы
        self._best_price = 0.0      # best price в сторону прибыли (для trail от пика)
        self._skip_ts = None        # ts, до которого позицию не тикать (бар входа)
        self.spread_half = 0.0      # половина спреда в цене: снимается на выходе

    @property
    def activation_pct(self):
        return float(self.trailing_params.get('activation', DEFAULT_ACTIVATION))

    @property
    def trail_pct(self):
        return float(self.trailing_params.get('trail', DEFAULT_TRAIL))

    @property
    def stop_loss_pct(self):
        # Направленно-точный стоп (% от ЦЕНЫ ВХОДА), если задан *_long/*_short;
        # иначе общий 'stop_loss' (DEFAULT_STOP_LOSS, если ключа нет).
        tp = self.trailing_params
        v = tp.get('stop_loss_long') if self.direction == 'long' else tp.get('stop_loss_short')
        if v is not None:
            return float(v)
        return float(tp.get('stop_loss', DEFAULT_STOP_LOSS))

    @property
    def timeout_bars(self):
        return int(self.trailing_params.get('timeout', DEFAULT_TIMEOUT))

    @property
    def fixed_tp_pct(self):
        """Фиксированный TP (mean_reversion): закрытие при движении в нашу
        сторону на tp_pct от входа. None = не использовать.
        Для OCO-входа точная дистанция задаётся в ТИКАХ от уровня лимитки:
        стратегия кладёт 'fixed_tp_long'/'fixed_tp_short' (% от цены входа);
        общий 'fixed_tp' остаётся фолбэком."""
        tp = self.trailing_params
        v = tp.get('fixed_tp_long') if self.direction == 'long' else tp.get('fixed_tp_short')
        if v is not None:
            return float(v)
        v = tp.get('fixed_tp')
        return float(v) if v is not None else None

    @property
    def timeout_hours(self):
        """Таймаут в РЕАЛЬНЫХ часах (mean_reversion): закрытие по времени,
        а не по числу баров. None = не использовать (обычный timeout_bars)."""
        v = self.trailing_params.get('timeout_hours')
        return float(v) if v is not None else None

    def __repr__(self):
        return (f"<{self.strategy} {self.direction} {self.ticker} "
                f"entry={self.entry_price} shares={self.shares}>")


class BrokerSim:
    """Executor: только исполнение (fill, slippage, ликвидность, комиссия, PnL).
    Решения (SL/трейл/пирамидинг/timeout) — в PositionManager."""

    def __init__(self, commission=DEFAULT_COMMISSION, slippage_out=DEFAULT_SLIPPAGE_OUT, slippage_in=1,
                 connector=None):
        self.connector = connector   # ExchangeMock | MT5: только филл-цена, решений нет
        self.commission = commission
        self.slippage_out = slippage_out
        self.slippage_in = slippage_in
        from strategies.trend_sma.position_manager import PositionManager
        self.pm = PositionManager(self)

    def check_liquidity(self, shares: int, volume: float) -> float:
        """Проверить ликвидность. Вернуть множитель slippage."""
        if volume <= 0 or shares <= 0:
            return 2.0
        share = shares / volume
        if share > 0.25:
            return 3.0
        elif share > 0.1:
            return 1.5
        return 1.0

    def update(self, pos: Position, bar_idx: int,
               hi: float, lo: float, prc: float,
               volume: float = 0, ts=None) -> float:
        """Исполнение делегирует решения position-manager'у (см. position_manager.py)."""
        pos.pm = self.pm        # чтобы pos сам знал свой pm (для внешних вызовов)
        return self.pm.update(pos, bar_idx, hi, lo, prc, volume, ts)

    def _close_market(self, pos: Position, exit_prc: float, reason: str, volume: float = 0) -> float:
        """Закрыть по рынку: exit_prc + slippage + liquidity impact."""
        slip_mult = self.check_liquidity(pos.shares, volume)
        total_slippage = self.slippage_out * slip_mult
        _half = float(getattr(pos, 'spread_half', 0.0) or 0.0)   # вторая половина спреда

        if pos.direction == 'long':
            exit_px = exit_prc - total_slippage * pos.min_step - _half
        else:
            exit_px = exit_prc + total_slippage * pos.min_step + _half

        if self.connector is not None:
            exit_px = self.connector.fill_price(pos.ticker, 'close', exit_px)

        ticks = (exit_px - pos.entry_price) / max(pos.min_step, 0.0001)
        if pos.direction == 'short':
            ticks = -ticks
        gross = ticks * pos.step_price * pos.shares * pos.pct
        # комиссия за РАУНД-ТРИП: вход + выход (раньше списывалась только одна нога)
        pnl = gross - 2.0 * pos.commission * pos.shares
        pos.pnl = round(pnl, 2)
        pos.exit_price = round(exit_px, 4)
        pos.exit_reason = reason
        pos.closed = True
        return pos.pnl
