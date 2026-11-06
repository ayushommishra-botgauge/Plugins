---
name: review-medium
description: Review this repo's unpushed changes for medium issues (duplication, reimplemented helpers, complexity, missing docstrings or changelog, weak tests, repeated work). Any merge blocker it notices is shown too.
argument-hint: "[areas…] [--quick|--deep] [--full] [path]"
disable-model-invocation: true
model: haiku
allowed-tools:
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" *)
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/bg.py *)
  - Read
  - Grep
  - Glob
---

# botgauge review: medium

Run exactly this command, as one Bash tool call with `timeout` 600000, and nothing before it:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" review --level medium $ARGUMENTS
```

It does the whole review: the repo's checks, reviewers in parallel, a vote on every merge blocker, saving the findings, and the report. Lines starting with `botgauge:` are progress; the report follows them and ends with a `Cost:` line.

Then:
- Show the report exactly as printed, inside a ```text block. Add at most one sentence, and only if it helps.
- If the arguments include `--from-pre-push`, print the report as plain text with no code fence and nothing else.
- If the command fails, show its last lines and stop. Don't run any other command, don't edit files, and don't review the code yourself.
