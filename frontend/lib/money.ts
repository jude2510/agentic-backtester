// One formatter for every dollar amount the results page shows. Building the
// string by hand gave "$-5199.3": the sign after the dollar sign, no
// separators, and a dropped trailing zero.
const USD = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** 100000 → "$100,000.00"; -5199.3 → "-$5,199.30". Missing or non-finite → "N/A". */
export function formatMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'N/A';
  // Rounds first so a tiny loss like -0.001 shows "$0.00", not "-$0.00".
  const rounded = Math.round(value * 100) / 100;
  return USD.format(rounded === 0 ? 0 : rounded);
}

/** Like formatMoney, with "+" on gains: "+$12.50", "-$3.00". */
export function formatSignedMoney(value: number | null | undefined): string {
  const text = formatMoney(value);
  return value != null && Number.isFinite(value) && Math.round(value * 100) > 0 ? `+${text}` : text;
}
