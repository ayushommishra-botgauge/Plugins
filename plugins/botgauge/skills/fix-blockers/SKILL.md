---
name: fix-blockers
description: Fix every open merge blockers finding from /botgauge:review, then recheck only what changed.
disable-model-invocation: true
allowed-tools:
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" *)
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/bg.py *)
  - Read
  - Grep
  - Glob
---

# botgauge fix: merge blockers

- SELECTION: level `blockers`
- Helper (BG): `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py"`

Read `${CLAUDE_PLUGIN_ROOT}/references/fix-procedure.md` and follow it with these values.
