---
name: fix
description: Fix specific botgauge findings by ID, for example /botgauge:fix B1 H2.
argument-hint: "<ID> [ID…]"
disable-model-invocation: true
allowed-tools:
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" *)
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/bg.py *)
  - Read
  - Grep
  - Glob
---

# botgauge fix: by ID

- SELECTION: the finding IDs in `$ARGUMENTS` (letters and numbers such as B1, H2, M3, L1, P1)
- Helper (BG): `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py"`

If no IDs were given, run `BG status`, show it in a ```text block, and ask which findings to fix. Otherwise read `${CLAUDE_PLUGIN_ROOT}/references/fix-procedure.md` and follow it with these values, using `BG list --ids <IDs>` in step 1.
