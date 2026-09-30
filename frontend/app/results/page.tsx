'use client';

import { useState, useEffect, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { motion } from 'framer-motion';
import GlassCard from '@/components/ui/GlassCard';
import AnimatedButton from '@/components/ui/AnimatedButton';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import Markdown from '@/components/Markdown';
import PipelineProgress, { STEP_LABELS } from '@/components/PipelineProgress';
import { AgentOutput, PipelineStep, StepName, StrategyInput, Trade } from '@/types/strategy';
import { FRONTEND_VERSION } from '@/lib/version';

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || '';

// A backtest usually finishes in about a minute; the worker's own limit is
// ten, so a job still running after that is not coming back.
const POLL_INTERVAL_MS = 2000;
const POLL_DEADLINE_MS = 10 * 60 * 1000;

/** The results page's view of the job's structured data. */
function toOutput(data: any): AgentOutput {
  const strategy: Partial<StrategyInput> = data.strategyInput || {};
  const m = data.backtest_metrics || {};
  const metrics = m.metrics || {};
  const report = data.summary_report || {};
  const cents = (v: number) => String(Math.round(v * 100) / 100);
  const sharpe = metrics['Sharpe Ratio'];

  return {
    initial_investment: m.initial_value != null ? String(m.initial_value) : 'N/A',
    final_portfolio_value: m.final_value != null ? cents(m.final_value) : 'N/A',
    total_return: m.total_return != null ? `${m.total_return.toFixed(2)}%` : 'N/A',
    maximum_drawdown: metrics['Max Drawdown'] || 'N/A',
    profit_loss: m.final_value != null && m.initial_value != null
      ? cents(m.final_value - m.initial_value) : 'N/A',
    sharpe_ratio: sharpe != null && sharpe !== 'N/A' ? String(sharpe) : 'N/A',
    symbol: strategy.stock_symbol || '',
    strategy_type: strategy.name || '',
    stop_loss: `${strategy.stop_loss}%`,
    take_profit: `${strategy.take_profit}%`,
    position_pct: strategy.position_pct ?? 95,
    buy_conditions: strategy.buy_conditions || '',
    sell_conditions: strategy.sell_conditions || '',
    backtest_window: strategy.backtest_window || '',
    executive_summary: report.executiveSummary,
    detailed_analysis: report.detailedAnalysis,
    concerns_and_recommendations: report.concernsAndRecommendations,
    strategy_code: data.strategyCode || undefined,
    trades: data.trades || [],
    trade_summary: data.trade_summary,
    data_warnings: data.data_warnings || [],
    data_period: data.data_period || undefined,
    versions: data.versions,
  };
}

function ResultsDisplayContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [results, setResults] = useState<AgentOutput | null>(null);
  const [steps, setSteps] = useState<PipelineStep[]>([]);
  const [startTime, setStartTime] = useState<number | undefined>();
  const [failure, setFailure] = useState<{ message: string; step?: StepName } | null>(null);

  useEffect(() => {
    const jobId = searchParams.get('jobId');
    if (!jobId) {
      setFailure({ message: 'Missing job information.' });
      return;
    }

    let cancelled = false;
    const deadline = Date.now() + POLL_DEADLINE_MS;

    const poll = async () => {
      while (!cancelled && Date.now() < deadline) {
        try {
          const response = await fetch(
            `${BASE_PATH}/api/execute-backtest-async?jobId=${jobId}`, { cache: 'no-store' });
          const job = await response.json();
          if (cancelled) return;

          if (response.status === 404) {
            setFailure({ message: job.error || 'This backtest could not be found.' });
            return;
          }
          if (job.steps) setSteps(job.steps);
          if (job.startTime) setStartTime(job.startTime);

          if (job.status === 'complete') {
            setResults(toOutput(job.data));
            return;
          }
          if (job.status === 'error') {
            setFailure({ message: job.error || 'The backtest failed.', step: job.failedStep });
            return;
          }
        } catch (err) {
          // A dropped poll is not a failed backtest; the next one retries.
          console.error('[Results] polling error:', err);
        }
        await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL_MS));
      }
      if (!cancelled) {
        setFailure({ message: "This backtest didn't finish within 10 minutes. Please try again." });
      }
    };

    poll();
    return () => {
      cancelled = true;
    };
  }, [searchParams]);

  const handleNewStrategy = () => {
    router.push('/');
  };

  const parsePercentage = (value: string | number | undefined): number => {
    if (typeof value === 'number') return value;
    if (!value || typeof value !== 'string') return 0;
    return parseFloat(value.replace('%', '').replace('+', '')) || 0;
  };

  if (failure) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-dark-primary via-dark-secondary to-dark-tertiary">
        <div className="container mx-auto max-w-3xl px-6 py-12">
          <h1 className="mb-3 text-4xl font-bold text-white">
            {failure.step ? 'The backtest stopped' : 'Something went wrong'}
          </h1>
          <p className="mb-8 text-gray-300">
            {failure.step && (
              <span className="font-semibold text-white">{STEP_LABELS[failure.step]}: </span>
            )}
            {failure.message}
          </p>
          {steps.length > 0 && (
            <GlassCard className="p-6 md:p-8">
              <PipelineProgress steps={steps} finished />
            </GlassCard>
          )}
          <div className="mt-8 flex justify-center">
            <AnimatedButton onClick={handleNewStrategy} variant="primary">
              Try New Strategy
            </AnimatedButton>
          </div>
        </div>
      </div>
    );
  }

  if (!results) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-dark-primary via-dark-secondary to-dark-tertiary">
        <div className="container mx-auto max-w-3xl px-6 py-12">
          <h1 className="mb-3 text-4xl font-bold bg-gradient-to-r from-accent-blue to-accent-purple bg-clip-text text-transparent">
            Running your backtest
          </h1>
          <p className="mb-8 text-gray-300">
            Each step reports here as it happens. This usually takes about a minute.
          </p>
          <GlassCard className="p-6 md:p-8">
            <PipelineProgress steps={steps} startTime={startTime} />
          </GlassCard>
        </div>
      </div>
    );
  }

  const totalReturn = parsePercentage(results.total_return);
  const performanceColor = totalReturn >= 0 ? 'text-accent-green' : 'text-red-400';
  const performanceEmoji = totalReturn >= 20 ? '🚀' : totalReturn >= 10 ? '✅' : totalReturn >= 0 ? '📈' : '⚠️';
  const summaryStep = steps.find(s => s.step === 'summarize');
  const hasAnalysis = Boolean(results.executive_summary || results.detailed_analysis);
  const totalSeconds = steps.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0) / 1000;

  return (
    <div className="min-h-screen bg-gradient-to-br from-dark-primary via-dark-secondary to-dark-tertiary">
      <div className="container mx-auto px-6 py-12">
        {/* Header */}
        <motion.div
          className="mb-12"
          initial={{ opacity: 0, y: -50 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8 }}
        >
          <h1 className="text-5xl font-bold bg-gradient-to-r from-accent-blue to-accent-purple bg-clip-text text-transparent mb-4">
            📈 Backtest Results
          </h1>
          <p className="text-xl text-gray-300">
            Your trading strategy performance powered by AgentCore
          </p>
        </motion.div>

        {/* Sits next to the numbers, not only in the footer: a return figure
            reads as advice unless told otherwise. */}
        <div className="mb-10 rounded-lg border border-amber-400/30 bg-amber-400/5 px-5 py-3">
          <p className="text-sm text-amber-200/90">
            <span className="font-semibold">Simulated results.</span>{' '}
            <span className="text-amber-100/70">
              Hypothetical performance on historical data, not a prediction. Costs
              such as slippage and taxes are excluded, and the strategy code was
              generated by a language model. Educational use only — not financial advice.
            </span>
          </p>
        </div>

        {/* Shown from the agent's coverage check, not the model's prose, so a
            stale or short data window is flagged even if the analysis omits it. */}
        {results.data_warnings && results.data_warnings.length > 0 && (
          <div className="-mt-6 mb-10 rounded-lg border border-red-400/40 bg-red-400/5 px-5 py-3">
            <p className="text-sm font-semibold text-red-300">
              This backtest ran on less data than the window you chose.
            </p>
            <ul className="mt-1 list-disc pl-5 text-sm text-red-200/80">
              {results.data_warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Performance Overview */}
        <motion.div
          initial={{ opacity: 0, y: 50 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.2 }}
          className="mb-12"
        >
          <GlassCard className="p-8 text-center">
            <div className="flex items-center justify-center space-x-4 mb-6">
              <span className="text-6xl">{performanceEmoji}</span>
              <div>
                <h2 className="text-3xl font-bold text-white">Performance Overview</h2>
                <motion.div
                  className={`text-5xl font-bold ${performanceColor}`}
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ duration: 0.5, delay: 0.5 }}
                >
                  {results.total_return}
                </motion.div>
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
              <motion.div
                className="text-center"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.7 }}
              >
                <div className="text-gray-400 text-sm mb-1">Initial Investment</div>
                <div className="text-2xl font-bold text-white">
                  ${results.initial_investment}
                </div>
              </motion.div>

              <motion.div
                className="text-center"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.8 }}
              >
                <div className="text-gray-400 text-sm mb-1">Final Value</div>
                <div className="text-2xl font-bold text-accent-green">
                  ${results.final_portfolio_value}
                </div>
              </motion.div>

              <motion.div
                className="text-center"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.9 }}
              >
                <div className="text-gray-400 text-sm mb-1">Profit/Loss</div>
                <div className={`text-2xl font-bold ${parseFloat(results.profit_loss || '0') >= 0 ? 'text-accent-green' : 'text-red-400'}`}>
                  ${results.profit_loss || 'N/A'}
                </div>
              </motion.div>

              <motion.div
                className="text-center"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 1.0 }}
              >
                <div className="text-gray-400 text-sm mb-1">Max Drawdown</div>
                <div className="text-2xl font-bold text-red-400">
                  {results.maximum_drawdown}
                </div>
              </motion.div>
            </div>

            {/* Additional Metrics Row */}
            <div className="grid grid-cols-2 gap-6 mt-6 pt-6 border-t border-white/10">
              <motion.div
                className="text-center"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 1.1 }}
              >
                <div className="text-gray-400 text-sm mb-1">Sharpe Ratio</div>
                <div className="text-2xl font-bold text-accent-purple">
                  {results.sharpe_ratio || 'N/A'}
                </div>
              </motion.div>

              <motion.div
                className="text-center"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 1.2 }}
              >
                <div className="text-gray-400 text-sm mb-1">Symbol</div>
                <div className="text-2xl font-bold text-accent-blue">
                  {results.symbol}
                </div>
              </motion.div>
            </div>
          </GlassCard>
        </motion.div>

        {/* Strategy Details */}
        <motion.div
          initial={{ opacity: 0, y: 50 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.4 }}
          className="mb-12"
        >
          <GlassCard className="p-8">
            <h3 className="text-2xl font-semibold text-white mb-6">📋 Strategy Details</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div>
                <span className="text-gray-400">Strategy Type:</span>
                <p className="text-white text-lg font-medium mt-1">{results.strategy_type}</p>
              </div>
              <div>
                <span className="text-gray-400">Symbol:</span>
                <p className="text-white text-lg font-medium mt-1">{results.symbol}</p>
              </div>
              <div>
                <span className="text-gray-400">Stop Loss:</span>
                <p className="text-red-400 text-lg font-medium mt-1">{results.stop_loss}</p>
              </div>
              <div>
                <span className="text-gray-400">Take Profit:</span>
                <p className="text-accent-green text-lg font-medium mt-1">{results.take_profit}</p>
              </div>
              <div>
                <span className="text-gray-400">Position Size:</span>
                <p className="text-white text-lg font-medium mt-1">
                  {results.position_pct ? `${results.position_pct}% of cash` : 'default (95% of cash)'}
                </p>
              </div>
              {results.backtest_window && (
                <div>
                  <span className="text-gray-400">Backtest Window:</span>
                  <p className="text-white text-lg font-medium mt-1">{results.backtest_window}</p>
                </div>
              )}
              {results.data_period && (
                <div>
                  <span className="text-gray-400">Data Period:</span>
                  <p className="text-white text-lg font-medium mt-1">
                    {results.data_period.start} → {results.data_period.end}{' '}
                    <span className="text-gray-400 text-base font-normal">
                      ({results.data_period.trading_days} trading days)
                    </span>
                  </p>
                </div>
              )}
            </div>
            {/* Buy/Sell Conditions */}
            {(results.buy_conditions || results.sell_conditions) && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6 pt-6 border-t border-white/10">
                {results.buy_conditions && (
                  <div>
                    <span className="text-gray-400">Buy Conditions:</span>
                    <p className="text-accent-green text-sm font-medium mt-1">{results.buy_conditions}</p>
                  </div>
                )}
                {results.sell_conditions && (
                  <div>
                    <span className="text-gray-400">Sell Conditions:</span>
                    <p className="text-red-400 text-sm font-medium mt-1">{results.sell_conditions}</p>
                  </div>
                )}
              </div>
            )}
          </GlassCard>
        </motion.div>

        {/* AI Agent Analysis */}
        <motion.div
          initial={{ opacity: 0, y: 50 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.5 }}
          className="mb-12"
        >
          <div className="flex items-center space-x-3 mb-6">
            <div className="w-12 h-12 bg-gradient-to-r from-accent-purple to-accent-blue rounded-lg flex items-center justify-center">
              <span className="text-2xl">🤖</span>
            </div>
            <div>
              <h3 className="text-2xl font-bold text-white">AI Agent Analysis</h3>
              <p className="text-gray-400">Powered by Pydantic AI and AgentCore</p>
            </div>
          </div>

          {/* The analysis step can fail on its own; the numbers above don't
              depend on it, so say so rather than showing an empty section. */}
          {!hasAnalysis && (
            <GlassCard className="p-6">
              <p className="text-gray-300">
                The written analysis isn&apos;t available for this run
                {summaryStep?.detail ? `: ${summaryStep.detail}` : '.'}
              </p>
              <p className="mt-2 text-sm text-gray-400">
                The numbers above come straight from the backtest and are unaffected.
              </p>
            </GlassCard>
          )}

          {/* Executive Summary */}
          {results.executive_summary && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.6 }}
              className="mb-6"
            >
              <GlassCard className="p-6">
                <div className="flex items-center space-x-2 mb-4">
                  <span className="text-2xl">📊</span>
                  <h4 className="text-xl font-semibold text-white">Executive Summary</h4>
                </div>
                <Markdown className="text-gray-300">{results.executive_summary}</Markdown>
              </GlassCard>
            </motion.div>
          )}

          {/* Detailed Analysis */}
          {results.detailed_analysis && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.7 }}
              className="mb-6"
            >
              <GlassCard className="p-6">
                <div className="flex items-center space-x-2 mb-4">
                  <span className="text-2xl">🔍</span>
                  <h4 className="text-xl font-semibold text-white">Detailed Analysis</h4>
                </div>
                <Markdown className="text-gray-300">{results.detailed_analysis}</Markdown>
              </GlassCard>
            </motion.div>
          )}

          {/* Concerns and Recommendations */}
          {results.concerns_and_recommendations && Object.keys(results.concerns_and_recommendations).length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.8 }}
            >
              <GlassCard className="p-6">
                <div className="flex items-center space-x-2 mb-4">
                  <span className="text-2xl">💡</span>
                  <h4 className="text-xl font-semibold text-white">Concerns & Recommendations</h4>
                </div>

                <div className="space-y-4">
                  {/* High Priority */}
                  {results.concerns_and_recommendations.highPriority && results.concerns_and_recommendations.highPriority.length > 0 && (
                    <div>
                      <div className="flex items-center space-x-2 mb-2">
                        <span className="text-red-400 font-semibold">🔴 High Priority</span>
                      </div>
                      <ul className="list-disc list-inside space-y-1 text-gray-300 ml-4">
                        {results.concerns_and_recommendations.highPriority.map((item: string, index: number) => (
                          <li key={index}><Markdown inline>{item}</Markdown></li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Medium Priority */}
                  {results.concerns_and_recommendations.mediumPriority && results.concerns_and_recommendations.mediumPriority.length > 0 && (
                    <div>
                      <div className="flex items-center space-x-2 mb-2">
                        <span className="text-yellow-400 font-semibold">🟡 Medium Priority</span>
                      </div>
                      <ul className="list-disc list-inside space-y-1 text-gray-300 ml-4">
                        {results.concerns_and_recommendations.mediumPriority.map((item: string, index: number) => (
                          <li key={index}><Markdown inline>{item}</Markdown></li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Consider Testing */}
                  {results.concerns_and_recommendations.considerTesting && results.concerns_and_recommendations.considerTesting.length > 0 && (
                    <div>
                      <div className="flex items-center space-x-2 mb-2">
                        <span className="text-accent-blue font-semibold">🔵 Consider Testing</span>
                      </div>
                      <ul className="list-disc list-inside space-y-1 text-gray-300 ml-4">
                        {results.concerns_and_recommendations.considerTesting.map((item: string, index: number) => (
                          <li key={index}><Markdown inline>{item}</Markdown></li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </GlassCard>
            </motion.div>
          )}
        </motion.div>

        {/* Generated Strategy Code */}
        {results.strategy_code && (
          <motion.div
            initial={{ opacity: 0, y: 50 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.6 }}
            className="mb-12"
          >
            <GlassCard className="p-8">
              <details>
                <summary className="text-2xl font-semibold text-white cursor-pointer flex items-center space-x-3">
                  <span>📝</span>
                  <span>Generated Strategy Code</span>
                </summary>
                <pre className="mt-6 p-4 bg-black/50 rounded-lg overflow-x-auto text-sm text-green-400 leading-relaxed whitespace-pre-wrap">
                  <code>{results.strategy_code
                    .replace(/^["']|["']$/g, '')
                    .replace(/```python\\n|```\\n|```python\n|```\n|```/g, '')
                    .replace(/\\n/g, '\n')
                    .trim()
                  }</code>
                </pre>
              </details>
            </GlassCard>
          </motion.div>
        )}

        {/* Transaction Log */}
        {results.trades && results.trades.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 50 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.65 }}
            className="mb-12"
          >
            <GlassCard className="p-8">
              <h3 className="text-2xl font-semibold text-white mb-4">📊 Transaction Log</h3>

              {/* Trade Summary */}
              {results.trade_summary && (
                <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
                  <div className="bg-white/5 rounded-lg p-3 text-center">
                    <div className="text-gray-400 text-xs">Total Trades</div>
                    <div className="text-xl font-bold text-white">{results.trade_summary.total_closed}</div>
                  </div>
                  <div className="bg-white/5 rounded-lg p-3 text-center">
                    <div className="text-gray-400 text-xs">Won</div>
                    <div className="text-xl font-bold text-accent-green">{results.trade_summary.won}</div>
                  </div>
                  <div className="bg-white/5 rounded-lg p-3 text-center">
                    <div className="text-gray-400 text-xs">Lost</div>
                    <div className="text-xl font-bold text-red-400">{results.trade_summary.lost}</div>
                  </div>
                  <div className="bg-white/5 rounded-lg p-3 text-center">
                    <div className="text-gray-400 text-xs">Win Rate</div>
                    <div className="text-xl font-bold text-accent-purple">{results.trade_summary.win_rate}%</div>
                  </div>
                  <div className="bg-white/5 rounded-lg p-3 text-center">
                    <div className="text-gray-400 text-xs">Open</div>
                    <div className="text-xl font-bold text-accent-blue">{results.trade_summary.total_open}</div>
                  </div>
                </div>
              )}

              {/* Trades Table */}
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-white/10">
                      <th className="text-left text-gray-400 font-medium py-3 px-2">#</th>
                      <th className="text-left text-gray-400 font-medium py-3 px-2">Direction</th>
                      <th className="text-left text-gray-400 font-medium py-3 px-2">Entry Date</th>
                      <th className="text-left text-gray-400 font-medium py-3 px-2">Exit Date</th>
                      <th className="text-right text-gray-400 font-medium py-3 px-2">Entry $</th>
                      <th className="text-right text-gray-400 font-medium py-3 px-2">Exit $</th>
                      <th className="text-right text-gray-400 font-medium py-3 px-2">Shares</th>
                      <th className="text-right text-gray-400 font-medium py-3 px-2">P&L</th>
                      <th className="text-right text-gray-400 font-medium py-3 px-2">P&L %</th>
                      <th className="text-right text-gray-400 font-medium py-3 px-2">Hold Days</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.trades.map((trade: Trade, index: number) => {
                      const holdDays = Math.round(
                        (new Date(trade.exit_date).getTime() - new Date(trade.entry_date).getTime()) / (1000 * 60 * 60 * 24)
                      );
                      return (
                        <tr key={index} className="border-b border-white/5 hover:bg-white/5 transition-colors">
                          <td className="py-3 px-2 text-gray-300">{index + 1}</td>
                          <td className="py-3 px-2">
                            <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                              trade.direction === 'LONG' ? 'bg-accent-green/20 text-accent-green' : 'bg-red-400/20 text-red-400'
                            }`}>
                              {trade.direction}
                            </span>
                          </td>
                          <td className="py-3 px-2 text-gray-300">{trade.entry_date}</td>
                          <td className="py-3 px-2 text-gray-300">{trade.exit_date}</td>
                          <td className="py-3 px-2 text-right text-gray-300">${trade.entry_price.toFixed(2)}</td>
                          <td className="py-3 px-2 text-right text-gray-300">${trade.exit_price.toFixed(2)}</td>
                          <td className="py-3 px-2 text-right text-gray-300">{trade.shares}</td>
                          <td className={`py-3 px-2 text-right font-medium ${trade.pnl >= 0 ? 'text-accent-green' : 'text-red-400'}`}>
                            {trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)}
                          </td>
                          <td className={`py-3 px-2 text-right font-medium ${trade.pnl_pct >= 0 ? 'text-accent-green' : 'text-red-400'}`}>
                            {trade.pnl_pct >= 0 ? '+' : ''}{trade.pnl_pct.toFixed(2)}%
                          </td>
                          <td className="py-3 px-2 text-right text-gray-300">{holdDays}d</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </GlassCard>
          </motion.div>
        )}

        {/* How the run went, step by step: kept after it finishes so a slow
            or skipped step can still be seen. */}
        {steps.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 50 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.68 }}
            className="mb-12"
          >
            <GlassCard className="p-8">
              <details>
                <summary className="text-2xl font-semibold text-white cursor-pointer flex items-center space-x-3">
                  <span>⏱️</span>
                  <span>Pipeline Steps</span>
                  <span className="text-base font-normal text-gray-400">{totalSeconds.toFixed(1)}s</span>
                </summary>
                <div className="mt-6">
                  <PipelineProgress steps={steps} finished />
                </div>
              </details>
            </GlassCard>
          </motion.div>
        )}

        {/* Actions */}
        <motion.div
          initial={{ opacity: 0, y: 50 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.7 }}
          className="flex justify-center gap-4"
        >
          <AnimatedButton
            onClick={handleNewStrategy}
            variant="accent"
            size="lg"
            glow={true}
            className="text-xl px-8 py-4"
          >
            🚀 Try Another Strategy
          </AnimatedButton>
          <AnimatedButton
            onClick={() => router.push('/chat')}
            variant="primary"
            size="lg"
            className="text-xl px-8 py-4"
          >
            💬 Chat with Quant Assistant
          </AnimatedButton>
        </motion.div>

        {/* Version Information - subtle, at bottom */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.5, delay: 1.0 }}
          className="mt-8 text-center text-xs text-gray-500"
        >
          <div className="inline-block">
            Frontend: {FRONTEND_VERSION}
            {results.versions && (
              <>
                {' | '}
                Backend Agents:
                {' '}Quant: {results.versions.quant_agent ?? '—'}
                {' | '}Strategy: {results.versions.strategy_generator ?? '—'}
                {' | '}Summary: {results.versions.results_summary ?? '—'}
              </>
            )}
          </div>
        </motion.div>
      </div>
    </div>
  );
}

export default function ResultsDisplay() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-gradient-to-br from-dark-primary via-dark-secondary to-dark-tertiary flex items-center justify-center">
        <LoadingSpinner size="lg" text="Loading results..." />
      </div>
    }>
      <ResultsDisplayContent />
    </Suspense>
  );
}
