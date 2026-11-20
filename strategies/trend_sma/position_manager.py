from __future__ import annotations

"""Position-manager trend_sma — слой РЕШЕНИЙ (skills/trading-strategy-architecture).

Здесь: SL, трейлинг (arm/пик/откат), пирамидинг, scale-in, timeout, фикс-TP,
сайзинг и портфельные гейты. Ничего не исполняет: закрытие делегирует
executor'у (BrokerSim._close_market), поэтому исполнение и PnL остаются в broker.
Код перенесён из BrokerSim БЕЗ изменения формул и порядка проверок.
"""


class PositionManager:
    """Решения по позиции; исполнение — через переданный executor."""

    def __init__(self, executor=None):
        self.executor = executor

    def bind(self, executor):
        self.executor = executor
        return self

    def update(self, pos: Position, bar_idx: int,
               hi: float, lo: float, prc: float,
               volume: float = 0, ts=None) -> float:
        if pos.closed:
            return 0.0

        if (pos.trailing_params or {}).get('trail_mode') == 'trend_sma':
            return self._update_trend_sma(pos, bar_idx, hi, lo, prc, volume, ts)

        # 1. Закрытие стопа, сработавшего на предыдущем баре
        if pos._stop_triggered:
            return self.executor._close_market(pos, prc, pos.exit_reason, volume)

        # 1b. Timeout по РЕАЛЬНОМУ времени (mean_reversion: hold_h часов)
        if pos.timeout_hours is not None and ts is not None:
            if pos.entry_time is not None:
                dt = (ts - pos.entry_time).total_seconds() / 3600
                if dt >= pos.timeout_hours:
                    pos._stop_triggered = True
                    pos.exit_reason = 'timeout'
                    return 0.0

        entry = pos.entry_price
        direction = pos.direction

        # 1. Закрытие стопа, сработавшего на предыдущем баре
        if pos._stop_triggered:
            return self.executor._close_market(pos, prc, pos.exit_reason, volume)

        # 2. Favourable movement (движение в нашу сторону)
        if direction == 'long':
            fav_abs = hi
            cur_retrace_abs = lo          # откат — минимум бара
        else:
            fav_abs = lo
            cur_retrace_abs = hi

        fav_pct = (fav_abs - entry) / entry * 100 if direction == 'long' else (entry - fav_abs) / entry * 100

        if fav_pct > pos.best_price:
            pos.best_price = fav_pct
            pos.best_abs = fav_abs

        # 2b. Hard stop loss (против входа)
        if direction == 'long':
            loss_pct = (entry - lo) / entry * 100
        else:
            loss_pct = (hi - entry) / entry * 100
        if loss_pct >= pos.stop_loss_pct:
            if direction == 'long':
                stop_px = entry * (1 - pos.stop_loss_pct / 100)
            else:
                stop_px = entry * (1 + pos.stop_loss_pct / 100)
            return self.executor._close_market(pos, stop_px, 'stop_loss', volume)

        # 2c. Fixed TP (mean_reversion): движение в нашу сторону на fixed_tp_pct
        fixed_tp = pos.fixed_tp_pct
        if fixed_tp is not None:
            if direction == 'long':
                tp_pct = (hi - entry) / entry * 100
            else:
                tp_pct = (entry - lo) / entry * 100
            if tp_pct >= fixed_tp:
                if direction == 'long':
                    tp_px = entry * (1 + fixed_tp / 100)
                else:
                    tp_px = entry * (1 - fixed_tp / 100)
                return self.executor._close_market(pos, tp_px, 'fixed_tp', volume)


        # 3. Активация трейлинга
        if not pos.trail_activated and fav_pct >= pos.activation_pct:
            pos.trail_activated = True

        # 4. Проверка трейлинг-стопа (откат от пика, а не от входа)
        if pos.trail_activated and pos.best_abs > 0:
            if direction == 'long':
                retrace_from_peak = (pos.best_abs - cur_retrace_abs) / pos.best_abs * 100
            else:
                retrace_from_peak = (cur_retrace_abs - pos.best_abs) / pos.best_abs * 100

            if retrace_from_peak >= pos.trail_pct:
                # Исполняем стоп НЕМЕДЛЕННО на этом баре по уровню стопа + slippage
                if direction == 'long':
                    stop_px = pos.best_abs * (1 - pos.trail_pct / 100)
                else:
                    stop_px = pos.best_abs * (1 + pos.trail_pct / 100)
                return self.executor._close_market(pos, stop_px, 'trailing_tp', volume)

        # 5. Таймаут
        if not pos._stop_triggered and bar_idx - pos.entry_bar >= pos.timeout_bars:
            pos._stop_triggered = True
            pos.exit_reason = 'timeout'
            return 0.0

        return 0.0

    def _update_trend_sma(self, pos: Position, bar_idx: int, hi: float, lo: float,
                          prc: float, volume: float = 0, ts=None) -> float:
        """Канон trend_sma (дд_triz.run): SL 1.5%, пирамидинг +0.3x при +1%/+2%
        (2 ступени, поза до n0*1.6), arm трейла при движении >=2% от входа (close),
        затем трейл 1.5% от пика (close). БЕЗ hold — выход только SL/трейл.
        Пирамидинг меняет ПОЗУ (shares), а не вводит отдельные позиции.
        """
        tr = pos.trailing_params
        sl_pct = float(tr.get('stop_loss', 1.5)) / 100.0          # 0.015
        trail_pct = float(tr.get('trail', 1.5)) / 100.0           # 0.015
        arm_pct = float(tr.get('activation', 2.0)) / 100.0        # 0.02
        pyr_add = float(tr.get('oi_pyramid_add', 0.3))
        pyr_step = float(tr.get('oi_pyramid_thr', 0.01))
        pyr_max = int(pos.trailing_params.get('oi_pyramid_max', 2))

        # ── Scale-in (skill scale-in-averaging): доливка ПРОТИВ входа на уровнях
        #    отката (для long — ниже entry), средавляя entry_price. Сосуществует
        #    с pyramiding (тот — в плюс, этот — в минус). Параметры:
        #    scale_in      bool        вкл/выкл
        #    scale_step    %  отката на первый уровень (0.005 = 0.5%)
        #    scale_levels  int  число доливок
        #    scale_add     доля от _base_shares на каждую доливку
        si_on = bool(tr.get('scale_in', False))
        si_step = float(tr.get('scale_step', 0.005))
        si_levels = int(tr.get('scale_levels', 2))
        si_add = float(tr.get('scale_add', 0.5))

        entry = pos.entry_price
        if entry <= 0:
            return 0.0

        # Базовый размер позы фиксируем на первом вызове (как peak) — для пирамидинга
        if getattr(pos, '_base_shares', 0) == 0:
            pos._base_shares = pos.shares

        # Инициализация пика от входа
        if pos._best_price == 0.0:
            pos._best_price = entry

        # ── Scale-in (skill scale-in-averaging): доливка ПРОТИВ входа на уровнях
        #    отката (для long — ниже entry), накопительно к текущей позе, ПОВЕРХ
        #    pyramiding-уровня (pyr задаёт базовый shares, si копит _si_extra).
        #    Параметры: scale_in bool; scale_step %; scale_levels int; scale_add доля.
        if si_on and si_levels > 0 and pos._si_done < si_levels:
            _n = pos._si_done + 1
            hit_px = entry * (1.0 - _n * si_step) if pos.direction == 'long' else entry * (1.0 + _n * si_step)
            if (pos.direction == 'long' and lo <= hit_px) or (pos.direction == 'short' and hi >= hit_px):
                add = max(1, int(round(pos._base_shares * si_add)))
                _tot = int(pos.shares) + add
                # средневзвешенная цена корзины (как exit_grid._add_level)
                pos.entry_price = (float(entry) * int(pos.shares) + hit_px * add) / _tot
                pos.shares = _tot
                pos._si_done += 1
                pos._si_extra += add
                # после доливки пересчитываем entry для дальнейших проверок
                entry = pos.entry_price

        # ── Пирамидинг: 2 ступени (поза → n0*1.3, n0*1.6) при движении в плюс.
        #    Задаёт БАЗОВЫЙ уровень позы от _base_shares; scale-in копится ПОВЕРХ
        #    отдельным счётчиком _si_extra (иначе pyr-loop на каждом баре стирал
        #    scale-in добавку: shares = base*(1+pyr_add*step)).
        for step in range(pos._pyred_n + 1, pyr_max):
            mv = (prc - entry) / entry if pos.direction == 'long' else (entry - prc) / entry
            if mv >= pyr_step * step:
                pos._pyred_n = step
                _bs = pos._base_shares
                _new = _bs * (1.0 + pyr_add * step) + pos._si_extra
                # целый лот (канон MOEX) — как было; дробный (FX) — без округления до нуля
                pos.shares = int(round(_new)) if isinstance(_bs, int) else round(_new, 6)
            else:
                break

        # ── SL 1.5% (по lo/hi бара): точная канон-конвенция
        if pos.direction == 'long' and lo <= entry * (1.0 - sl_pct):
            return self.executor._close_market(pos, entry * (1.0 - sl_pct), 'stop_loss', volume)
        if pos.direction == 'short' and hi >= entry * (1.0 + sl_pct):
            return self.executor._close_market(pos, entry * (1.0 + sl_pct), 'stop_loss', volume)

        # ── Обновление пика в сторону прибыли (close)
        if pos.direction == 'long' and prc > pos._best_price:
            pos._best_price = prc
        if pos.direction == 'short' and prc < pos._best_price:
            pos._best_price = prc

        # ── Трейл: arm при движении >=arm_pct от входа (close), затем трейл от пика
        mv = (prc - entry) / entry if pos.direction == 'long' else (entry - prc) / entry
        if mv >= arm_pct:
            pos._trend_arm = True
        if pos._trend_arm:
            if pos.direction == 'long' and prc <= pos._best_price * (1.0 - trail_pct):
                return self.executor._close_market(pos, prc, 'trailing_tp', volume)  # PnL по close
            if pos.direction == 'short' and prc >= pos._best_price * (1.0 + trail_pct):
                return self.executor._close_market(pos, prc, 'trailing_tp', volume)

        return 0.0


