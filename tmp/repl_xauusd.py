import os
import json
import numpy as np
from datetime import datetime
import sys

# Подсчет данных для XAUUSD
def calculate_metrics(file_path, year_str):
    """Рассчитать метрики для одного файла"""
    trades = []
    
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            trades = data.get('trades', [])
    except Exception as e:
        print(f"Ошибка чтения файла {file_path}: {e}")
        return None
    
    if not trades:
        return None
        
    # Вычисляем для каждой сделки PnL и проверяем соответствие
    total_pnl = 0
    trade_details = []
    inconsistencies = []
    
    for trade in trades:
        try:
            # Проверяем соответствие entry/exit цен с данными о барах
            entry_price = float(trade['entry_price'])
            exit_price = float(trade['exit_price'])
            shares = float(trade['shares'])
            direction = trade['direction']
            
            # Расчет PnL в пунктах по формуле: (exit - entry) * shares * contract_size / pip_size
            
            # Для XAUUSD: contract=100 унций, pip=0.1, поэтому 1 пп = 10 USD
            # Поэтому если цены в пунктах, то результат в пунктах
            if direction == 'long':
                trade_pnl = (exit_price - entry_price) * shares
            else:
                trade_pnl = (entry_price - exit_price) * shares
                
            # Проверка совпадения с рассчитанным PnL
            if abs(float(trade['pnl']) - trade_pnl) > 0.01:
                inconsistencies.append((trade['entry_i'], trade['exit_i'], trade['pnl'], trade_pnl))
                
            total_pnl += trade_pnl
            trade_details.append({
                'pnl': trade_pnl,
            })
            
        except Exception as e:
            print(f"Ошибка обработки сделки: {e}")
            continue
    
    if not trade_details:
        return None
        
    # Статистика для текущего файла (данные)
    metrics = {}
    metrics['pair'] = 'XAUUSD'
    metrics['year'] = year_str
    metrics['n'] = len(trade_details)
    metrics['sum'] = round(total_pnl, 2)
    
    # Среднее
    avg_pnl = total_pnl / len(trade_details)
    metrics['avg'] = round(avg_pnl, 2)
    
    # T-статистика
    if len(trade_details) >= 2:
        sample_mean = avg_pnl
        sample_std = np.std([t['pnl'] for t in trade_details], ddof=1)
        sample_size = len(trade_details)
        
        try:
            se = sample_std / np.sqrt(sample_size)  # стандартная ошибка среднего
            t_stat = sample_mean / se if se != 0 else 0
            metrics['t'] = round(t_stat, 2)
        except Exception:
            metrics['t'] = 0
    else:
        metrics['t'] = 0
        
    # WR - win rate
    winning_trades = sum(1 for t in trade_details if t['pnl'] > 0)
    metrics['wr'] = round(winning_trades / len(trade_details) * 100, 2) if trade_details else 0
    
    return metrics

# Расширяем функцию для группировки пар
def analyze_pairs():
    """Анализ всех пар без золота"""
    # Списки пар без золота
    pairs_without_gold = [
        "audjpy", "audusd", "euraud", "eurgbp", "eurjpy", 
        "eurusd", "gbpjpy", "gbpusd", "usdcad", "usdchf", "usdjpy"
    ]
    
    # Собираем все данные 2025 года
    year_2025_data = []
    for pair in pairs_without_gold:
        file_path = f"results/oos25_{pair}.json"
        if os.path.isfile(file_path):
            with open(file_path, 'r') as f:
                data = json.load(f)
                year_2025_data.extend(data.get('trades', []))
    
    # Собираем все данные 2026 года  
    year_2026_data = []
    for pair in pairs_without_gold:
        file_path = f"results/oos_rep_{pair}.json"
        if os.path.isfile(file_path):
            with open(file_path, 'r') as f:
                data = json.load(f)
                year_2026_data.extend(data.get('trades', []))

    # Все сделки для анализа
    all_trades_2025 = []
    all_trades_2026 = []
    inconsistencies = []
    
    # Анализ 2025 года
    for trade in year_2025_data:
        try:
            entry_price = float(trade['entry_price'])
            exit_price = float(trade['exit_price'])
            shares = float(trade['shares'])
            direction = trade['direction']
            
            # Логика расчета PnL: 
            # если цена в пунктах, то для XAUUSD получаем уже в пунктах, так как pip_size = 0.1
            # Причем 1 пп = 10 USD, но поскольку мы хотим получить пп, то используем эту шкалу
            
            if direction == 'long':
                pnl = (exit_price - entry_price) * shares
            else:
                pnl = (entry_price - exit_price) * shares
                
            if abs(float(trade['pnl']) - pnl) > 0.01:
                inconsistencies.append((trade['entry_i'], trade['exit_i'], trade['pnl'], pnl))
                
            all_trades_2025.append(pnl)
        except Exception as e:
            continue
            
    # Анализ 2026 года
    for trade in year_2026_data:
        try:
            entry_price = float(trade['entry_price'])
            exit_price = float(trade['exit_price'])
            shares = float(trade['shares'])
            direction = trade['direction']
            
            if direction == 'long':
                pnl = (exit_price - entry_price) * shares
            else:
                pnl = (entry_price - exit_price) * shares
                
            if abs(float(trade['pnl']) - pnl) > 0.01:
                inconsistencies.append((trade['entry_i'], trade['exit_i'], trade['pnl'], pnl))
                
            all_trades_2026.append(pnl)
        except Exception as e:
            continue

    # Объединяем данные 2025 и 2026 лет
    
    all_trades = all_trades_2025 + all_trades_2026
    
    # Статистика для всего периода без золота
    metrics = {
        'pair': 'Другие пары',
        'year': '2025+2026',
        'n': len(all_trades),
        'sum': round(sum(all_trades), 2),
    }
    
    if len(all_trades) > 0:
        metrics['avg'] = round(metrics['sum'] / len(all_trades), 2)
        
        # T-statistics
        if len(all_trades) >= 2:
            avg_pnl = metrics['sum'] / len(all_trades)
            sample_std = np.std(all_trades, ddof=1)  # ddof=1 для выборочной стандартной ошибки
            sample_size = len(all_trades)
            
            try:
                se = sample_std / np.sqrt(sample_size)
                t_stat = avg_pnl / se if se != 0 else 0
                metrics['t'] = round(t_stat, 2)
            except Exception:
                metrics['t'] = 0
        else:
            metrics['t'] = 0
            
        # WR
        winning_trades = sum(1 for t in all_trades if t > 0)
        metrics['wr'] = round(winning_trades / len(all_trades) * 100, 2) if all_trades else 0
    else:
        metrics['avg'] = 0
        metrics['t'] = 0
        metrics['wr'] = 0
        
    return metrics, inconsistencies

