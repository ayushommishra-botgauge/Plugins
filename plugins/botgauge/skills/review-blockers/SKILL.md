---
name: review-blockers
description: Check this repo's unpushed changes for merge blockers only (failing checks, breaking bugs, security issues, leaked secrets, breaking API changes). The quick check to run before git push; the botgauge pre-push check runs it automatically.
argument-hint: "[areas…] [--quick|--deep] [--full] [path]"
model: haiku
allowed-tools:
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" *)
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/bg.py *)
  - Read
  - Grep
  - Glob
---

# botgauge review: merge blockers

Run exactly this command, as one Bash tool call with `timeout` 600000, and nothing before it:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" review --level blockers $ARGUMENTS
```

It does the whole review: the repo's checks, reviewers in parallel, a vote on every merge blocker, saving the findings, and the report. Lines starting with `botgauge:` are progress; the report follows them and ends with a `Cost:` line.

Then:
- Show the report exactly as printed, inside a ```text block. Add at most one sentence, and only if it helps.
- If the arguments include `--from-pre-push`, print the report as plain text with no code fence and nothing else.
- If the command fails, show its last lines and stop. Don't run any other command, don't edit files, and don't review the code yourself.
