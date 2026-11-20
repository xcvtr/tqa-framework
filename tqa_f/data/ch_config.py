
# Configuration for ClickHouse data access for IR strategy
CH_HOSTS = ['10.0.0.60', '10.0.0.64']
CH_PORT = 8123
CH_DATABASE = 'forex'
CH_TABLE = 'forex.bars'

# Forex pairs used by the IR strategy
FOREX_PAIRS = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD', 'USDCAD', 'NZDUSD', 'AUDUSD', 'EURJPY']
