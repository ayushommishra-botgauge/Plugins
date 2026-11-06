# botgauge: code review before you push

botgauge reviews your unpushed changes in Claude Code before they reach GitHub, in any repo and any language. It finds bugs, security problems, breaking API changes, docstrings that don't match the code, missing tests, code-quality issues, backend performance problems and wasteful frontend API calls.

Every finding gets a level and an ID. Each level has its own review command and its own fix command, and a git pre-push check stops a push while a merge blocker is open.

## Set up a repo (once per repo, then once per clone)

```
/botgauge:setup
```

Setup detects the stack and writes three files: `.botgauge/config.json` (checks, formatter and team settings), `REVIEW.md` (this repo's review rules) and a short Checks section in `CLAUDE.md`. Then it switches on the pre-push check. Commit those files. After pulling, each teammate runs `/botgauge:setup` once in their own clone to switch the pre-push check on; Claude reminds them if it's off.

## Commands

| Level | Find | Show | Fix |
|---|---|---|---|
| 🔴 Merge blocker | `/botgauge:review-blockers` | `/botgauge:status blockers` | `/botgauge:fix-blockers` |
| 🟠 High priority | `/botgauge:review-high` | `/botgauge:status high` | `/botgauge:fix-high` |
| 🟡 Medium | `/botgauge:review-medium` | `/botgauge:status medium` | `/botgauge:fix-medium` |
| ⚪ Low | `/botgauge:review-low` | `/botgauge:status low` | `/botgauge:fix-low` |
| All levels | `/botgauge:review` | `/botgauge:status` | `/botgauge:fix B1 H2` |

The other commands:
- `/botgauge:dismiss H2 intentional "retries are idempotent here"` closes a finding without fixing it. Reason types: `wrong`, `intentional`, `not-worth-it`. A dismissed finding isn't raised again.
- `/botgauge:setup [--update]` sets up the repo or this clone, or refreshes the setup. `/botgauge:setup --remove` switches the pre-push check off in this clone; do that before uninstalling the plugin, or the check keeps stopping pushes it can no longer review.

Options for every review command:
- `[areas…]` limits the review to some areas: `checks`, `bugs`, `security`, `api`, `docs`, `tests`, `quality`, `performance`, `frontend-api`.
- `--quick` runs one cheaper pass. `--deep` puts the strongest model on every area.
- `--full` re-reviews everything. By default, only what changed since the last review is checked, and findings are never repeated.
- `[path]` reviews only one folder.

A typical day:

```
/botgauge:review
/botgauge:fix-blockers
/botgauge:fix-high
/botgauge:dismiss M2 not-worth-it "one-off script"
git push
```

## Levels

| Level | Examples | Before push |
|---|---|---|
| 🔴 **Merge blocker** | Failing tests, lint or type checks; a bug that breaks behavior; any confirmed security issue; a leaked secret or personal data; a breaking public API change with no deprecation or version plan; requests fired in an endless loop | The push is stopped |
| 🟠 **High priority** | A likely edge-case bug; an error hidden from the caller; a public docstring that's wrong; a bug fix with no test; N+1 queries or blocking calls in async code; refetching on every render or duplicate requests | Fix before pushing |
| 🟡 **Medium** | Duplication, a reimplemented helper, needless complexity; a new public function with no docstring; no changelog entry for a change users will notice; weak tests; waterfall requests | Fix if cheap |
| ⚪ **Low** | Naming, wording, comments that restate the code (at most 5 shown) | Optional |
| 🟣 **Pre-existing** | A problem in code the change didn't touch | Never blocks |

## What happens on `git push`

The pre-push check (`.botgauge/pre-push`, committed to the repo) does two things:
- It stops the push while a merge blocker is open.
- If the pushed commits weren't checked for merge blockers yet, it runs `/botgauge:review-blockers` through Claude Code first. That usually takes a few minutes.

To skip it once: `git push --no-verify`. To change the behavior: `"pre_push": {"mode": "block" | "warn" | "off", "auto_review": true}` in `.botgauge/config.json`.

## How a review works

The helper script (`scripts/bg.py`) runs the whole review in code. Each reviewer and verifier is its own short, read-only Claude Code session; the instructions for each are in `reviewers/`. Every review prints what it cost.

1. **Plan** (no AI): the helper works out what changed since the push base, including uncommitted and untracked files. It skips lockfiles and generated code, picks the review areas that apply, and scans added lines for secrets.
2. **Checks**: the repo's own test, lint and type-check commands run. A failure is a merge blocker.
3. **Review**: one reviewer per area runs in parallel. Bugs and security use Opus; the other areas use Sonnet. Small changes get a single quick pass instead. Reviewers also see each changed file's history and its earlier fixes, so a change that brings back an old bug stands out.
4. **Verify**: findings that two reviewers both reported are merged first. Then every merge blocker must survive a vote: a majority of three independent verifiers must agree it's real. Two run first, and a third only when they disagree, because two matching votes already decide a majority of three. High findings get one verifier. Security findings go through Anthropic's false-positive filter, and a verifier keeps one only at 8/10 confidence or higher.
5. **Report**: findings are saved per branch in `.botgauge/local/`, which is never committed. Each level gets its own fix command.

This follows how Anthropic's Claude Code team reviews code: checks first, parallel reviewers, verification before posting, severity tags, a summary that starts with the count, no repeated findings, a step-back warning after three rounds, and review rules kept in `REVIEW.md`. The security method adapts Anthropic's open-source `/security-review` (MIT); see `THIRD_PARTY_NOTICES.md`.

## Hooks

- **After Claude edits a file**: the repo's formatter and linter run on that file (the `on_edit` setting). Their problems go back to Claude to fix.
- **When a session starts**: if the repo uses botgauge but the pre-push check is off in this clone, Claude reminds you to run `/botgauge:setup`.

Both hooks do nothing in repos without `.botgauge/config.json`.

## Requirements

- A recent Claude Code (tested with 2.1.318), in the terminal or the VS Code extension. Reviewers run as separate Claude Code processes. Without a `claude` command, botgauge uses the copy bundled with the VS Code extension (or its Cursor and Windsurf builds).
- git
- Python 3.8 or later as `python3`. It's used by the helper script and the pre-push check, which need nothing beyond the standard library.

## Settings

See `references/config.md` for every key in `.botgauge/config.json`.
