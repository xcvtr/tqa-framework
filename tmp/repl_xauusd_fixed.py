import os
import json
import numpy as np

# Анализ данных XAUUSD
print("Анализ XAUUSD")

# Пересчитываем XAUUSD
# Получаем данные из двух файлов
all_xau_raw = []

try:
    with open("results/oos25_xauusd.json", "r") as f:
        data25 = json.load(f)
        all_xau_raw.extend(data25)
    
    with open("results/oos_rep_xauusd.json", "r") as f:
        data26 = json.load(f)
        all_xau_raw.extend(data26)
        
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
                year_2025_data.extend(data)
    
    # Собираем все данные 2026 года  
    year_2026_data = []
    for pair in pairs_without_gold:
        file_path = f"results/oos_rep_{pair}.json"
        if os.path.isfile(file_path):
            with open(file_path, 'r') as f:
                data = json.load(f)
                year_2026_data.extend(data)

    # Все сделки для анализа
    all_trades_2025 = []
    all_trades_2026 = []
    
    # Анализ 2025 года
    for trade in year_2025_data:
        try:
            entry_price = float(trade['entry_price'])
            exit_price = float(trade['exit_price'])
            shares = float(trade['shares'])
            direction = trade['direction']
            
            if direction == 'long':
                pnl = (exit_price - entry_price) * shares
            else:
                pnl = (entry_price - exit_price) * shares
                
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
                
            all_trades_2026.append(pnl)
        except Exception as e:
            continue

    # Объединяем данные 2025 и 2026 лет
    
    all_trades = all_trades_2025 + all_trades_2026
    
    # Статистика для всего периода без золота
    sum_other = round(sum(all_trades), 2)
    n_other = len(all_trades)
    
    if n_other > 0:
        avg_other = round(sum_other / n_other, 2)
        
        # T-statistics
        if n_other >= 2:
            avg_pnl = sum_other / n_other
            sample_std = np.std(all_trades, ddof=1)  # ddof=1 для выборочной стандартной ошибки
            sample_size = n_other
            
            try:
                se = sample_std / np.sqrt(sample_size)
                t_stat = avg_pnl / se if se != 0 else 0
                t_other = round(t_stat, 2)
            except Exception:
                t_other = 0
        else:
            t_other = 0
            
        # WR
        winning_trades = sum(1 for t in all_trades if t > 0)
        wr_other = round(winning_trades / n_other * 100, 2) if all_trades else 0
    else:
        avg_other = 0
        t_other = 0
        wr_other = 0
        
    # Вывод результатов
    expected_other_sum = -4076
    expected_other_avg = -23.3
    expected_other_t = -1.83
    expected_other_wr = 42
    
    # Проверим отклонения
    other_actual_sum = sum_other
    other_actual_avg = avg_other
    other_actual_t = t_other
    other_actual_wr = wr_other
    other_actual_n = n_other
    
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