// Strategy input matching your agent's expected format
export interface StrategyInput {
  name: string;
  stock_symbol: string;
  backtest_window: string;
  position_pct: number; // % of available cash per buy (1-95)
  stop_loss: number;
  take_profit: number;
  buy_conditions: string;
  sell_conditions: string;
}

// AgentCore response from API route
export interface AgentCoreResponse {
  success: boolean;
  analysis: string;
  raw_response?: any;
  session_id?: string;
  error?: string;
  error_type?: string;
}

// Agent output format (parsed from AgentCore response)
export interface AgentOutput {
  initial_investment: string;
  final_portfolio_value: string;
  total_return: string;
  maximum_drawdown: string;
  symbol: string;
  strategy_type: string;
  stop_loss: string;
  take_profit: string;
  position_pct: number;
  buy_conditions?: string;
  sell_conditions?: string;
  backtest_window?: string;
  profit_loss?: string;
  sharpe_ratio?: string;
  executive_summary?: string;
  detailed_analysis?: string;
  concerns_and_recommendations?: {
    highPriority?: string[];
    mediumPriority?: string[];
    considerTesting?: string[];
  };
  analysis_text?: string; // Full markdown analysis from agent
  strategy_code?: string; // Generated Backtrader strategy Python code
  trades?: Trade[];
  trade_summary?: TradeSummary;
  data_warnings?: string[]; // Gaps between the requested window and the data the backtest ran on
  versions?: {
    quant_agent: string;
    strategy_generator: string;
    results_summary: string;
  };
}

// Individual trade record from Backtrader
export interface Trade {
  entry_date: string;
  exit_date: string;
  entry_price: number;
  exit_price: number;
  shares: number;
  pnl: number;
  pnl_pct: number;
  commission: number;
  direction: 'LONG' | 'SHORT';
}

// Trade summary statistics
export interface TradeSummary {
  total_trades: number;
  total_open: number;
  total_closed: number;
  won: number;
  lost: number;
  win_rate: number;
}

// Complete backtest record stored in AgentCore Memory
export interface BacktestMemoryRecord {
  timestamp: string;
  symbol: string;
  performance: {
    initial_value: number;
    final_value: number;
    total_return: number;
    metrics: Record<string, string>;
    strategy_class: string;
  };
  trade_summary: TradeSummary;
  trades: Trade[];
  strategy_code: string | null;
}

// Display names for the symbols the pipeline loads. Which symbols are offered,
// and for which windows, comes from the data itself via /api/coverage.
export const STOCK_NAMES: Record<string, string> = {
  AMZN: 'Amazon.com Inc.',
  NVDA: 'NVIDIA Corporation',
  MSFT: 'Microsoft Corporation',
  TSLA: 'Tesla, Inc.',
  SPY: 'SPDR S&P 500 ETF'
};

/** Stored date range per symbol, as published by the ingest pipeline. */
export type Coverage = Record<string, { start: string; end: string; rows?: number }>;

/**
 * Used only until /api/coverage answers, or if it can't: the ranges as of
 * September 2026. The live values come from the pipeline after every run.
 */
export const FALLBACK_COVERAGE: Coverage = {
  AMZN: { start: '2000-01-03', end: '2026-09-29' },
  NVDA: { start: '2021-08-17', end: '2026-09-29' },
  MSFT: { start: '2021-08-17', end: '2026-09-29' },
  TSLA: { start: '2021-08-17', end: '2026-09-29' },
  SPY: { start: '2021-08-17', end: '2026-09-29' }
};

export const WINDOW_ORDER = ['1M', '3M', '6M', '1Y', '2Y', '5Y', '10Y', '20Y'] as const;

// Calendar days per window — the same values the agent resolves dates from.
const WINDOW_DAYS: Record<string, number> = {
  '1M': 30, '3M': 91, '6M': 182, '1Y': 365, '2Y': 730, '5Y': 1825, '10Y': 3652, '20Y': 7305
};

/**
 * Windows the stored data can actually back for a symbol whose history starts
 * on `start`. Offering a longer window wouldn't fail — it would silently
 * backtest a shorter period than the label claims.
 */
export function windowsFor(start: string | undefined): string[] {
  if (!start) return ['1M', '3M', '6M', '1Y'];
  const historyDays = (Date.now() - new Date(start).getTime()) / 86_400_000;
  // A few days of slack so a window starting on a weekend still counts.
  return WINDOW_ORDER.filter(w => WINDOW_DAYS[w] <= historyDays + 5);
}

export interface ValidationResult {
  isValid: boolean;
  errors: string[];
}
