'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import GlassCard from '@/components/ui/GlassCard';
import GlassInput from '@/components/ui/GlassInput';
import GlassSelect from '@/components/ui/GlassSelect';
import AnimatedButton from '@/components/ui/AnimatedButton';
import { Coverage, FALLBACK_COVERAGE, STOCK_NAMES, ValidationResult, WINDOW_ORDER, windowsFor } from '@/types/strategy';
import { FRONTEND_VERSION } from '@/lib/version';

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || '';

export default function StrategyBuilder() {
  const router = useRouter();
  const [formData, setFormData] = useState({
    name: 'My Trading Strategy',
    stock_symbol: 'AMZN',
    backtest_window: '10Y',
    position_pct: 95,
    stop_loss: 10,
    take_profit: 30,
    buy_conditions: '10 SMA crosses above 30 SMA',
    sell_conditions: '10 SMA crosses below 30 SMA'
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidationResult>({
    isValid: true,
    errors: []
  });

  // What the market-data table actually holds, published by the ingest after
  // every run. The fallback covers the moment before this answers, or an outage.
  const [coverage, setCoverage] = useState<Coverage>(FALLBACK_COVERAGE);

  useEffect(() => {
    fetch(`${BASE_PATH}/api/coverage`)
      .then(r => (r.ok ? r.json() : null))
      .then(data => { if (data?.symbols) setCoverage(data.symbols); })
      .catch(() => { /* keep the fallback */ });
  }, []);

  // If the loaded coverage no longer backs the selected window, clamp it.
  useEffect(() => {
    const allowed = windowsFor(coverage[formData.stock_symbol]?.start);
    if (!allowed.includes(formData.backtest_window)) {
      setFormData(prev => ({ ...prev, backtest_window: allowed[allowed.length - 1] }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverage]);

  // Offer exactly the symbols the table holds, known names first.
  const symbols = [
    ...Object.keys(STOCK_NAMES).filter(s => s in coverage),
    ...Object.keys(coverage).filter(s => !(s in STOCK_NAMES)).sort(),
  ];
  const stockOptions = symbols.map(symbol => ({
    value: symbol,
    label: `${symbol} - ${STOCK_NAMES[symbol] ?? symbol}`
  }));

  const WINDOW_LABELS: Record<string, string> = {
    '1M': '1 Month', '3M': '3 Months', '6M': '6 Months', '1Y': '1 Year',
    '2Y': '2 Years', '5Y': '5 Years', '10Y': '10 Years', '20Y': '20 Years'
  };

  // Offer only windows the selected symbol has data for. AMZN reaches back to
  // 2000; the others start 2021-08-17, so a longer window would quietly
  // backtest a shorter period than the label promises.
  const symbolCoverage = coverage[formData.stock_symbol];
  const windowOptions = windowsFor(symbolCoverage?.start).map(w => ({
    value: w,
    label: WINDOW_LABELS[w]
  }));

  const handleInputChange = (field: keyof typeof formData, value: string | number) => {
    const next = { ...formData, [field]: value };

    // Switching to a shorter-history symbol can strand the current window
    // (e.g. AMZN 20Y -> NVDA). Clamp to the longest window the new symbol
    // supports rather than submitting a request it cannot satisfy.
    if (field === 'stock_symbol') {
      const allowed = windowsFor(coverage[String(value)]?.start);
      if (!allowed.includes(next.backtest_window)) {
        next.backtest_window = allowed[allowed.length - 1];
      }
    }

    setFormData(next);
    validateForm(next);
  };

  const validateForm = (data: typeof formData = formData): ValidationResult => {
    const errors: string[] = [];

    if (!data.name?.trim()) errors.push('Strategy name is required');
    if (!data.stock_symbol) errors.push('Please select a stock');
    if (!data.buy_conditions?.trim()) errors.push('Buy conditions are required');
    if (!data.sell_conditions?.trim()) errors.push('Sell conditions are required');
    if (data.position_pct < 1 || data.position_pct > 95) errors.push('Position size must be between 1% and 95%');
    if (data.stop_loss < 0 || data.stop_loss > 100) errors.push('Stop loss must be between 0 and 100');
    if (data.take_profit < 0 || data.take_profit > 100) errors.push('Take profit must be between 0 and 100');

    const result = { isValid: errors.length === 0, errors };
    setValidation(result);
    return result;
  };

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    
    if (!validateForm().isValid) return;

    setIsSubmitting(true);
    setSubmitError(null);

    try {
      // Start the backtest job
      const response = await fetch(`${BASE_PATH}/api/execute-backtest-async`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      });

      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        // The server's own message — e.g. the daily capacity notice on a 429 —
        // rather than a generic failure the visitor can't act on.
        setSubmitError(body.error || `Couldn't start the backtest (HTTP ${response.status}). Please try again.`);
        setIsSubmitting(false);
        return;
      }

      const { jobId } = body;
      console.log('[StrategyBuilder] ✅ Job started, ID:', jobId);
      
      // The results page shows the steps as they run, then the results. The
      // strategy travels with the job, so the job id is all it needs.
      router.push(`/results?jobId=${jobId}`);
      
    } catch (error) {
      console.error('[StrategyBuilder] ❌ Error:', error);
      setSubmitError("Couldn't reach the server. Check your connection and try again.");
      setIsSubmitting(false);
    }
  };

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
            🎯 Trading Strategy Backtesting
          </h1>
          <p className="text-xl text-gray-300 max-w-3xl">
            Multi-agent trading strategy backtesting with Pydantic AI and Amazon Bedrock AgentCore
          </p>
          <div className="mt-4">
            <a
              href={`${BASE_PATH}/chat`}
              className="inline-flex items-center text-accent-blue hover:text-accent-purple transition-colors text-sm border border-accent-blue/30 hover:border-accent-purple/30 rounded-lg px-4 py-2"
            >
              💬 Chat with Quant Assistant — Analyze past backtests &amp; get improvement suggestions
            </a>
          </div>
        </motion.div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {/* Main Form */}
          <motion.div
            className="lg:col-span-2"
            initial={{ opacity: 0, x: -50 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.8, delay: 0.2 }}
          >
            <GlassCard className="p-8">
              <form onSubmit={handleSubmit} className="space-y-6">
                {/* Row 1: Stock Selection & Backtest Window */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <GlassSelect
                    label="📈 Stock Symbol"
                    options={stockOptions}
                    value={formData.stock_symbol}
                    onChange={(value) => handleInputChange('stock_symbol', value)}
                  />

                  <div>
                    <GlassSelect
                      label="📅 Backtest Window"
                      options={windowOptions}
                      value={formData.backtest_window}
                      onChange={(value) => handleInputChange('backtest_window', value)}
                    />
                    {symbolCoverage && (
                      <p className="mt-2 text-xs text-white/50">
                        Data available {symbolCoverage.start} to {symbolCoverage.end}
                        {windowOptions.length < WINDOW_ORDER.length &&
                          ` — windows beyond ${windowOptions[windowOptions.length - 1].label} aren't offered for ${formData.stock_symbol}`}
                      </p>
                    )}
                  </div>
                </div>

                {/* Row 2: Position Size & Stop Loss */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <GlassInput
                    label="📐 Position Size (% of cash)"
                    type="number"
                    min={1}
                    max={95}
                    value={formData.position_pct}
                    onChange={(e) => handleInputChange('position_pct', parseInt(e.target.value) || 1)}
                    error={validation.errors.find(e => e.includes('Position size'))}
                  />

                  <GlassInput
                    label="🛑 Stop Loss (%)"
                    type="number"
                    min={0}
                    max={100}
                    step={0.5}
                    value={formData.stop_loss}
                    onChange={(e) => handleInputChange('stop_loss', parseFloat(e.target.value) || 0)}
                  />
                </div>

                {/* Row 3: Take Profit & Strategy Name */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <GlassInput
                    label="💰 Take Profit (%)"
                    type="number"
                    min={0}
                    max={100}
                    step={0.5}
                    value={formData.take_profit}
                    onChange={(e) => handleInputChange('take_profit', parseFloat(e.target.value) || 0)}
                  />

                  <GlassInput
                    label="📝 Strategy Name"
                    value={formData.name}
                    onChange={(e) => handleInputChange('name', e.target.value)}
                    placeholder="e.g., EMA Crossover Strategy"
                    error={validation.errors.find(e => e.includes('name'))}
                  />
                </div>

                {/* Row 4: Buy Conditions & Sell Conditions */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <GlassInput
                    label="📈 Buy Conditions"
                    value={formData.buy_conditions}
                    onChange={(e) => handleInputChange('buy_conditions', e.target.value)}
                    placeholder="e.g., Price above 20-day moving average and RSI below 70"
                    error={validation.errors.find(e => e.includes('Buy'))}
                  />

                  <GlassInput
                    label="📉 Sell Conditions"
                    value={formData.sell_conditions}
                    onChange={(e) => handleInputChange('sell_conditions', e.target.value)}
                    placeholder="e.g., Price below 20-day moving average or RSI above 80"
                    error={validation.errors.find(e => e.includes('Sell'))}
                  />
                </div>

                {/* Submit Button */}
                <div className="pt-6">
                  <AnimatedButton
                    onClick={handleSubmit}
                    variant="accent"
                    size="lg"
                    disabled={!validation.isValid || isSubmitting}
                    loading={isSubmitting}
                    glow={validation.isValid}
                    className="w-full text-xl py-4"
                  >
                    {isSubmitting ? 'Starting Backtest...' : '🚀 Run Backtest'}
                  </AnimatedButton>
                  {submitError && (
                    <p className="mt-3 rounded-lg border border-amber-400/30 bg-amber-400/5 px-4 py-2 text-sm text-amber-200/90">
                      {submitError}
                    </p>
                  )}
                </div>
              </form>
            </GlassCard>
          </motion.div>

          {/* Preview Sidebar */}
          <motion.div
            className="space-y-6"
            initial={{ opacity: 0, x: 50 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.8, delay: 0.4 }}
          >
            {/* Strategy Preview */}
            <GlassCard className="p-6">
              <h3 className="text-xl font-semibold text-white mb-4">📋 Strategy Preview</h3>
              <div className="space-y-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-gray-400">Name:</span>
                  <span className="text-white font-medium">{formData.name}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Stock:</span>
                  <span className="text-white font-medium">{formData.stock_symbol}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Window:</span>
                  <span className="text-white font-medium">{formData.backtest_window}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Position Size:</span>
                  <span className="text-white font-medium">{formData.position_pct}% of cash</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Stop Loss:</span>
                  <span className="text-red-400 font-medium">{formData.stop_loss}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Take Profit:</span>
                  <span className="text-accent-green font-medium">{formData.take_profit}%</span>
                </div>
                <div className="border-t border-gray-600 pt-3 mt-3">
                  <div className="mb-2">
                    <span className="text-gray-400">Buy:</span>
                    <p className="text-white text-xs mt-1">{formData.buy_conditions}</p>
                  </div>
                  <div>
                    <span className="text-gray-400">Sell:</span>
                    <p className="text-white text-xs mt-1">{formData.sell_conditions}</p>
                  </div>
                </div>
              </div>
            </GlassCard>

            {/* Validation Status */}
            <GlassCard className={`p-6 ${validation.isValid ? 'border-accent-green/30' : 'border-red-400/30'}`}>
              <div className="flex items-center space-x-3">
                <div className={`w-3 h-3 rounded-full ${validation.isValid ? 'bg-accent-green' : 'bg-red-400'}`} />
                <span className={`font-medium ${validation.isValid ? 'text-accent-green' : 'text-red-400'}`}>
                  {validation.isValid ? 'Strategy Ready' : 'Please Fix Errors'}
                </span>
              </div>
              {validation.errors.length > 0 && (
                <div className="mt-3 space-y-1">
                  {validation.errors.map((error, index) => (
                    <p key={index} className="text-red-400 text-sm">• {error}</p>
                  ))}
                </div>
              )}
            </GlassCard>
          </motion.div>
        </div>
      </div>
      <div className="mt-8 text-center text-xs text-gray-500">
        Frontend: {FRONTEND_VERSION}
      </div>
    </div>
  );
}