# Анализ данных XAUUSD
print("Анализ XAUUSD")

# Пересчитываем XAUUSD
# Получаем данные из двух файлов
all_xau_raw = []

try:
    with open("results/oos25_xauusd.json", "r") as f:
        data25 = json.load(f)
        all_xau_raw.extend(data25.get('trades', []))
    
    with open("results/oos_rep_xauusd.json", "r") as f:
        data26 = json.load(f)
        all_xau_raw.extend(data26.get('trades', []))
        
    all_xau_trades = []
    for trade in all_xau_raw:
        entry_price = float(trade['entry_price'])
        exit_price = float(trade['exit_price'])
        shares = float(trade['shares'])
        direction = trade['direction']
        
        if direction == 'long':
            pnl = (exit_price - entry_price) * shares
        else:
            pnl = (entry_price - exit_price) * shares
        all_xau_trades.append(pnl)
    
    # Теперь рассчитываем статистики на самом деле
    sum_real_xau = sum(all_xau_trades) if all_xau_trades else 0
    n_real_xau = len(all_xau_trades)
    
    avg_real_xau = 0
    t_real_xau = 0
    wr_real_xau = 0
    
    if n_real_xau > 0:
        avg_real_xau = round(sum_real_xau / n_real_xau, 2)
        
        if n_real_xau >= 2:
            sample_std = np.std(all_xau_trades, ddof=1)
            sample_size = n_real_xau
            se = sample_std / np.sqrt(sample_size) if sample_std > 0 else 0
            t_real_xau = round(avg_real_xau / se if se > 0 else 0, 2)
            
        # WR
        winning_trades = sum(1 for t in all_xau_trades if t > 0)
        wr_real_xau = round(winning_trades / n_real_xau * 100, 2)
    
    # Подверждение по утверждению
    xau_actual_sum = sum_real_xau
    xau_actual_avg = avg_real_xau
    xau_actual_t = t_real_xau
    xau_actual_wr = wr_real_xau
    xau_actual_n = n_real_xau
    
    # Ожидаемые значения XAUUSD из утверждения
    expected_xau_sum = 16095
    expected_xau_avg = 240
    expected_xau_t = 2.46
    expected_xau_wr = 60
    
    verdict_xau = "подтверждено"
    if abs(abs(xau_actual_sum) - abs(expected_xau_sum)) > 1:
        verdict_xau = "расходится"
    if abs(round(xau_actual_avg , 1) - expected_xau_avg) > 1:
        verdict_xau = "расходится"
    if abs(round(xau_actual_t, 2) - expected_xau_t) > 0.01:
        verdict_xau = "расходится"
    if abs(round(xau_actual_wr, 1) - expected_xau_wr) > 0.1:  
        verdict_xau = "расходится"

    # Выводим результат анализа XAUUSD
    print(f"XAUUSD: n={xau_actual_n}, сумма={xau_actual_sum}, среднее={xau_actual_avg}, t={xau_actual_t}, WR={xau_actual_wr}% → {verdict_xau}")

    # Анализ остальных пар
    
    print("\nАнализ остальных пар")
    
    # Теперь анализируем другие пары
    pairs_metrics, inconsistencies = analyze_pairs()
    
    # Вывод результатов
    expected_other_sum = -4076
    expected_other_avg = -23.3
    expected_other_t = -1.83
    expected_other_wr = 42
    
    # Проверим отклонения
    other_actual_sum = pairs_metrics['sum']
    other_actual_avg = pairs_metrics['avg']
    other_actual_t = pairs_metrics['t']
    other_actual_wr = pairs_metrics['wr']
    other_actual_n = pairs_metrics['n']
    
    verdict_other = "подтверждено"
    if abs(other_actual_sum - expected_other_sum) > 1:
        verdict_other = "расходится"
    if abs(round(other_actual_avg, 1) - expected_other_avg) > 0.1:
        verdict_other = "расходится"
    if abs(round(other_actual_t, 2) - expected_other_t) > 0.01:
        verdict_other = "расходится" 
    if abs(round(other_actual_wr, 1) - expected_other_wr) > 0.1:
        verdict_other = "расходится"

    print(f"Другие пары: n={other_actual_n}, сумма={other_actual_sum}, среднее={other_actual_avg}, t={other_actual_t}, WR={other_actual_wr}% → {verdict_other}")
    
except Exception as e:
    print(f"Ошибка анализа: {e}")