#!/usr/bin/env python3
"""botgauge pre-push check.

/botgauge:setup commits this file to the repo so the whole team runs the same check, and
installs a git hook (.git/hooks/pre-push, or a line in .husky/pre-push) that calls it.

Before every push it:
  * stops the push while a merge blocker found by /botgauge:review is still open, and
  * when the commits being pushed weren't checked for merge blockers yet, runs
    /botgauge:review-blockers through Claude Code first (or asks for it, if auto_review is off).

Skip once:  git push --no-verify      or      BOTGAUGE_SKIP=1 git push
Settings:   .botgauge/config.json -> "pre_push": {"mode": "block" | "warn" | "off",
                                                  "auto_review": true, "timeout_minutes": 15}
Standard library only; Python 3.8+.
"""

BOTGAUGE_GATE_VERSION = 1

import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
SKIP_GLOBS = [
    ".botgauge/**", "node_modules/**", "**/node_modules/**", "vendor/**", "**/vendor/**",
    "dist/**", "**/dist/**", "build/**", "**/build/**", "*.min.js", "*.min.css", "*.map", "*.lock",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock", "Cargo.lock",
    "go.sum", "*.snap", "**/__snapshots__/**", "**/generated/**", "*_pb2.py", "*_pb2_grpc.py",
    "*.pb.go", "*.generated.*",
]


def git(args, cwd, check=True):
    proc = subprocess.run(["git"] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)
    if check and proc.returncode != 0:
        raise RuntimeError("git {} failed: {}".format(" ".join(args), proc.stderr.strip()))
    return proc


