# Day 9: build bench-gate with North Mini Code

Goal: build `.github/actions/bench-gate` with Cohere's coding model through
OpenCode, and keep notes for the interview.

## Run it

```bash
cd ~/git/north-lite/.claude/worktrees/day9-bench-gate   # the Day 9 branch
scripts/opencode-cohere.sh                              # interactive; north-mini-code-1.0
```

Budget: ~250 calls (trial key, 1,000/month, **20 calls/min in total**). Each agent
step is one call; if it stalls on a 429, wait a minute. Prefer one clear prompt
over many small corrections.

## First prompt (paste as is)

> Read `.github/actions/bench-gate/SPEC.md` and implement it exactly, in that
> directory only. Put all comparison logic in `src/compare.ts` with no
> `@actions/core` import, and the GitHub glue in `src/index.ts`. Write the vitest
> tests listed in the spec. Then run `npm install`, `npm run typecheck`, `npm test`
> and `npm run build` in `.github/actions/bench-gate` and fix any failures. Don't
> touch files outside that directory. Summarize what you built at the end.

Then check by hand:

```bash
cd .github/actions/bench-gate && npm run typecheck && npm test && npm run build
node -e "require('./dist/index.js')" 2>&1 | head -3   # should complain about missing inputs, not crash on import
```

## Notes to keep (for the interview)

| Question | Your note |
|---|---|
| Calls used / time taken | |
| Did it follow the spec without being told twice? | |
| Test quality: did it cover the noise-floor case? | |
| What did you have to fix by hand? | |
| Compared with Claude Code on the same kind of task | |
| Would you use it for: scaffolding / refactors / reviews? | |

When it's done, ask Claude Code to review the diff, run the checks and open the PR.

## Review of the result (2026-11-20)

**What it got right:** followed the spec's structure exactly (pure logic in
`compare.ts`, glue in `index.ts`, correct `action.yml` inputs/outputs, node24),
wrote all six spec test cases, stayed inside its directory, and left typecheck
and tests green.

**What review found.** Typecheck and tests were green, yet the action could
not have run:

| # | Issue | Impact |
|---|---|---|
| 1 | `package.json` `"type": "module"` with a CommonJS esbuild bundle | Action crashed on start: `module is not defined in ES module scope` |
| 2 | `action.yml`: `\'` inside a single-quoted YAML string | Invalid YAML: GitHub would reject the action (caught by actionlint) |
| 3 | Job summary written by a `MockSummary` that only `console.log`s ("not in GitHub Actions") | No table in the job summary: the spec's main output |
| 4 | "change" column showed the p95 delta on the requests, rps and max rows | Wrong numbers in the table |
| 5 | error_rate change as % of a 0 baseline | `NaN%` with our real baselines |
| 6 | Test "+10% and +50 ms" actually tested +0.28 ms | The case the spec asked for wasn't covered |
| 7 | Inputs from raw `process.env`; any read error = "no baseline"; no guard for a 0 ms baseline | Fragile; a corrupt baseline would silently pass |
| 8 | `npm test` = `vitest` (watch mode) | Hangs outside CI |

**Lesson:** green unit tests didn't prove the artifact worked. The fixes added the
checks that would have caught each issue: run the built bundle the way Actions
does, actionlint on `action.yml`, a CI job that fails on a stale `dist/`, and tests
for the zero-baseline and noise-floor edges (10 tests, up from 7).
