# botgauge evals

These measure how well botgauge finds real problems, how often it raises false alarms, and what a review costs. They're the same idea as the Claude Code team's eval set ("any incidents are now an eval"): every bug that slips through becomes a case, and every change to the plugin's prompts or models is scored against all of them.

```
python3 evals/run.py                 # all cases, once
python3 evals/run.py --case shop     # one case
python3 evals/run.py --runs 3        # three runs per case, to see the variance
python3 evals/run.py --baseline      # also run Claude Code's built-in /code-review, for comparison
```

Running the evals spends Claude usage: roughly $0.50–2 per case per run in API terms, and more with `--baseline`. Results go to `evals/results/`, which isn't committed.

## Cases

Each `cases/<id>.json` is one repo state with known problems, or none:

- `fixture`: a script that builds a throwaway repo at `$1/repo`. Use this for planted examples.
- `repo` (instead of `fixture`): `{"path", "commit", "base"}` reviews a real commit from one of our repos against its base. The runner reviews a fresh clone, never your working copy. See `real-bug.json.example`.
- `command`: the botgauge command to run (default `review-blockers`).
- `config`: an optional `.botgauge/config.json` to use, for example to run checks.
- `expect`: the problems a good review must find. Each entry has a `file`, an optional `lines` range, the acceptable `levels`, and `any_of`, keywords at least one of which must appear in the finding.

An empty `expect` means a clean change: any merge-blocker or high finding counts as unexpected, which is the false-alarm check.

## Adding a real bug

When a review bot, a teammate or production finds a bug that botgauge should have caught:
1. Find the commit that introduced it, and its parent (or the PR's base).
2. Copy `real-bug.json.example` to `cases/<short-name>.json` and fill it in.
3. Run `python3 evals/run.py --case <short-name>`. If botgauge misses it, improve the reviewer prompt or `REVIEW.md`, then run all the cases again. Keep the change only if the score holds or improves.
