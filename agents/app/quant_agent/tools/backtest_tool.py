"""
Backtest Execution Tool
Runs a strategy over market data with Backtrader, in the agent's own process.
"""

import pandas as pd
import config
from step_types import MarketData, StepError
from tools.backtest import BacktestTool


# Initialize backtest tool
backtest_tool = BacktestTool()

INITIAL_CASH = 100_000
COMMISSION = 0.001


def backtest_strategy(strategy_code: str, market: MarketData, position_pct: int) -> dict:
    """Pipeline step: run the strategy over the bars with Backtrader, in process.

    The result is also saved to AgentCore Memory, which is where chat reads
    past backtests from.
    """
    df = pd.DataFrame(market.bars)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    for col in ('open', 'high', 'low', 'close', 'volume', 'adj_close'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    result = backtest_tool.process({
        'strategy_code': strategy_code,
        'market_data': {market.symbol: df},
        'params': {'initial_cash': INITIAL_CASH, 'commission': COMMISSION,
                   'position_pct': position_pct},
    })
    if 'error' in result:
        raise StepError(result['error'])

    config.save_backtest_results_to_memory_sync(result, strategy_code=strategy_code)
    return result
