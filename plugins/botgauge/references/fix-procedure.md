# How a botgauge fix runs

The command that sent you here gave you **SELECTION** (a level: `blockers`, `high`, `medium` or `low`, or a list of finding IDs such as `B1 H2`) and the **helper** command. Below, `BG` means that helper command exactly as the command wrote it.

Code edits go through the user's normal Claude Code approval. Never commit or push. Run every `BG` command as a single line.

## Step 1: Load the findings

Run `BG list --level <level>`, or `BG list --ids <IDs>` for IDs. It prints JSON:
- `snapshot`: the state of the code before you change anything. Call it **BEFORE**.
- `findings`: the open findings to fix. Each has `id`, `area`, `level`, `file`, `line`, `title`, `problem`, `trigger`, `fix`, `other_locations`.
- `unknown_ids` and `not_open_ids`: IDs the user gave that don't exist or are already fixed or dismissed.
- `step_back`: areas where every recent round brought new merge blockers or high findings.

Then:
- If `findings` is empty, say there are no open findings of that kind, show `BG status` in a ```` ```text ```` block, and stop.
- Mention any `unknown_ids` or `not_open_ids` in one line.
- If `step_back` lists an area these findings belong to, patching keeps producing new problems there. Ask the user with AskUserQuestion whether to keep patching or to stop and rethink the approach. If they choose to rethink, stop and explain in a few sentences what the findings have in common.

Pre-existing findings (IDs starting with P) are fixed only when the user named them by ID.

## Step 2: Fix

Work through the findings: merge blockers first, then high, then medium, then low, file by file.

For each one:
1. Read the code around `file:line`, and enough around it to understand it. If the problem is already gone, move on without editing.
2. Make the smallest change that fixes the problem the way the finding's `fix` describes. Match the surrounding code's style. Fix every place listed in `other_locations` too, since the finding is about the whole class of bug.
3. By area:
   - **checks**: run `BG run-checks --only <check name>` (the name is in the finding's title, after "Check failed: "). Read the output and fix the code. Only change a test if the test itself is wrong, and say so when you do.
   - **security**: fix the vulnerability. For a leaked secret, remove it and read it from configuration or the environment instead. Then tell the user to rotate the secret, because git history still contains it.
   - **docs**: make the docstring or doc match what the code does, in the repo's docstring style. If the finding says the code is what's wrong, fix the code.
   - **api**: if fixing means changing public behavior (a deprecation path, a version bump, a changelog entry), say what you plan and ask before editing.
4. Don't touch unrelated code, don't reformat whole files, and don't fix findings that weren't selected.

Don't run test or lint commands directly; they need the user's permission each time. The recheck in step 3 runs the repo's configured checks.

## Step 3: Recheck and save

Run, as one single-line command with the Bash tool's maximum timeout (600000 ms):

```
BG recheck --from <BEFORE> --ids <the IDs you worked on, comma-separated>
```

It runs the configured checks, asks a verifier whether each finding is really resolved, reviews your edits for any new merge blocker or high-priority problem, saves, and prints the result.

Show the report exactly as printed, inside a ```` ```text ```` block. Then add one line naming the files you changed, plus the rotate-the-secret reminder if it applies.

If the report shows a check that fails because of your edits, fix it and run the recheck once more. Don't commit.