# ── Сайзинг и портфельные гейты (перенесены из self_tester без изменения формул) ──

def size_position(base_eq: float, risk_pct: float, go_knur: float, go_limit: float) -> int:
    """Сайзинг канона: min(риск-лотов, ГО-лотов). Порядок и формулы 1:1."""
    ct_risk = max(1, int(base_eq * risk_pct / go_limit))
    ct_go = int((base_eq * go_knur) / go_limit) if go_limit > 0 else 999
    return min(ct_risk, ct_go) if ct_go > 0 else ct_risk


def frac_size_position(base_eq: float, risk_pct: float, go_knur: float, go_limit: float,
                       lot_step: float) -> float:
    """Дробный сайзинг (FX): порядок канона, лот кратен lot_step, минимум один шаг."""
    if go_limit <= 0:
        return lot_step
    raw = min(base_eq * risk_pct / go_limit, base_eq * go_knur / go_limit)
    return max(lot_step, int(raw / lot_step) * lot_step)


def fx_size_position(equity: float, risk_pct: float, risk_per_lot: float,
                     margin_per_lot: float, lot_step: float) -> float:
    """FX: лот по риску на стопе, но не больше, чем позволяет маржа брокера.

    risk_per_lot  = стоп% × цена × контракт (USD убытка на 1.0 лот при срабатывании)
    margin_per_lot= маржа% × цена × контракт (USD залога на 1.0 лот)
    """
    if risk_per_lot <= 0 or lot_step <= 0:
        return lot_step
    by_risk = equity * risk_pct / risk_per_lot
    by_margin = (equity / margin_per_lot) if margin_per_lot > 0 else float('inf')
    return round(max(lot_step, int(min(by_risk, by_margin) / lot_step) * lot_step), 8)


def check_portfolio_gates(ticker, lot_size, go_limit, go_used, equity, go_knur,
                          open_positions, cluster_of):
    """Гейты канона: позиция по тикеру → кластер → ГО-бюджет → резерв 1.2×ГО."""
    open_list = [p for p in open_positions if not getattr(p, 'closed', False)]
    if sum(1 for p in open_list if p.ticker == ticker) >= 1:
        return False, 'pos_open'
    cl = cluster_of(ticker)
    if cl and any(cluster_of(p.ticker) == cl for p in open_list):
        return False, 'cluster'
    if go_used + go_limit * lot_size > equity * go_knur:
        return False, 'go'
    if equity < go_limit * lot_size * 1.2:
        return False, 'go_reserve'
    return True, ''