def out(args, cwd):
    proc = git(args, cwd, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def safe_name(branch):
    """Same as safe_name() in the plugin's scripts/bg.py."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", branch).strip("._") or "branch"
    return slug[:80] + "-" + hashlib.sha1(branch.encode("utf-8")).hexdigest()[:8]


def match_glob(path, pattern):
    pattern = pattern.strip()
    if not pattern:
        return False
    if pattern.endswith("/"):
        pattern += "**"
    if pattern.startswith("/"):
        pattern = pattern[1:]
    if "/" not in pattern:
        return fnmatch.fnmatchcase(os.path.basename(path), pattern)
    if fnmatch.fnmatchcase(path, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def default_remote_branch(root):
    ref = out(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], root)
    if ref:
        return ref.replace("refs/remotes/", "", 1)
    for cand in ("origin/main", "origin/master"):
        if out(["rev-parse", "--verify", "--quiet", cand + "^{commit}"], root):
            return cand
    return None


def changed_paths(root, a, b, skip):
    proc = git(["diff", "--name-status", "-z", "--no-renames", a, b], root)
    tokens = proc.stdout.split("\0")
    paths = set()
    i = 0
    while i + 1 < len(tokens) and tokens[i]:
        status, path = tokens[i], tokens[i + 1]
        i += 2
        if status.startswith("D"):
            continue
        if any(match_glob(path, g) for g in skip):
            continue
        paths.add(path)
    return paths


def is_zero(sha):
    return not sha or set(sha) == {"0"}


def evaluate(root, cfg, branch, local_sha, remote_sha):
    """Return (open merge blockers, files not yet checked for merge blockers, base used)."""
    state = load_json(os.path.join(root, ".botgauge", "local", "state", safe_name(branch) + ".json")) or {}
    blockers = [f for f in state.get("findings", [])
                if f.get("status") == "open" and f.get("level") == "blocker" and not f.get("pre_existing")]
    tip_tree = out(["rev-parse", local_sha + "^{tree}"], root)
    base = None
    if not is_zero(remote_sha) and out(["rev-parse", "--verify", "--quiet", remote_sha + "^{commit}"], root):
        base = remote_sha
    else:
        remote = default_remote_branch(root)
        if remote:
            base = out(["merge-base", local_sha, remote], root)
    base_tree = out(["rev-parse", base + "^{tree}"], root) if base else EMPTY_TREE
    skip = SKIP_GLOBS + list((cfg.get("paths") or {}).get("skip") or [])
    change = changed_paths(root, base_tree, tip_tree, skip)
    if not change:
        return blockers, [], base
    reviewed_tree = (state.get("blocker_review") or {}).get("tree")
    if reviewed_tree and out(["cat-file", "-t", reviewed_tree], root) == "tree":
        unreviewed = change & changed_paths(root, reviewed_tree, tip_tree, skip)
    else:
        unreviewed = change
    return blockers, sorted(unreviewed), base


def is_claude_binary(path):
    return bool(path) and os.path.basename(path) in ("claude", "claude.exe") and os.path.isfile(path) \
        and os.access(path, os.X_OK)


def editor_claudes(home):
    """Copies of Claude Code bundled with the VS Code extension (or its Cursor and Windsurf builds), newest first."""
    found = []
    for editor in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        for path in glob.glob(os.path.join(home, editor, "extensions", "anthropic.claude-code-*",
                                           "resources", "native-binary", "claude")):
            m = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", path)
            found.append((tuple(int(n) for n in m.groups()) if m else (0, 0, 0), path))
    return [path for _, path in sorted(found, reverse=True)]


def find_claude():
    """Claude Code, to run the review.

    When Claude Code itself runs `git push`, CLAUDE_CODE_EXECPATH names its binary. Otherwise this tries PATH, then
    the usual install folders: git hooks started from an editor may not see the shell's PATH. Last come the
    editor-bundled copies, for people who use only the VS Code extension and have no `claude` command.
    """
    running = os.environ.get("CLAUDE_CODE_EXECPATH")
    if is_claude_binary(running):
        return running
    found = shutil.which("claude")
    if found:
        return found
    home = os.path.expanduser("~")
    for cand in [os.path.join(home, ".claude", "local", "claude"), os.path.join(home, ".local", "bin", "claude"),
                 "/opt/homebrew/bin/claude", "/usr/local/bin/claude"] + editor_claudes(home):
        if is_claude_binary(cand):
            return cand
    return None


def auto_review(root, cfg, branch, sha, base):
    claude = find_claude()
    if not claude:
        return "Claude Code wasn't found (no `claude` command, and no VS Code extension copy)"
    current = out(["symbolic-ref", "--quiet", "--short", "HEAD"], root)
    if current != branch:
        return "the branch being pushed ({}) isn't the one checked out".format(branch)
    minutes = int((cfg.get("pre_push") or {}).get("timeout_minutes", 15))
    prompt = "/botgauge:review-blockers --commit {} --branch {}".format(sha, branch)
    if base:
        prompt += " --base {}".format(base)
    prompt += " --from-pre-push"
    sys.stderr.write("botgauge: checking the pushed commits for merge blockers first "
                     "(usually a few minutes; Ctrl+C cancels, git push --no-verify skips).\n")
    sys.stderr.flush()
    try:
        proc = subprocess.run([claude, "-p", prompt, "--permission-mode", "dontAsk", "--output-format", "text"],
                              cwd=root, stdin=subprocess.DEVNULL, timeout=minutes * 60)
    except subprocess.TimeoutExpired:
        return "the review took longer than {} minutes".format(minutes)
    except KeyboardInterrupt:
        return "the review was cancelled"
    except OSError as exc:
        return "couldn't start Claude Code: {}".format(exc)
    if proc.returncode != 0:
        return "Claude Code exited with code {}".format(proc.returncode)
    return None


def describe_blockers(blockers):
    lines = []
    for f in blockers[:10]:
        loc = f.get("file") or ""
        if f.get("line"):
            loc += ":{}".format(f["line"])
        lines.append("  {:<4} {}  {}".format(f.get("id", "?"), loc, f.get("title", "")))
    if len(blockers) > 10:
        lines.append("  … and {} more".format(len(blockers) - 10))
    return lines


def main():
    if os.environ.get("BOTGAUGE_SKIP") == "1":
        return 0
    root = out(["rev-parse", "--show-toplevel"], os.getcwd())
    if not root:
        return 0
    cfg = load_json(os.path.join(root, ".botgauge", "config.json")) or {}
    settings = cfg.get("pre_push") or {}
    mode = settings.get("mode", "block")
    if mode == "off":
        return 0
    auto = settings.get("auto_review", True)

    problems = []
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, _remote_ref, remote_sha = parts
        if is_zero(local_sha) or not local_ref.startswith("refs/heads/") or local_sha == remote_sha:
            continue
        branch = local_ref[len("refs/heads/"):]
        try:
            blockers, unreviewed, base = evaluate(root, cfg, branch, local_sha, remote_sha)
            if unreviewed and not blockers and auto:
                failure = auto_review(root, cfg, branch, local_sha, base)
                blockers, unreviewed, base = evaluate(root, cfg, branch, local_sha, remote_sha)
                if failure and unreviewed:
                    problems.append(("unreviewed", branch, unreviewed, "Couldn't run the check automatically: "
                                     + failure + "."))
                    continue
        except RuntimeError as exc:
            sys.stderr.write("botgauge: skipped the pre-push check ({}).\n".format(exc))
            continue
        if blockers:
            problems.append(("blockers", branch, blockers, None))
        elif unreviewed:
            problems.append(("unreviewed", branch, unreviewed, None))

    if not problems:
        return 0
    stop = mode == "block"
    lines = []
    for kind, branch, items, note in problems:
        if kind == "blockers":
            lines.append("botgauge: {} — {} merge blocker{} open on {}:".format(
                "push stopped" if stop else "warning", len(items), "" if len(items) == 1 else "s", branch))
            lines += describe_blockers(items)
            lines.append("Fix them with /botgauge:fix-blockers in Claude Code, or if one isn't a real problem:")
            lines.append('  /botgauge:dismiss <ID> wrong|intentional|not-worth-it "why"')
        else:
            shown = ", ".join(items[:6]) + (" …" if len(items) > 6 else "")
            lines.append("botgauge: {} — {} changed file{} on {} not checked for merge blockers yet: {}".format(
                "push stopped" if stop else "warning", len(items), "" if len(items) == 1 else "s", branch, shown))
            if note:
                lines.append(note)
            lines.append("Run /botgauge:review-blockers in Claude Code, then push again.")
    if stop:
        lines.append("Skip this check once: git push --no-verify")
    sys.stderr.write("\n".join(lines) + "\n")
    return 1 if stop else 0


if __name__ == "__main__":
    sys.exit(main())
