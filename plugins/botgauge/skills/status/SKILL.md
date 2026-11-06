---
name: status
description: Show open botgauge findings (merge blockers, high, medium, low, pre-existing) and whether git push is allowed right now. Use when the user asks what's left to fix or whether they can push.
argument-hint: "[blockers|high|medium|low|pre-existing] [--detail]"
model: haiku
allowed-tools:
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" *)
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/bg.py *)
  - Read
  - Grep
  - Glob
---

# botgauge status

Run exactly this command, as one Bash tool call, and nothing before it:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" status $ARGUMENTS
```

Show its output exactly as printed, inside a ```text block. Add nothing unless the user asked a question. If it fails because of an unknown argument, say that the level must be one of blockers, high, medium, low or pre-existing.
