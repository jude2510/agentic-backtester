"""
Results Summary Tool
Has the results summary runtime write the analysis of a backtest.
"""

import json
from step_types import MarketData, StepError, SummaryReport
from tools.sub_agents import invoke_sub_agent


def _trade_statistics(trades: list) -> dict:
    """Derive the aggregates a reviewer actually reasons about.

    Computed here rather than left to the model for two reasons: summing P&L
    across dozens of trades in prose is slow and error-prone, and every token
    the model spends doing arithmetic is a token it isn't spending on analysis.
    Concentration is included because "most of the profit came from three
    trades" is the single most important thing to notice about a thin sample.
    """
    if not trades:
        return {}

    pnls = [float(t.get('pnl', 0) or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(pnls)

    top3 = sum(sorted(wins, reverse=True)[:3])

    stats = {
        'gross_profit': round(gross_profit, 2),
        'gross_loss': round(gross_loss, 2),
        'net_pnl': round(net, 2),
        'profit_factor': round(gross_profit / gross_loss, 2) if gross_loss else None,
        'avg_win': round(gross_profit / len(wins), 2) if wins else 0.0,
        'avg_loss': round(-gross_loss / len(losses), 2) if losses else 0.0,
        'largest_win': round(max(pnls), 2),
        'largest_loss': round(min(pnls), 2),
        'expectancy_per_trade': round(net / len(pnls), 2),
        'total_commission': round(sum(float(t.get('commission', 0) or 0) for t in trades), 2),
    }

    if gross_profit > 0:
        stats['top3_winners_pct_of_gross_profit'] = round(100 * top3 / gross_profit, 1)

    return stats


def _notable_trades(trades: list, n: int = 5) -> list:
    """The n best and n worst trades — more informative than the first n."""
    if len(trades) <= 2 * n:
        return trades
    ranked = sorted(trades, key=lambda t: float(t.get('pnl', 0) or 0))
    return ranked[:n] + ranked[-n:]


def summarize_backtest(result: dict, market: MarketData) -> SummaryReport:
    """Pipeline step: have the results summary runtime write the analysis.

    It gets the complete record plus pre-computed statistics and the most
    extreme trades rather than all of them: more trades in the prompt made the
    analysis longer, not better (see _trade_statistics).
    """
    payload = {key: result[key] for key in (
        'symbol', 'trades', 'trade_summary', 'metrics', 'initial_value',
        'final_value', 'total_return', 'strategy_class') if key in result}
    payload['backtest_period'] = market.period
    if market.warnings:
        payload['data_coverage_warnings'] = market.warnings

    all_trades = result.get('trades') or []
    if all_trades:
        payload['trade_statistics'] = _trade_statistics(all_trades)
        payload['trades'] = _notable_trades(all_trades)
        payload['trades_shown'] = len(payload['trades'])
        payload['trades_total'] = len(all_trades)

    data = invoke_sub_agent("BACKTEST_SUMMARY_RUNTIME_ARN", payload, "results summary agent")

    # The summary runtime reports its own failures as text, not JSON.
    analysis = str(data.get('analysis') or '')
    try:
        report = json.loads(analysis)
    except ValueError:
        print(f"❌ Results summary returned non-JSON output: {analysis}")
        first_line = analysis.strip().splitlines()[0][:200] if analysis.strip() else 'an empty response'
        raise StepError(f"the results summary agent couldn't write the analysis: {first_line}")
    if not isinstance(report, dict):
        raise StepError("the results summary agent returned the analysis in an unexpected shape")

    return SummaryReport(report=report, version=data.get('version', 'unknown'))
