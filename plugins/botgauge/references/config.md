# `.botgauge/config.json`

`/botgauge:setup` writes this file, and the team commits it so everyone shares the same settings. Every key is optional; anything left out uses the default below.

| Key | Default | What it does |
|---|---|---|
| `pre_push.mode` | `"block"` | `block` stops a push while a merge blocker is open or the pushed changes weren't checked for merge blockers. `warn` prints the same message and pushes anyway. `off` skips the check. |
| `pre_push.auto_review` | `true` | When the pushed commits weren't checked yet, run `/botgauge:review-blockers` through Claude Code before the push. If `false`, the push stops and asks for it instead. |
| `pre_push.timeout_minutes` | `15` | How long the automatic review may take before the push is stopped. |
| `checks` | `[]` | Commands that must pass, run from the repo root by `/botgauge:review` (and after fixes). Each is `{"name", "run", "cwd"?, "timeout"? (seconds, default 900), "on_fix"? (default true)}`. A failing check is a merge blocker. The commands run through the shell, so `cd dir && …` works. |
| `on_edit` | `{}` | Commands that run on a single file each time Claude edits it, keyed by language (`python`, `typescript`, `javascript`, `go`, …) or by extension (`.py`). `{file}` is replaced by the file's path. If a command exits non-zero, its output goes back to Claude to fix. Missing tools are skipped silently. |
| `published` | `false` | `true` for repos that publish a package. Turns on the public API review (breaking changes, deprecations, version bumps). |
| `docstring_style` | `{"python": "google", "typescript": "tsdoc", "javascript": "jsdoc"}` | The style the docs reviewer expects for new or changed public API. |
| `paths.skip` | `[]` | Globs never reviewed, on top of the built-in skips (lockfiles, `node_modules`, `dist`, `build`, generated code, `.botgauge/`). |
| `paths.frontend` | `[]` | Globs for frontend code that the defaults don't recognize. By default `.tsx`, `.jsx`, `.vue` and `.svelte` files count as frontend, plus `.ts` and `.js` files under `frontend/`, `web/`, `ui/`, `client/`, `components/`, `pages/` and `hooks/`. |
| `paths.backend` | `[]` | Globs that are backend even if they look like frontend. Backend source gets the performance review. |
| `paths.sensitive` | `[]` | Globs for code that's always reviewed in depth, on top of names matching auth, token, secret, crypto, billing, payment, privacy, mask, admin or migration, and CI files. |
| `paths.public_api` | `[]` | Globs for the public API. By default, any source file outside private folders (`_x/`, `internal/`, `tests/`, `scripts/`, `examples/`) and not starting with `_` counts as public. |
| `low_cap` | `5` | The most low findings kept per branch. |

Glob rules: a pattern without `/` matches file names anywhere (`*.lock`); a pattern with `/` matches from the repo root (`src/auth/**`); `**/` at the start matches at any depth.
