---
name: dismiss
description: Close a botgauge finding without fixing it, with a reason (wrong, intentional or not-worth-it).
argument-hint: "<ID> wrong|intentional|not-worth-it \"why\""
disable-model-invocation: true
allowed-tools:
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" *)
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/bg.py *)
  - Read
  - Grep
  - Glob
---

# botgauge dismiss

Arguments: `$ARGUMENTS`

1. Read the arguments: the first is the finding ID (such as H2), the second is the reason type (`wrong`, `intentional` or `not-worth-it`), and the rest is the reason in the user's words.
2. If the ID, the type or the reason is missing, ask for it with AskUserQuestion. For the type, offer: wrong (not a real problem), intentional (the behavior is wanted), not-worth-it (real but not worth fixing). Never invent a reason.
3. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" dismiss <ID> <type> "<reason>"`.
4. Show its output exactly as printed, inside a ```text block.
