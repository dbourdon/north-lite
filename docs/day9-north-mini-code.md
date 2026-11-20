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
