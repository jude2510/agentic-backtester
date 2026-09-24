"""
Results Summary Tool
Generates comprehensive trading strategy reports using AgentCore Runtime
"""

import os
import json
import uuid
import time
import config


def _data_period() -> dict:
    """Actual date range of the market data the backtest ran on.

    Ground truth, taken from what the gateway returned rather than from the
    requested window — the two can differ when a symbol's history is shorter
    than the request.
    """
    try:
        for payload in (config._stored_market_data or {}).values():
            daily = payload.get('daily_data') or []
            if daily:
                return {'start': daily[0].get('date'),
                        'end': daily[-1].get('date'),
                        'trading_days': len(daily)}
    except Exception as e:
        print(f"⚠️ Could not derive data period: {e}")
    return {}


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


def _enrich(backtest_results: dict) -> dict:
    """Overlay the authoritative backtest record onto the caller's dict.

    The orchestrating LLM assembles the argument to this tool by hand and
    routinely drops the trade list, the trade summary, and the period — the
    summarizer then reports them as "unknown" while the main narrative quotes
    them, so a single report contradicts itself. run_backtest already stored
    the complete record, so prefer it over whatever the model passed.
    """
    enriched = dict(backtest_results or {})
    authoritative = config._last_backtest_result or {}

    for key in ('symbol', 'trades', 'trade_summary', 'metrics', 'initial_value',
                'final_value', 'total_return', 'strategy_class'):
        if key in authoritative:
            enriched[key] = authoritative[key]

    period = _data_period()
    if period:
        enriched['backtest_period'] = period

    if config._data_coverage_warnings:
        enriched['data_coverage_warnings'] = config._data_coverage_warnings

    # Hand over pre-computed aggregates plus a representative slice of trades
    # rather than the full list. The statistics carry the analytical content,
    # so shipping every trade only inflates the prompt — and the response.
    all_trades = enriched.get('trades') or []
    if all_trades:
        enriched['trade_statistics'] = _trade_statistics(all_trades)
        enriched['trades'] = _notable_trades(all_trades)
        enriched['trades_shown'] = len(enriched['trades'])
        enriched['trades_total'] = len(all_trades)

    return enriched


def create_results_summary(backtest_results: dict) -> str:
    """
    Analyze backtest performance and generate comprehensive trading strategy report.

    Args:
        backtest_results: Dictionary containing backtest metrics and performance data

    Returns:
        Formatted analysis report with key statistics and performance assessment
    """
    agent_name = "📈 RESULTS SUMMARY AGENT"
    print("\n" + "="*50)
    print(agent_name)
    print("="*50)

    start_time = time.time()

    # If no backtest results provided, read from AgentCore Memory
    if backtest_results is None:
        print(f"📖 No backtest results provided, reading from AgentCore Memory...")
        backtest_results = config.get_backtest_results_from_memory()
        if backtest_results is None:
            return 'No backtest results found in AgentCore Memory'

    print(f"💾 AgentCore Memory: Processing stored results...")

    if 'error' in backtest_results:
        return f"Results in backtesting: {backtest_results['error']}"

    try:
        reasoning = "Analyzing backtest performance and generating summary..."

        # Send the complete record, not just what the model chose to pass along.
        backtest_results = _enrich(backtest_results)

        print(f"📥 INPUT: {backtest_results}")
        print(f"🧠 REASONING: {reasoning}")

        result = config._agentcore_runtime_client.invoke_agent_runtime(
            agentRuntimeArn=os.getenv('BACKTEST_SUMMARY_RUNTIME_ARN'),
            runtimeSessionId=str(uuid.uuid4()),  # Unique session ID
            payload=json.dumps(backtest_results).encode('utf-8'),
            qualifier="DEFAULT"                  # Optional; version/endpoint control
        )

        # Parse the AgentCore runtime response
        if 'response' in result:
            response_body = result['response'].read().decode('utf-8')
            response_data = json.loads(response_body)

            # results_summary returns {"analysis": "...", "version": "..."}
            if isinstance(response_data, dict):
                summary_text = response_data.get('analysis', str(response_data))
                config._results_summary_version = response_data.get('version', 'unknown')
                print(f"📌 Results Summary Version: {config._results_summary_version}")
            else:
                summary_text = response_data

        elif 'body' in result:
            response_body = result['body'].read().decode('utf-8')
            response_data = json.loads(response_body)

            if 'result' in response_data and 'content' in response_data['result']:
                content = response_data['result']['content']
                if isinstance(content, list) and len(content) > 0:
                    summary_text = content[0].get('text', '')
                else:
                    summary_text = str(content)
            else:
                summary_text = response_body
        else:
            summary_text = str(result)

        processing_time = time.time() - start_time
        print(f"⏱️ Results summary completed in {processing_time:.2f} seconds")
        print(f"got JSON result: {summary_text}")

        # Stash the structured report so the entrypoint can return it as a
        # dedicated field (the frontend renders from this, not parsed text).
        config._results_summary_report = summary_text

        # Brief pause to ensure completion
        time.sleep(0.5)
        return summary_text

    except Exception as e:
        processing_time = time.time() - start_time
        print(f"❌ Results summary failed after {processing_time:.2f} seconds: {e}")
        return f'Results processing failed: {str(e)}'
