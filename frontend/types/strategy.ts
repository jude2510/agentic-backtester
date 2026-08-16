// Strategy input matching your agent's expected format
export interface StrategyInput {
  name: string;
  stock_symbol: string;
  backtest_window: string;
  max_positions: number;
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
  max_positions: number;
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

export interface StockOption {
  symbol: string;
  name: string;
}

// Only symbols actually loaded in the market-data table. Keep in sync with
// market-data-pipeline/symbols.json — listing a symbol here that hasn't been
// ingested produces an empty backtest rather than an error.
export const AVAILABLE_STOCKS: StockOption[] = [
  { symbol: 'AMZN', name: 'Amazon.com Inc.' },
  { symbol: 'NVDA', name: 'NVIDIA Corporation' },
  { symbol: 'MSFT', name: 'Microsoft Corporation' },
  { symbol: 'TSLA', name: 'Tesla, Inc.' },
  { symbol: 'SPY', name: 'SPDR S&P 500 ETF' }
];

/**
 * Longest backtest window each symbol can actually support.
 *
 * Coverage is deliberately asymmetric: AMZN carries 25 years from the original
 * CSV load, while the rest begin at the data provider's 5-year plan limit
 * (2021-08-17). Offering a longer window than a symbol can back would silently
 * return a shorter series — the backtest would run, look successful, and cover
 * a different period than the UI claims.
 */
export const WINDOW_ORDER = ['1M', '3M', '6M', '1Y', '2Y', '5Y', '10Y', '20Y'] as const;

export const SYMBOL_COVERAGE: Record<string, { start: string; maxWindow: string }> = {
  AMZN: { start: '2000-01-03', maxWindow: '20Y' },
  NVDA: { start: '2021-08-17', maxWindow: '5Y' },
  MSFT: { start: '2021-08-17', maxWindow: '5Y' },
  TSLA: { start: '2021-08-17', maxWindow: '5Y' },
  SPY: { start: '2021-08-17', maxWindow: '5Y' }
};

/** Windows valid for `symbol`, longest-supported first removed beyond coverage. */
export function windowsFor(symbol: string): string[] {
  const max = SYMBOL_COVERAGE[symbol]?.maxWindow ?? '5Y';
  const maxIdx = WINDOW_ORDER.indexOf(max as typeof WINDOW_ORDER[number]);
  return WINDOW_ORDER.slice(0, maxIdx + 1);
}

export interface ValidationResult {
  isValid: boolean;
  errors: string[];
}
