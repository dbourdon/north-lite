import { describe, expect, it } from "vitest";
import { compare, pctChange, validateJson, type SummaryJson } from "./compare";

const LIMITS = { maxRegressionPct: 15, minDeltaMs: 20 };

function summary(p95: number, errorRate = 0): SummaryJson {
  return {
    schema: 1,
    requests: 600,
    rps: 20,
    error_rate: errorRate,
    latency_ms: { avg: p95 * 0.8, p50: p95 * 0.7, p90: p95 * 0.9, p95, p99: p95 * 1.5, max: p95 * 3 },
  };
}

describe("compare", () => {
  it("passes without a baseline and reports current numbers only", () => {
    const r = compare(null, summary(3), LIMITS);
    expect(r.regressed).toBe(false);
    expect(r.p95ChangePct).toBe("");
    expect(r.rows.find((row) => row.metric === "p95")?.baseline).toBe("–");
  });

  it("passes at +10% even when that is +50 ms (under the percentage limit)", () => {
    const r = compare(summary(500), summary(550), LIMITS);
    expect(r.p95DeltaMs).toBeCloseTo(50);
    expect(r.regressed).toBe(false);
    expect(r.reason).toContain("within 15%");
  });

  it("passes at +40% when that is only +1 ms (under the noise floor)", () => {
    const r = compare(summary(2.5), summary(3.5), LIMITS);
    expect(Number(r.p95ChangePct)).toBeCloseTo(40);
    expect(r.regressed).toBe(false);
    expect(r.reason).toContain("noise floor");
  });

  it("fails at +40% and +30 ms (both limits exceeded)", () => {
    const r = compare(summary(75), summary(105), LIMITS);
    expect(r.regressed).toBe(true);
    expect(r.reason).toContain("exceeds both limits");
  });

  it("passes and reports a negative change when faster", () => {
    const r = compare(summary(10), summary(9), LIMITS);
    expect(r.regressed).toBe(false);
    expect(r.p95ChangePct).toBe("-10.0");
  });

  it("shows error-rate change in percentage points, not % of a zero baseline", () => {
    const r = compare(summary(3, 0), summary(3, 0.02), LIMITS);
    const row = r.rows.find((x) => x.metric === "error_rate");
    expect(row).toEqual({ metric: "error_rate", baseline: "0.00%", current: "2.00%", change: "+2.00 pp" });
  });

  it("never reports NaN or Infinity for a 0 ms baseline", () => {
    expect(pctChange(0, 5)).toBeNull();
    const r = compare(summary(0), summary(5), LIMITS);
    expect(r.p95ChangePct).toBe("");
    expect(r.regressed).toBe(false); // no percentage => the percentage rule can't trip
    expect(JSON.stringify(r)).not.toMatch(/NaN|Infinity/);
  });
});

describe("validateJson", () => {
  it("rejects invalid JSON", () => {
    expect(() => validateJson("not json")).toThrow("Invalid JSON");
  });

  it("rejects the wrong schema", () => {
    expect(() => validateJson(JSON.stringify({ ...summary(3), schema: 2 }))).toThrow("Invalid schema");
  });

  it("rejects a summary without p95", () => {
    expect(() => validateJson(JSON.stringify({ schema: 1, error_rate: 0, latency_ms: {} }))).toThrow(
      "latency_ms.p95",
    );
  });
});
