"""
Multi-Agent Trading System Tools
"""

from tools.backtest import BacktestTool
from tools.strategy_generator import generate_strategy, generate_trading_strategy
from tools.market_data import fetch_market_data, fetch_market_data_via_gateway
from tools.backtest_tool import backtest_strategy, run_backtest
from tools.results_summary import summarize_backtest, create_results_summary
from tools.history import get_backtest_history

__all__ = [
    'BacktestTool',
    # Pipeline steps (pipeline.py)
    'generate_strategy',
    'fetch_market_data',
    'backtest_strategy',
    'summarize_backtest',
    # Chat tool
    'get_backtest_history',
    # Legacy orchestrator tools, removed with the orchestrator
    'generate_trading_strategy',
    'fetch_market_data_via_gateway',
    'run_backtest',
    'create_results_summary',
]
