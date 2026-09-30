'use client';

import { useEffect, useState } from 'react';
import { PipelineStep, STEP_ORDER, StepName, StepStatus } from '@/types/strategy';

export const STEP_LABELS: Record<StepName, string> = {
  prepare: 'Check the inputs and pick the dates',
  generate_strategy: 'Write the strategy code',
  fetch_market_data: 'Fetch market data',
  run_backtest: 'Run the backtest',
  summarize: 'Write the analysis',
};

const STEP_BY: Record<StepName, string> = {
  prepare: 'Code',
  generate_strategy: 'Strategy generator agent, then the sandbox safety check',
  fetch_market_data: 'AgentCore Gateway',
  run_backtest: 'Backtrader',
  summarize: 'Results summary agent',
};

type Shown = StepStatus | 'pending' | 'interrupted';

const STYLE: Record<Shown, { icon: string; tone: string; ring: string; word: string }> = {
  pending: { icon: '', tone: 'text-gray-500', ring: 'border-white/15', word: 'Waiting' },
  running: { icon: '', tone: 'text-accent-blue', ring: 'border-accent-blue/60', word: 'Running' },
  ok: { icon: '✓', tone: 'text-accent-green', ring: 'border-accent-green/60', word: 'Done' },
  empty: { icon: '○', tone: 'text-amber-300', ring: 'border-amber-300/60', word: 'Nothing found' },
  failed: { icon: '✕', tone: 'text-red-400', ring: 'border-red-400/60', word: 'Failed' },
  timeout: { icon: '✕', tone: 'text-red-400', ring: 'border-red-400/60', word: 'Timed out' },
  interrupted: { icon: '✕', tone: 'text-red-400', ring: 'border-red-400/60', word: 'Interrupted' },
  skipped: { icon: '–', tone: 'text-gray-500', ring: 'border-white/15', word: 'Skipped' },
};

interface Props {
  steps: PipelineStep[];
  /** The job has ended, so a step still marked running never finished. */
  finished?: boolean;
  /** When the job started (ms), for the running clock. */
  startTime?: number;
}

/**
 * The backtest's steps with the status each one actually reported. Nothing
 * here is simulated: a step shows as running only once the agent says so.
 */
export default function PipelineProgress({ steps, finished = false, startTime }: Props) {
  const reported = new Map(steps.map(s => [s.step, s]));
  const elapsed = useElapsedSeconds(finished ? undefined : startTime);

  return (
    <div>
      {elapsed != null && (
        <p className="mb-5 text-sm text-gray-400">Running for {elapsed}s</p>
      )}
      <ol className="space-y-5">
        {STEP_ORDER.map(name => {
          const step = reported.get(name);
          let shown: Shown = step?.status ?? 'pending';
          if (finished && shown === 'running') shown = 'interrupted';
          const style = STYLE[shown];
          const seconds = step?.duration_ms != null ? `${(step.duration_ms / 1000).toFixed(1)}s` : null;
          const quiet = shown === 'pending' || shown === 'skipped';

          return (
            <li key={name} className="flex items-start gap-4">
              <div className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border ${style.ring}`}>
                {shown === 'running' ? (
                  <span className="h-3.5 w-3.5 rounded-full border-2 border-accent-blue border-t-transparent animate-spin" />
                ) : (
                  <span className={`text-sm ${style.tone}`}>{style.icon}</span>
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-x-4">
                  <span className={quiet ? 'text-gray-400' : 'font-medium text-white'}>
                    {STEP_LABELS[name]}
                  </span>
                  <span className={`text-sm ${style.tone}`}>
                    {[style.word, seconds].filter(Boolean).join(' · ')}
                  </span>
                </div>
                <div className="text-xs text-gray-500">{STEP_BY[name]}</div>
                {step?.detail && (
                  <p className={`mt-1 break-words text-sm ${style.tone === 'text-red-400' ? 'text-red-300' : 'text-gray-300'}`}>
                    {step.detail}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function useElapsedSeconds(startTime?: number): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!startTime) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [startTime]);
  return startTime ? Math.max(0, Math.round((now - startTime) / 1000)) : null;
}
