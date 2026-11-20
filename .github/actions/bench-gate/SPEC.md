# bench-gate: spec

A JavaScript GitHub Action written in **TypeScript** that compares a k6 benchmark
summary with a committed baseline and fails the job on a latency regression.

## Inputs (action.yml)

| Input | Required | Default | Meaning |
|---|---|---|---|
| `summary` | yes | | Path to the current run's summary JSON (written by `loadtest/chat.js`) |
| `baseline` | yes | | Path to the baseline JSON (same format) |
| `max-regression-pct` | no | `15` | Fail if p95 grew by more than this percentage... |
| `min-delta-ms` | no | `20` | ...**and** by more than this many milliseconds (noise floor) |

## Outputs

- `regressed`: `"true"` or `"false"`
- `p95-change-pct`: p95 change vs baseline in percent, e.g. `"-3.2"`; empty without a baseline

## Summary JSON format (schema 1)

```json
{
  "schema": 1,
  "requests": 600,
  "rps": 20.0,
  "error_rate": 0,
  "latency_ms": { "avg": 4.2, "p50": 2.4, "p90": 2.6, "p95": 2.8, "p99": 58.2, "max": 202.2 }
}
```

## Behaviour

1. Read and validate `summary`. Missing or invalid file, or `schema !== 1`: fail
   with a clear message.
2. If `baseline` doesn't exist: write the job summary with current numbers only,
   emit a `core.notice` ("no baseline for …; commit this summary as the baseline"),
   set `regressed=false`, and **pass**.
3. Otherwise compare p95:
   - `delta = current.p95 - baseline.p95`, `pct = delta / baseline.p95 * 100`
   - **regressed** when `pct > max-regression-pct` **and** `delta > min-delta-ms`.
     Both are needed: on a ~3 ms baseline, +15% is 0.45 ms of noise.
4. Write a job summary (`core.summary`) with a table: metric | baseline | current |
   change, for p50, p90, p95, p99 and error_rate, plus a PASS/FAIL line naming the
   rule that decided it.
5. On regression: `core.setFailed("p95 regressed X% (+Y ms) vs baseline; limits Z% and W ms")`.

## Structure

```
.github/actions/bench-gate/
  action.yml          runs: using: node24, main: dist/index.js
  package.json        deps: @actions/core; dev: typescript, vitest, esbuild, @types/node
  tsconfig.json       strict
  src/compare.ts      pure logic: parse + compare, no @actions/core (unit-testable)
  src/index.ts        reads inputs, calls compare, writes summary/outputs/failure
  src/compare.test.ts vitest
  dist/index.js       esbuild bundle, COMMITTED (Actions runs it directly)
```

Scripts: `npm run build` (esbuild bundle, platform node, target node24),
`npm test` (vitest), `npm run typecheck` (`tsc --noEmit`).

## Tests (minimum)

- no baseline → pass, `regressed=false`
- +10% and +50 ms → pass (under the percentage limit)
- +40% but +1 ms → pass (under the noise floor)
- +40% and +30 ms → regressed
- faster than baseline → pass, negative change
- invalid summary / wrong schema → error

## Done when

- `npm run typecheck && npm test && npm run build` pass, and `dist/index.js` is committed
- In CI, every e2e leg shows the table in its job summary
- Adding a `time.sleep(0.05)` to `/search` turns the gate red; removing it turns it green
