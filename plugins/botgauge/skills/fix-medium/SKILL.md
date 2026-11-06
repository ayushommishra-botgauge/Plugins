---
name: fix-medium
description: Fix every open medium finding from /botgauge:review, then recheck only what changed.
disable-model-invocation: true
allowed-tools:
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" *)
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/bg.py *)
  - Read
  - Grep
  - Glob
---

# botgauge fix: medium

- SELECTION: level `medium`
- Helper (BG): `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py"`

Read `${CLAUDE_PLUGIN_ROOT}/references/fix-procedure.md` and follow it with these values.
