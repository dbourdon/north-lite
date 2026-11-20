// GitHub glue: inputs, job summary, outputs, failure. Logic lives in compare.ts.
import { existsSync, readFileSync } from "node:fs";
import * as core from "@actions/core";
import { compare, validateJson } from "./compare";

function readSummary(path: string, label: string) {
  try {
    return validateJson(readFileSync(path, "utf8"));
  } catch (error) {
    throw new Error(`${label} ${path}: ${(error as Error).message}`);
  }
}

export async function run(): Promise<void> {
  try {
    const summaryPath = core.getInput("summary", { required: true });
    const baselinePath = core.getInput("baseline", { required: true });
    const maxRegressionPct = Number(core.getInput("max-regression-pct") || "15");
    const minDeltaMs = Number(core.getInput("min-delta-ms") || "20");
    if (!Number.isFinite(maxRegressionPct) || !Number.isFinite(minDeltaMs)) {
      throw new Error("max-regression-pct and min-delta-ms must be numbers");
    }

    const current = readSummary(summaryPath, "summary");
    // Only a missing file means "no baseline"; an unreadable or invalid one is an error.
    const baseline = existsSync(baselinePath) ? readSummary(baselinePath, "baseline") : null;

    const result = compare(baseline, current, { maxRegressionPct, minDeltaMs });

    await core.summary
      .addHeading(`bench-gate: ${summaryPath}`, 3)
      .addTable([
        [
          { data: "metric", header: true },
          { data: "baseline", header: true },
          { data: "current", header: true },
          { data: "change", header: true },
        ],
        ...result.rows.map((r) => [r.metric, r.baseline, r.current, r.change]),
      ])
      .addRaw(`<p><b>${result.regressed ? "FAIL" : "PASS"}</b>: ${result.reason}</p>`)
      .write();

    core.setOutput("regressed", String(result.regressed));
    core.setOutput("p95-change-pct", result.p95ChangePct);

    if (baseline === null) {
      core.notice(`No baseline at ${baselinePath}; commit this run's summary as the baseline.`);
    } else if (result.regressed) {
      core.setFailed(result.reason);
    } else {
      core.info(result.reason);
    }
  } catch (error) {
    core.setFailed((error as Error).message);
  }
}

void run();
