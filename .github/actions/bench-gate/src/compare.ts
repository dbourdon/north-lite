// Pure comparison logic for bench-gate: no @actions/core, so it's unit-testable.

export interface LatencyStats {
  avg: number;
  p50: number;
  p90: number;
  p95: number;
  p99: number;
  max: number;
}

export interface SummaryJson {
  schema: number;
  requests: number;
  rps: number;
  error_rate: number;
  latency_ms: LatencyStats;
}

export interface CompareOptions {
  maxRegressionPct: number;
  minDeltaMs: number;
}

export interface Row {
  metric: string;
  baseline: string;
  current: string;
  change: string;
}

export interface CompareResult {
  regressed: boolean;
  /** p95 change in percent, e.g. "-3.2"; "" without a baseline (or a 0 ms baseline) */
  p95ChangePct: string;
  p95DeltaMs: number;
  /** Which rule decided the outcome, for the job summary and the failure message. */
  reason: string;
  rows: Row[];
}

export function validateJson(input: string): SummaryJson {
  let parsed: unknown;
  try {
    parsed = JSON.parse(input);
  } catch (error) {
    throw new Error(`Invalid JSON: ${(error as Error).message}`);
  }
  const s = parsed as Partial<SummaryJson>;
  if (s.schema !== 1) {
    throw new Error(`Invalid schema: expected 1, got ${String(s.schema)}`);
  }
  if (typeof s.latency_ms?.p95 !== "number" || typeof s.error_rate !== "number") {
    throw new Error("Invalid summary: latency_ms.p95 and error_rate must be numbers");
  }
  return s as SummaryJson;
}

/** Percent change, or null when the baseline is 0 (a percentage is meaningless). */
export function pctChange(baseline: number, current: number): number | null {
  return baseline === 0 ? null : ((current - baseline) / baseline) * 100;
}

const ms = (v: number) => `${v.toFixed(1)} ms`;
const signed = (v: number, digits = 1) => `${v >= 0 ? "+" : ""}${v.toFixed(digits)}`;

function latencyRow(metric: keyof LatencyStats, base: SummaryJson, cur: SummaryJson): Row {
  const b = base.latency_ms[metric];
  const c = cur.latency_ms[metric];
  const pct = pctChange(b, c);
  const change = `${signed(c - b)} ms` + (pct === null ? "" : ` (${signed(pct)}%)`);
  return { metric, baseline: ms(b), current: ms(c), change };
}

function errorRow(base: SummaryJson, cur: SummaryJson): Row {
  // Error rates are fractions; show percentage points, never "% of 0".
  const pp = (cur.error_rate - base.error_rate) * 100;
  const fmt = (r: number) => `${(r * 100).toFixed(2)}%`;
  return {
    metric: "error_rate",
    baseline: fmt(base.error_rate),
    current: fmt(cur.error_rate),
    change: `${signed(pp, 2)} pp`,
  };
}

export function currentRows(cur: SummaryJson): Row[] {
  const latency = (["p50", "p90", "p95", "p99"] as const).map((m) => ({
    metric: m,
    baseline: "–",
    current: ms(cur.latency_ms[m]),
    change: "–",
  }));
  return [
    ...latency,
    { metric: "error_rate", baseline: "–", current: `${(cur.error_rate * 100).toFixed(2)}%`, change: "–" },
  ];
}

/**
 * Regressed only when p95 grew by more than `maxRegressionPct` percent AND by more
 * than `minDeltaMs` milliseconds. Both are needed: on a ~3 ms baseline, +15% is
 * 0.45 ms of runner noise; on a 2 s baseline, +20 ms is noise too.
 */
export function compare(
  baseline: SummaryJson | null,
  current: SummaryJson,
  { maxRegressionPct, minDeltaMs }: CompareOptions,
): CompareResult {
  if (baseline === null) {
    return {
      regressed: false,
      p95ChangePct: "",
      p95DeltaMs: 0,
      reason: "no baseline: nothing to compare against",
      rows: currentRows(current),
    };
  }

  const delta = current.latency_ms.p95 - baseline.latency_ms.p95;
  const pct = pctChange(baseline.latency_ms.p95, current.latency_ms.p95);
  const overPct = pct !== null && pct > maxRegressionPct;
  const overMs = delta > minDeltaMs;
  const regressed = overPct && overMs;

  const describe = `p95 ${pct === null ? "n/a" : signed(pct)}% (${signed(delta)} ms)`;
  let reason: string;
  if (regressed) reason = `${describe} exceeds both limits (${maxRegressionPct}% and ${minDeltaMs} ms)`;
  else if (overPct) reason = `${describe}: over ${maxRegressionPct}% but within the ${minDeltaMs} ms noise floor`;
  else if (overMs) reason = `${describe}: over ${minDeltaMs} ms but within ${maxRegressionPct}%`;
  else reason = `${describe}: within limits`;

  return {
    regressed,
    p95ChangePct: pct === null ? "" : pct.toFixed(1),
    p95DeltaMs: delta,
    reason,
    rows: [
      ...(["p50", "p90", "p95", "p99"] as const).map((m) => latencyRow(m, baseline, current)),
      errorRow(baseline, current),
    ],
  };
}
