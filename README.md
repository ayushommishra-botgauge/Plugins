# BotGauge Claude Code plugins

This repo is a Claude Code plugin marketplace for the BotGauge team. It holds one plugin so far:

- **botgauge**: code review before `git push`, for any repo. Findings are merge blockers, high, medium or low; each level has its own review and fix command; and a pre-push check stops pushes while a merge blocker is open. See [plugins/botgauge/README.md](plugins/botgauge/README.md).

**New to botgauge?** Start with [USER-GUIDE.md](USER-GUIDE.md). It covers installing botgauge, setting up a project and everyday use, in plain language.

## Install

In a terminal:

```
claude plugin marketplace add ayushommishra-botgauge/Plugins
claude plugin install botgauge@botgauge-plugins
```

To update later:

```
claude plugin marketplace update botgauge-plugins
claude plugin update botgauge@botgauge-plugins
```

**From the claude.ai organization library**, once an org Owner has added it: nothing to do if it's set to *Installed by default* or *Required*.

**In a repo, so teammates are told to install it**: add this to the repo's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "botgauge-plugins": {"source": {"source": "github", "repo": "ayushommishra-botgauge/Plugins"}}
  },
  "enabledPlugins": {"botgauge@botgauge-plugins": true}
}
```

Then in each repo: `/botgauge:setup`.

## Working on the plugin

```
plugins/botgauge/
  .claude-plugin/plugin.json      manifest
  skills/<command>/SKILL.md       the /botgauge:* commands
  reviewers/*.md                  instructions, model and effort for each reviewer and verifier
  references/                     the review and fix procedures, config reference
  scripts/bg.py                   helper: plans and runs reviews (each reviewer is a headless Claude call),
                                  votes, saves findings, prints reports, checks pushes, detects the stack, hooks
  templates/                      files /botgauge:setup writes into a repo (pre-push check, REVIEW.md, …)
  hooks/hooks.json                format and lint after edits; reminder at session start
evals/                            cases with known bugs, and the runner that scores reviews against them
tests/                            unit tests for the helper and the pre-push check
```

To measure a change to the prompts or models, run the evals in [evals/](evals/README.md). They spend Claude usage. Keep a change only if catch rate and false alarms hold or improve:

```
python3 evals/run.py
python3 -m unittest discover -s tests      # helper script and pre-push check; no Claude usage
```

To try a change locally without installing it:

```
claude --plugin-dir ./plugins/botgauge
claude plugin validate --strict ./plugins/botgauge
claude plugin validate --strict .
```

`--plugin-dir` applies to that one session, so the pre-push check's automatic review (a separate `claude -p`) can't use it. To use the push check before this repo is on GitHub, install from a local copy:

```
claude plugin marketplace add /path/to/this/repo
claude plugin install botgauge@botgauge-plugins
```

A plugin installed from a folder is read from that folder, so edits take effect at the next session start or `/reload-plugins`.

Bump `version` in both `plugins/botgauge/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` for each release, so `claude plugin update` picks it up for installs from GitHub.
