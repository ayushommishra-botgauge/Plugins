---
name: setup
description: Set up botgauge in this repo (detect the stack, write .botgauge/config.json and REVIEW.md, add the checks to CLAUDE.md) and switch on the git pre-push check in this clone. Run once per repo; teammates run it once per clone. --remove switches the pre-push check off in this clone.
argument-hint: "[--update|--remove]"
disable-model-invocation: true
allowed-tools:
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py" *)
  - Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/bg.py *)
  - Read
  - Grep
  - Glob
---

# botgauge setup

- Arguments: `$ARGUMENTS`
- Helper (BG): `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bg.py"`
- Templates: `${CLAUDE_PLUGIN_ROOT}/templates/` (`REVIEW.md`, `claude-md-checks.md`, `config.example.json`)
- Config reference: `${CLAUDE_PLUGIN_ROOT}/references/config.md`

File edits go through the user's normal approval. Never commit or push.

## With `--remove`

Run `BG install-hook --remove` to switch the pre-push check off in this clone, and say what was removed. If its output lists files under `commit` (a Husky setup), tell the user that committing that change switches the check off for teammates too. Then stop.

## If `.botgauge/config.json` already exists

A teammate has already set this repo up.
- **Without `--update`**: run `BG install-hook` to switch the pre-push check on in this clone. Say where it went, and stop. If its output lists files under `commit`, tell the user to commit them.
- **With `--update`**: run `BG install-hook --update` to refresh `.botgauge/pre-push` from the plugin. Then go through steps 1–4 to refresh the config. Show the user what would change before writing anything.

## 1. Detect

Run `BG detect`. It prints JSON describing the repo: languages, docstring styles in use, Python and Node projects with their tools and scripts, CI workflow `run:` lines, guidance files, sensitive paths, existing git hooks, and which tools are on PATH.

Then read the files under `guidance_files` (CONTRIBUTING.md, CLAUDE.md, README.md), plus any Makefile, justfile, tox.ini or noxfile.py. Learn the exact commands this repo uses to test, lint, format and type-check. CI's run lines and CONTRIBUTING are the source of truth.

## 2. Propose the config

Draft `.botgauge/config.json`, following `config.example.json` and `references/config.md`:

- **checks**: the commands that test, lint and type-check, written to run from the repo root. In a multi-package repo, use `cd <dir> && …`. Prefer what CI and CONTRIBUTING run. Only use tools the repo already uses and that are available locally (`tools_on_path`). Give each check a short `name`, a `timeout` in seconds, and set `"on_fix": false` for slow suites (over about 5 minutes).
- **on_edit**: per language, the formatter and linter for a single file, using `{file}`. For example, Python: `["ruff format {file}", "ruff check --fix {file}"]`; TypeScript: `["npx --no-install prettier --write {file}", "npx --no-install eslint --fix {file}"]`; Go: `["gofmt -w {file}"]`. Only list tools the repo already uses; never introduce new ones.
- **published**: `true` when the repo publishes a package (`published_hint`). This turns on the public API review.
- **docstring_style**: the style most files already use (`python_docstrings.styles`, `js_doc_comments`). The defaults are `google` for Python, `tsdoc` for TypeScript, `jsdoc` for JavaScript and `godoc` for Go.
- **paths.frontend** and **paths.backend**: set these only where the defaults would guess wrong. For example, a TypeScript backend under `src/` needs `paths.backend`, and a frontend app outside the usual folders needs `paths.frontend`.
- **paths.sensitive**: folders and files that handle auth, billing, privacy or masking, secrets, CI workflows or migrations (`sensitive_dirs`, `sensitive_files`, plus what you saw while reading).
- **paths.skip**: generated or vendored paths the defaults don't already cover.
- **pre_push**: `{"mode": "block", "auto_review": true, "timeout_minutes": 15}`.

## 3. Confirm with the user

Show a short summary: the checks, the on-edit commands, published or not, docstring style, sensitive paths, and the pre-push mode. Then ask with AskUserQuestion:
- **Write this setup?** Options: "Write it" or "Change something".
- **Pre-push check**: Block (recommended: stops the push while a merge blocker is open), Warn (shows the problems and pushes anyway), or Off.

Make any changes they ask for, then go on.

## 4. Write the files

1. `.botgauge/config.json`.
2. `REVIEW.md` at the repo root, from `templates/REVIEW.md`. Fill in the placeholders from what you learned: what failure means in this repo, the sensitive paths, the paths to skip, and the docstring standard. Delete placeholder lines you can't fill. If REVIEW.md already exists, ask before adding a `## botgauge` section to it; never replace it.
3. `CLAUDE.md`: add or refresh a short `## Checks` section from `templates/claude-md-checks.md` that lists the check commands. If there's no CLAUDE.md, ask before creating one. Keep the section under 15 lines, because CLAUDE.md loads into every session.

## 5. Switch on the pre-push check

Run `BG install-hook`. Report where it went: a small `.git/hooks/pre-push` script, or a line in `.husky/pre-push`. If it wrapped an existing pre-push hook, say that both still run.

## 6. Try the checks

Ask whether to run the configured checks once now with `BG run-checks`, to confirm the commands work. If a command itself is wrong (not found, wrong folder), fix the config, not the code. Failing tests are fine to report; this isn't the moment to fix them.

## 7. Wrap up

Tell the user, briefly:
- **Files to commit**: `.botgauge/config.json`, `.botgauge/pre-push`, `.botgauge/.gitignore`, `REVIEW.md`, `CLAUDE.md`, plus `.husky/pre-push` if it changed. Nothing has been committed.
- **Teammates**: after pulling, each runs `/botgauge:setup` once in their clone to switch the pre-push check on. Claude reminds them when it's off.
- **Next step**: `/botgauge:review`.
