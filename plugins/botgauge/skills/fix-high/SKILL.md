---
name: fix-high
description: Fix every open high priority finding from /botgauge:review, then recheck only what changed.
disable-model-invocation: true
allowed-tools:
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" *)
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/bg.py *)
  - Read
  - Grep
  - Glob
---

# botgauge fix: high priority

- SELECTION: level `high`
- Helper (BG): `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py"`

Read `${CLAUDE_PLUGIN_ROOT}/references/fix-procedure.md` and follow it with these values.
