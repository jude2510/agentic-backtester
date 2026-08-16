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
