# Benchmark baselines

One `<profile>.json` per flag profile, in the summary format `loadtest/chat.js`
writes. The `bench gate` step in CI compares each e2e leg's run against its file.

- **Source:** taken from CI runs (the `bench-<profile>` artifacts), not a laptop,
  so the baseline matches the hardware it's compared on.
- **Updating:** a PR that replaces the file, with the reason in the description
  (e.g. "accept +8 ms p95 for rerank v5"). Never update a baseline in the same PR
  that causes the regression without saying so.
- **No file yet:** the gate passes and prints the current numbers to copy in.
