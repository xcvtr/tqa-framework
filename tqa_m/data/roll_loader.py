"""Loader: дотягивает moex.mt5_roll живым потоком moex.mt5_continuous.

Зачем отдельный контейнер: детектор и папер не ходят за сырьём сами —
единственный писатель в канон-таблицу это loader, иначе живые дни просто
не появляются (roll отвязан от моста и обновляется только вручную).

Правила:
  * пишем ТОЛЬКО вперёд: bt > последнего бара тикера в roll, историю не трогаем;
  * дубли не вставляем (сверяем уже лежащие таймстемпы);
  * цены — как в continuous, back-adjust на ролле контракта не повторяем
    (это осознанный компромисс: шов roll↔continuous виден один раз на стыке).
"""
from __future__ import annotations

import logging

import clickhouse_connect

log = logging.getLogger('loader')

CANON = ('PT,MX,NG,LK,SV,TT,RI,MG,MM,SG,USDRUBF,BR,CR,SN,MN,IMOEXF,MC,NM,NA,FL,'
         'TP,GLDRUBF,CM,SO,YD,HY,AL,VK').split(',')


def _client():
    return clickhouse_connect.get_client(host='10.0.0.60', port=8123, database='moex')


def extend(tickers: list[str] | None = None, dry_run: bool = False) -> dict[str, int]:
    """Дописать в mt5_roll всё, что появилось в mt5_continuous после хвоста roll."""
    cli = _client()
    out: dict[str, int] = {}
    for t in (tickers or CANON):
        (last,) = cli.query("SELECT max(bt) FROM moex.mt5_roll WHERE ticker=%(t)s", parameters={'t': t}).result_rows[0]
        if last is None:
            log.warning('%s: в roll нет истории — backfill делает stitch, не loader', t)
            continue
        rows = cli.query(
            "SELECT ticker, bt, opn, hi, lo, prc, vol, tick_vol FROM moex.mt5_continuous "
            "WHERE ticker=%(t)s AND bt > %(last)s ORDER BY bt",
            parameters={'t': t, 'last': last}).result_rows
        if not rows:
            out[t] = 0
            continue
        have = {r[0] for r in cli.query(
            "SELECT bt FROM moex.mt5_roll WHERE ticker=%(t)s AND bt > %(last)s",
            parameters={'t': t, 'last': last}).result_rows}
        new = [r for r in rows if r[1] not in have]
        if new and not dry_run:
            cli.insert('moex.mt5_roll', new,
                       column_names=['ticker', 'bt', 'opn', 'hi', 'lo', 'prc', 'vol', 'tick_vol'])
        out[t] = len(new)
    total = sum(out.values())
    log.info('loader: +%d баров (%d тикеров)', total, sum(1 for v in out.values() if v))
    return out
