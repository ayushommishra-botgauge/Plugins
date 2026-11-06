#!/usr/bin/env python3
"""botgauge helper for the /botgauge:* commands and the plugin's hooks.

Subcommands (each prints to stdout):
  scope            work out what to review: changed files, which areas apply, diffs
  run-checks       run the repo's configured checks (tests, lint, type checks)
  save             merge new findings into the branch's findings and print the report
  status           print open findings and whether a push is allowed
  list             print open findings as JSON, for the fix commands
  snapshot         print a tree hash of the working tree (committed + uncommitted + untracked)
  changed          print the files that differ between two trees, as JSON
  dismiss          close a finding with a reason
  detect           describe the repo's stack, for /botgauge:setup
  install-hook     install the git pre-push check in this clone
  hook-status      say whether the pre-push check is installed
  hook-post-edit   Claude Code PostToolUse hook: format and lint the file Claude edited
  hook-session-start   Claude Code SessionStart hook: remind when the pre-push check is off

Local state is never committed: it lives in .botgauge/local/ (kept out of git through
.git/info/exclude). Team settings live in .botgauge/config.json, which is committed.
Standard library only; Python 3.8+.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

STATE_VERSION = 1
GATE_VERSION = 1
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE_TEMPLATE = os.path.join(PLUGIN_ROOT, "templates", "pre-push.py")
SHIM_MARKER = "botgauge pre-push shim"

# Finding levels, most severe first.
LEVELS = ("blocker", "high", "medium", "low")
LEVEL_LETTER = {"blocker": "B", "high": "H", "medium": "M", "low": "L"}
PRE_LETTER = "P"
ICON = {"blocker": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪", "pre": "🟣"}
HEADING = {"blocker": "Merge blockers", "high": "High priority", "medium": "Medium", "low": "Low"}

# Review commands: which finding levels each one covers, and which areas it runs.
REVIEW_LEVELS = ("all", "blockers", "high", "medium", "low")
REVIEW_COVERS = {
    "all": list(LEVELS),
    "blockers": ["blocker"],
    "high": ["high"],
    "medium": ["medium"],
    "low": ["low"],
}
AREAS = ("checks", "bugs", "security", "api", "docs", "tests", "quality", "performance", "frontend-api")
LEVEL_AREAS = {
    "all": list(AREAS),
    "blockers": ["checks", "bugs", "security", "api", "frontend-api"],
    "high": ["bugs", "security", "api", "docs", "tests", "performance", "frontend-api"],
    "medium": ["docs", "tests", "quality", "performance", "frontend-api"],
    "low": ["docs", "quality"],
}
DISMISS_CATEGORIES = ("wrong", "intentional", "not-worth-it")

DEFAULT_CONFIG = {
    "version": 1,
    "pre_push": {"mode": "block", "auto_review": True, "timeout_minutes": 15},
    "checks": [],
    "on_edit": {},
    "published": False,
    "docstring_style": {"python": "google", "typescript": "tsdoc", "javascript": "jsdoc"},
    "paths": {"skip": [], "frontend": [], "backend": [], "sensitive": [], "public_api": []},
    "low_cap": 5,
    "review": {"parallel": 4, "reviewer_timeout": 420, "verifier_timeout": 240, "max_budget_per_call": 3.0},
}

CODE_EXT = {
    ".py": "python", ".pyi": "python", ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript",
    ".cts": "typescript", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".vue": "vue", ".svelte": "svelte", ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala", ".rb": "ruby",
    ".php": "php", ".cs": "csharp", ".swift": "swift", ".c": "c", ".h": "c", ".cc": "cpp",
    ".cpp": "cpp", ".hpp": "cpp", ".m": "objc", ".sql": "sql", ".sh": "shell", ".bash": "shell",
    ".zsh": "shell", ".ps1": "powershell", ".lua": "lua", ".dart": "dart", ".ex": "elixir",
    ".exs": "elixir",
}
DOC_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc"}
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".properties",
              ".xml", ".tf", ".hcl"}
DEPS_FILES = {"requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py", "setup.cfg",
              "Pipfile", "package.json", "go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle",
              "build.gradle.kts", "composer.json"}
FRONTEND_EXT = {".tsx", ".jsx", ".vue", ".svelte"}
DEFAULT_SKIP = [
    ".botgauge/**", "node_modules/**", "**/node_modules/**", "vendor/**", "**/vendor/**",
    "dist/**", "**/dist/**", "build/**", "**/build/**", "*.min.js", "*.min.css", "*.map", "*.lock",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock", "Cargo.lock",
    "go.sum", "*.snap", "**/__snapshots__/**", "**/generated/**", "*_pb2.py", "*_pb2_grpc.py",
    "*.pb.go", "*.generated.*",
]
CI_GLOBS = [".github/workflows/**", ".gitlab-ci.yml", ".circleci/**", "Jenkinsfile", "Dockerfile*",
            "**/Dockerfile*", "docker-compose*.yml", "docker-compose*.yaml", "helm/**", "**/helm/**",
            "k8s/**", "**/k8s/**", "terraform/**", "*.tf"]
FRONTEND_GLOBS = ["frontend/**", "**/frontend/**", "web/**", "**/web/**", "ui/**", "client/**",
                  "**/components/**", "**/pages/**", "**/hooks/**"]
TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|e2e|e2e-tests|testing)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$|(^|/)conftest\.py$")
SENSITIVE_RE = re.compile(
    r"(auth|login|logout|session|token|secret|credential|passw|crypt|permission|rbac|acl|oauth|jwt|sso"
    r"|billing|payment|invoice|checkout|privacy|pii|redact|mask|security|admin|migration)", re.I)
FIX_RE = re.compile(r"\b(fix|fixes|fixed|bug|bugs|revert|reverts|regress\w*|hotfix|crash\w*|leak\w*|incident)\b", re.I)
DEEP_RE = re.compile(r"(auth|billing|payment|secret|credential|passw|crypt|privacy|pii|migration)", re.I)
SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("Stripe live key", re.compile(r"[sr]k_live_[0-9a-zA-Z]{20,}")),
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("hard-coded credential", re.compile(
        r"""(?i)\b(api[_-]?key|secret|token|passw(or)?d|private[_-]?key)\b["']?\s*[:=]\s*["'][^"'\s]{16,}["']""")),
]
STOP_WORDS = {"the", "and", "for", "with", "that", "this", "when", "from", "into", "not", "are", "was",
              "but", "can", "its", "has", "have", "will", "may", "missing", "uses", "use", "using",
              "does", "should", "could", "would", "after", "before", "without", "which", "than", "then"}


class BgError(Exception):
    pass


# ----------------------------------------------------------------------------- utilities

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_git(args, cwd, env=None, check=True):
    try:
        proc = subprocess.run(["git"] + list(args), cwd=cwd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
    except FileNotFoundError:
        raise BgError("git is not installed or not on PATH")
    if check and proc.returncode != 0:
        raise BgError("git {} failed: {}".format(" ".join(args), proc.stderr.strip()))
    return proc


def git_out(args, cwd, env=None):
    return run_git(args, cwd, env=env).stdout.strip()


def git_ok(args, cwd):
    return run_git(args, cwd, check=False).returncode == 0


def find_root(start=None):
    start = start or os.getcwd()
    if not os.path.isdir(start):
        start = os.path.dirname(start)
    try:
        proc = run_git(["rev-parse", "--show-toplevel"], start, check=False)
    except BgError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def require_root():
    root = find_root()
    if not root:
        raise BgError("not inside a git repository")
    return root


def git_dir(root):
    return git_out(["rev-parse", "--absolute-git-dir"], root)


def has_head(root):
    return git_ok(["rev-parse", "--verify", "--quiet", "HEAD"], root)


def rev_parse(root, rev):
    proc = run_git(["rev-parse", "--verify", "--quiet", rev + "^{commit}"], root, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def tree_of(root, rev):
    if not rev:
        return EMPTY_TREE
    return git_out(["rev-parse", rev + "^{tree}"], root)


def merge_base(root, a, b):
    proc = run_git(["merge-base", a, b], root, check=False)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def branch_name(root):
    proc = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], root, check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    sha = run_git(["rev-parse", "--short", "HEAD"], root, check=False).stdout.strip()
    return "detached-" + (sha or "nohead")


def default_remote_branch(root):
    proc = run_git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], root, check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip().replace("refs/remotes/", "", 1)
    for cand in ("origin/main", "origin/master"):
        if rev_parse(root, cand):
            return cand
    return None


def safe_name(branch):
    """File name for a branch's state. Keep in sync with templates/pre-push.py."""
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


def any_glob(path, patterns):
    return any(match_glob(path, p) for p in patterns or [])


def tail(text, lines=60, chars=4000):
    text = (text or "").rstrip()
    out = "\n".join(text.splitlines()[-lines:])
    return out[-chars:]


def write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


# ----------------------------------------------------------------------------- config and state

def deep_merge(base, extra):
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def config_path(root):
    return os.path.join(root, ".botgauge", "config.json")


def load_config(root):
    data = read_json(config_path(root), {}) or {}
    return deep_merge(DEFAULT_CONFIG, data)


def ensure_local(root):
    """Create .botgauge/local/ and keep it out of git without touching committed files."""
    local = os.path.join(root, ".botgauge", "local")
    os.makedirs(local, exist_ok=True)
    exclude = git_out(["rev-parse", "--git-path", "info/exclude"], root)
    if not os.path.isabs(exclude):
        exclude = os.path.join(root, exclude)
    line = "/.botgauge/local/"
    try:
        with open(exclude, encoding="utf-8") as fh:
            existing = fh.read()
    except OSError:
        existing = ""
    if line not in existing.splitlines():
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        with open(exclude, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(line + "\n")
    return local


def state_path(root, branch):
    return os.path.join(root, ".botgauge", "local", "state", safe_name(branch) + ".json")


def new_state(branch):
    return {
        "version": STATE_VERSION, "branch": branch, "counters": {}, "findings": [], "coverage": {},
        "rounds": {}, "blocker_review": None, "last_review": None, "last_fix": None,
        "checks": None, "low_hidden": 0,
    }


def load_state(root, branch):
    data = read_json(state_path(root, branch))
    if isinstance(data, dict) and data.get("version") == STATE_VERSION:
        base = new_state(branch)
        base.update(data)
        return base
    return new_state(branch)


def save_state(root, st):
    ensure_local(root)
    write_json_atomic(state_path(root, st["branch"]), st)


def log_event(root, event):
    try:
        local = ensure_local(root)
        event = dict(event, at=now_iso())
        with open(os.path.join(local, "log.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ----------------------------------------------------------------------------- trees and diffs

def snapshot_tree(root):
    """Tree hash of the working tree as it is now, untracked files included, without
    touching the real index."""
    ensure_local(root)
    gdir = git_dir(root)
    fd, tmp_index = tempfile.mkstemp(prefix="botgauge-index-")
    os.close(fd)
    env = dict(os.environ, GIT_INDEX_FILE=tmp_index)
    try:
        real_index = os.path.join(gdir, "index")
        if os.path.exists(real_index):
            shutil.copyfile(real_index, tmp_index)
        else:
            os.remove(tmp_index)
            if has_head(root):
                run_git(["read-tree", "HEAD"], root, env=env)
        run_git(["add", "-A"], root, env=env)
        return git_out(["write-tree"], root, env=env)
    finally:
        for path in (tmp_index, tmp_index + ".lock"):
            try:
                os.remove(path)
            except OSError:
                pass


def changed_entries(root, a, b, paths=None):
    """Files that differ between two tree-ish objects, with line counts."""
    extra = ["--"] + list(paths) if paths else []
    raw = run_git(["diff", "--name-status", "-z", "-M", a, b] + extra, root).stdout
    tokens = raw.split("\0")
    entries = []
    i = 0
    while i < len(tokens) and tokens[i]:
        status = tokens[i]
        i += 1
        if status[0] in "RC":
            entries.append({"status": status[0], "old_path": tokens[i], "path": tokens[i + 1]})
            i += 2
        else:
            entries.append({"status": status[0], "path": tokens[i]})
            i += 1
    raw = run_git(["diff", "--numstat", "-z", "-M", a, b] + extra, root).stdout
    tokens = raw.split("\0")
    stats = {}
    j = 0
    while j < len(tokens) and tokens[j]:
        parts = tokens[j].split("\t")
        j += 1
        if len(parts) >= 3 and parts[2]:
            path = parts[2]
        else:
            path = tokens[j + 1] if j + 1 < len(tokens) else ""
            j += 2
        stats[path] = (parts[0], parts[1])
    for entry in entries:
        added, deleted = stats.get(entry["path"], ("0", "0"))
        entry["binary"] = added == "-"
        entry["added"] = 0 if added == "-" else int(added)
        entry["deleted"] = 0 if deleted == "-" else int(deleted)
    return entries


def write_diff(root, a, b, paths, dest):
    if not paths:
        return None
    proc = run_git(["diff", "--no-color", "--no-ext-diff", "-U5", "-M", a, b, "--"] + list(paths), root)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(proc.stdout)
    return dest


def classify(path, cfg):
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    paths = cfg.get("paths", {})
    info = {"language": CODE_EXT.get(ext)}
    if any_glob(path, DEFAULT_SKIP) or any_glob(path, paths.get("skip")):
        info["category"] = "skip"
    elif any_glob(path, CI_GLOBS):
        info["category"] = "ci"
    elif name in DEPS_FILES or name.startswith("requirements"):
        info["category"] = "deps"
    elif ext in CODE_EXT and TEST_RE.search(path):
        info["category"] = "test"
    elif ext in CODE_EXT:
        info["category"] = "source"
    elif ext in DOC_EXT or path.startswith("docs/"):
        info["category"] = "docs"
    elif ext in CONFIG_EXT or name.startswith(".env"):
        info["category"] = "config"
    else:
        info["category"] = "other"
    frontend_globs = paths.get("frontend") or []
    backend_globs = paths.get("backend") or []
    if info["category"] in ("source", "test"):
        if any_glob(path, backend_globs):
            info["frontend"] = False
        elif ext in FRONTEND_EXT or any_glob(path, frontend_globs):
            info["frontend"] = True
        elif ext in (".ts", ".js", ".mjs", ".cjs", ".mts") and any_glob(path, FRONTEND_GLOBS):
            info["frontend"] = True
        else:
            info["frontend"] = False
    else:
        info["frontend"] = False
    info["sensitive"] = bool(DEEP_RE.search(path)) or info["category"] == "ci" or \
        any_glob(path, paths.get("sensitive"))
    public_globs = paths.get("public_api") or []
    if public_globs:
        info["public"] = any_glob(path, public_globs)
    else:
        info["public"] = info["category"] == "source" and not is_private_path(path)
    return info


def is_private_path(path):
    parts = path.split("/")
    for part in parts[:-1]:
        if part in ("internal", "_internal", "private", "tests", "test", "scripts", "examples") or \
                (part.startswith("_") and not part.startswith("__")):
            return True
    base = parts[-1]
    if base.startswith("_") and base not in ("__init__.py", "__main__.py"):
        return True
    return False


def scan_secrets(diff_text):
    """Look for secret-looking strings on added lines of a unified diff."""
    hits = []
    current, line_no = None, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            continue
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            line_no = int(match.group(1)) - 1 if match else 0
            continue
        if current is None:
            continue
        if line.startswith("+"):
            line_no += 1
            for kind, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    hits.append({"file": current, "line": line_no, "kind": kind})
                    break
        elif not line.startswith("-"):
            line_no += 1
    return hits


# ----------------------------------------------------------------------------- scope

def resolve_base(root, tip, explicit=None, use_upstream=True):
    if explicit:
        commit = merge_base(root, tip, explicit) or rev_parse(root, explicit)
        return explicit, commit
    if use_upstream:
        proc = run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root, check=False)
        upstream = proc.stdout.strip() if proc.returncode == 0 else ""
        if upstream:
            commit = merge_base(root, tip, upstream)
            if commit:
                return upstream, commit
    remote = default_remote_branch(root)
    if remote:
        commit = merge_base(root, tip, remote)
        if commit:
            return remote, commit
    if has_head(root):
        return "HEAD", rev_parse(root, "HEAD")
    return None, None


def applicable_files(files):
    by_cat = {}
    for f in files:
        by_cat.setdefault(f["category"], []).append(f)
    src = by_cat.get("source", [])
    tests = by_cat.get("test", [])
    docs = by_cat.get("docs", [])
    infra = by_cat.get("ci", []) + by_cat.get("deps", []) + by_cat.get("config", [])
    names = lambda items: sorted({f["path"] for f in items})  # noqa: E731
    return {
        "bugs": names(src + by_cat.get("ci", [])),
        "security": names(src + infra),
        "api": names([f for f in src if f.get("public")]),
        "docs": names(src + docs),
        "tests": names(src + tests),
        "quality": names(src + tests),
        "performance": names([f for f in src if not f["frontend"]]),
        "frontend-api": names([f for f in src if f["frontend"]]),
    }


def plan_area(st, area, levels_needed, files, snapshot, base_tree, full, root):
    if not files:
        return {"mode": "skip", "reason": "no changed files this area looks at", "files": []}
    coverage = st.get("coverage", {}).get(area, {})
    trees = {coverage.get(level) for level in levels_needed}
    if full or None in trees or len(trees) != 1:
        return {"mode": "full", "files": files, "from": base_tree, "to": snapshot}
    reviewed = trees.pop()
    if reviewed == snapshot:
        return {"mode": "skip", "reason": "already reviewed at exactly this state", "files": []}
    try:
        since = {e["path"] for e in changed_entries(root, reviewed, snapshot) if e["status"] != "D"}
    except BgError:
        return {"mode": "full", "files": files, "from": base_tree, "to": snapshot}
    new_files = [p for p in files if p in since]
    if not new_files:
        return {"mode": "skip", "reason": "no changes since this area was last reviewed", "files": []}
    return {"mode": "incremental", "files": new_files, "from": reviewed, "to": snapshot}


def compute_scope(args):
    root = require_root()
    cfg = load_config(root)
    level = args.level
    branch = args.branch or branch_name(root)
    st = load_state(root, branch)
    if args.commit:
        tip = rev_parse(root, args.commit)
        if not tip:
            raise BgError("unknown commit: {}".format(args.commit))
        snapshot = tree_of(root, tip)
        base_ref, base_commit = resolve_base(root, tip, args.base, use_upstream=False)
        uncommitted = False
    else:
        snapshot = snapshot_tree(root)
        tip = "HEAD" if has_head(root) else None
        base_ref, base_commit = resolve_base(root, tip or "HEAD", args.base) if tip else (None, None)
        uncommitted = snapshot != (tree_of(root, "HEAD") if tip else EMPTY_TREE)
    base_tree = tree_of(root, base_commit) if base_commit else EMPTY_TREE

    entries = changed_entries(root, base_tree, snapshot)
    files, skipped = [], []
    for e in entries:
        if e["status"] == "D":
            continue
        if args.path and not any(e["path"].startswith(p.rstrip("/") + "/") or e["path"] == p or
                                 match_glob(e["path"], p) for p in args.path):
            continue
        info = classify(e["path"], cfg)
        if info["category"] == "skip" or e.get("binary"):
            skipped.append({"path": e["path"], "reason": "binary" if e.get("binary") else "generated, vendored or lockfile"})
            continue
        files.append(dict(e, **info))
    totals = {"files": len(files), "added": sum(f["added"] for f in files),
              "deleted": sum(f["deleted"] for f in files),
              "deleted_files": len([e for e in entries if e["status"] == "D"])}

    by_area = applicable_files(files)
    checks_configured = bool(cfg.get("checks"))
    requested = [a.strip() for a in (args.areas or "").replace(",", " ").split() if a.strip()]
    unknown = [a for a in requested if a not in AREAS]
    if unknown:
        raise BgError("unknown area(s): {}. Areas: {}".format(", ".join(unknown), ", ".join(AREAS)))
    candidate_areas = requested or LEVEL_AREAS[level]
    levels_needed = REVIEW_COVERS[level]

    run_dir = os.path.join(ensure_local(root), "run")
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir, ignore_errors=True)
    os.makedirs(run_dir, exist_ok=True)
    reviewable = [f["path"] for f in files]
    full_diff = write_diff(root, base_tree, snapshot, reviewable, os.path.join(run_dir, "full.diff"))

    areas, not_applicable = {}, {}
    for area in candidate_areas:
        if area == "checks":
            if not checks_configured:
                not_applicable[area] = "no checks configured in .botgauge/config.json (run /botgauge:setup)"
            elif not files and not requested:
                not_applicable[area] = "nothing changed"
            else:
                last = st.get("checks") or {}
                if not args.full and last.get("tree") == snapshot and last.get("all_passed"):
                    areas[area] = {"mode": "skip", "reason": "checks already passed at exactly this state"}
                else:
                    areas[area] = {"mode": "run"}
            continue
        files_for_area = by_area.get(area, [])
        if not files_for_area and not requested:
            not_applicable[area] = {
                "api": "no public, non-test source files changed" if cfg.get("published")
                else "repo is not marked as a published package",
                "frontend-api": "no frontend files changed",
                "performance": "no backend source files changed",
            }.get(area, "no files this area looks at changed")
            continue
        if area == "api" and not cfg.get("published") and not requested:
            not_applicable[area] = "repo is not marked as a published package (set \"published\": true)"
            continue
        if requested and not files_for_area:
            files_for_area = reviewable
        plan = plan_area(st, area, levels_needed, files_for_area, snapshot, base_tree, args.full, root)
        if plan["mode"] == "incremental":
            plan["diff_file"] = write_diff(root, plan["from"], plan["to"], plan["files"],
                                           os.path.join(run_dir, area + ".diff"))
        elif plan["mode"] == "full":
            plan["diff_file"] = full_diff
        areas[area] = plan

    secret_hits = []
    if full_diff and os.path.exists(full_diff):
        with open(full_diff, encoding="utf-8", errors="replace") as fh:
            secret_hits = scan_secrets(fh.read())

    reviewing = [a for a, p in areas.items() if p.get("mode") in ("full", "incremental", "run")]
    lines_changed = totals["added"] + totals["deleted"]
    sensitive = sorted({f["path"] for f in files if f.get("sensitive")})
    if args.depth:
        depth, depth_reason = args.depth, "chosen with --{}".format(args.depth)
    elif sensitive:
        depth, depth_reason = "deep", "touches sensitive paths: " + ", ".join(sensitive[:5])
    elif lines_changed >= 800:
        depth, depth_reason = "deep", "large change ({} lines)".format(lines_changed)
    elif lines_changed <= 40 and len(files) <= 3:
        depth, depth_reason = "quick", "small change ({} lines in {} files)".format(lines_changed, len(files))
    else:
        depth, depth_reason = "standard", "{} lines in {} files".format(lines_changed, len(files))

    commits = []
    if base_commit and (args.commit or tip):
        log = run_git(["log", "--no-decorate", "--format=%h %s", "-n", "50",
                       "{}..{}".format(base_commit, args.commit or "HEAD")], root, check=False).stdout
        commits = [line for line in log.splitlines() if line.strip()]

    history = file_history(root, base_commit, files)
    open_findings = [summary_of(f) for f in st["findings"] if f["status"] == "open"]
    dismissed = [summary_of(f, extra=("dismissed_as", "dismiss_reason"))
                 for f in st["findings"] if f["status"] == "dismissed"]
    step_back = sorted(a for a, n in (st.get("rounds") or {}).items() if n >= 3 and a in areas)

    result = {
        "repo": root, "branch": branch, "level": level, "covers_levels": levels_needed,
        "base_ref": base_ref, "base_commit": base_commit, "base_tree": base_tree,
        "snapshot": snapshot, "reviewing_commit": args.commit, "uncommitted_changes": uncommitted,
        "depth": depth, "depth_reason": depth_reason, "totals": totals,
        "files": [{k: f[k] for k in ("path", "status", "added", "deleted", "category", "language",
                                      "frontend", "sensitive", "public") if k in f} for f in files],
        "skipped_files": skipped[:50], "full_diff": full_diff, "commits": commits,
        "areas": areas, "not_applicable": not_applicable, "reviewing": reviewing,
        "secret_candidates": secret_hits[:50], "history": history,
        "open_findings": open_findings, "dismissed_findings": dismissed,
        "step_back": step_back,
        "config": {"published": bool(cfg.get("published")), "docstring_style": cfg.get("docstring_style"),
                   "review_rules": [p for p in ("REVIEW.md", "CLAUDE.md", "CONTRIBUTING.md")
                                    if os.path.exists(os.path.join(root, p))]},
        "nothing_to_review": not reviewing,
    }
    return result


def cmd_scope(args):
    print(json.dumps(compute_scope(args), indent=1, ensure_ascii=False))


def file_history(root, base_commit, files, limit=25):
    """Recent commits touching each changed file before this change, with earlier fixes
    picked out, so reviewers can check the change doesn't bring back a bug fixed there before."""
    history = {}
    if not base_commit:
        return history
    for f in files:
        if len(history) >= limit:
            break
        if f["category"] not in ("source", "test", "ci") or f.get("status") == "A":
            continue
        proc = run_git(["log", "-n", "12", "--no-merges", "--follow", "--format=%h %ad %s", "--date=short",
                        base_commit, "--", f.get("old_path") or f["path"]], root, check=False)
        entries = [line for line in proc.stdout.splitlines() if line.strip()]
        if not entries:
            continue
        history[f["path"]] = {"recent": entries[:5], "past_fixes": [e for e in entries if FIX_RE.search(e)][:5]}
    return history


def summary_of(f, extra=()):
    keys = ("id", "area", "level", "file", "line", "title") + tuple(extra)
    return {k: f.get(k) for k in keys if f.get(k) is not None}


# ----------------------------------------------------------------------------- checks

def run_checks_list(root, cfg, for_fix=False, only=None):
    checks = cfg.get("checks") or []
    if for_fix:
        checks = [c for c in checks if c.get("on_fix", True)]
    if only:
        wanted = set(only.split(","))
        checks = [c for c in checks if c.get("name") in wanted]
    results = []
    for check in checks:
        name = check.get("name") or check.get("run")
        command = check.get("run")
        if not command:
            continue
        cwd = os.path.normpath(os.path.join(root, check.get("cwd", ".")))
        timeout = int(check.get("timeout", 900))
        start = time.time()
        try:
            proc = subprocess.run(command, shell=True, cwd=cwd, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, universal_newlines=True, timeout=timeout)
            ok, code, output = proc.returncode == 0, proc.returncode, proc.stdout
        except subprocess.TimeoutExpired as exc:
            out = exc.output or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            ok, code, output = False, None, out + "\n[timed out after {}s]".format(timeout)
        results.append({"name": name, "run": command, "cwd": check.get("cwd", "."), "ok": ok,
                        "exit_code": code, "seconds": round(time.time() - start, 1),
                        "output_tail": "" if ok else tail(output)})
    return results


def cmd_run_checks(args):
    root = require_root()
    results = run_checks_list(root, load_config(root), args.for_fix, args.only)
    print(json.dumps({"checks": results, "all_passed": all(r["ok"] for r in results)}, indent=1))

# ----------------------------------------------------------------------------- findings

def tokens_of(text):
    return {w for w in re.findall(r"[a-z0-9_]+", (text or "").lower()) if len(w) > 2 and w not in STOP_WORDS}


def similarity(a, b):
    ta, tb = tokens_of(a), tokens_of(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def same_finding(new, old):
    """Whether a reported finding is one we already have. Strict on purpose: two different
    problems in one file often share most of their wording ("foo has no docstring" and
    "bar has no docstring"), so similar titles alone aren't enough unless nearly identical."""
    if new.get("same_as") and new["same_as"] == old.get("id"):
        return True
    if new.get("area") != old.get("area") or new.get("file") != old.get("file"):
        return False
    sim = similarity(new.get("title"), old.get("title"))
    if sim >= 0.8:
        return True
    try:
        close = abs(int(new.get("line")) - int(old.get("line"))) <= 3
    except (TypeError, ValueError):
        close = False
    return close and sim >= 0.3


def next_id(st, letter):
    counters = st.setdefault("counters", {})
    counters[letter] = counters.get(letter, 0) + 1
    return "{}{}".format(letter, counters[letter])


def normalize_finding(raw):
    level = str(raw.get("level", "medium")).lower().strip()
    level = {"merge-blocker": "blocker", "merge blocker": "blocker", "critical": "blocker",
             "important": "high", "nit": "low"}.get(level, level)
    if level not in LEVELS:
        level = "medium"
    finding = {k: raw.get(k) for k in ("area", "file", "line", "end_line", "title", "problem", "trigger",
                                       "fix", "evidence", "other_locations", "confidence", "category",
                                       "severity", "exploit_scenario", "same_as") if raw.get(k) not in (None, "", [])}
    finding["level"] = level
    finding["pre_existing"] = bool(raw.get("pre_existing"))
    finding.setdefault("area", "bugs")
    finding.setdefault("title", "(no title)")
    finding.setdefault("file", "")
    return finding


def apply_checks(st, checks, snapshot):
    """Turn failing checks into merge blockers and close the ones that pass now."""
    created, closed = [], []
    for result in checks:
        name = result.get("name") or result.get("run") or "check"
        key = "check:" + name
        existing = [f for f in st["findings"] if f.get("check") == key and f["status"] == "open"]
        if result.get("ok"):
            for f in existing:
                f["status"] = "fixed"
                f["fixed_at"] = now_iso()
                closed.append(f["id"])
            continue
        detail = {"problem": "`{}` failed{}.\n{}".format(
            result.get("run"), "" if result.get("exit_code") is None else " (exit {})".format(result["exit_code"]),
            tail(result.get("output_tail", ""), lines=25, chars=1500)),
                  "fix": "Run `{}` and fix what it reports.".format(result.get("run"))}
        if existing:
            existing[0].update(detail)
            continue
        finding = {"id": next_id(st, "B"), "status": "open", "area": "checks", "level": "blocker",
                   "pre_existing": False, "file": "(check) " + name, "title": "Check failed: " + name,
                   "check": key, "created_at": now_iso()}
        finding.update(detail)
        st["findings"].append(finding)
        created.append(finding["id"])
    all_passed = all(r.get("ok") for r in checks)
    st["checks"] = {"tree": snapshot, "all_passed": all_passed, "at": now_iso(),
                    "results": [{k: r.get(k) for k in ("name", "ok", "seconds")} for r in checks]}
    return created, closed


def merge_findings(st, raw_findings):
    new_ids, updated_ids, suppressed, reopened = [], [], [], []
    for raw in raw_findings:
        finding = normalize_finding(raw)
        match = None
        for old in st["findings"]:
            if old.get("check"):
                continue
            if same_finding(finding, old):
                match = old
                break
        if match is not None:
            if match["status"] == "dismissed":
                suppressed.append(match["id"])
                continue
            if match["status"] == "fixed":
                match["status"] = "open"
                match["reopened_at"] = now_iso()
                reopened.append(match["id"])
            for key in ("line", "end_line", "problem", "trigger", "fix", "evidence", "other_locations",
                        "confidence", "category", "exploit_scenario"):
                if finding.get(key) not in (None, "", []):
                    match[key] = finding[key]
            if LEVELS.index(finding["level"]) < LEVELS.index(match["level"]) and not match.get("pre_existing"):
                match["level"] = finding["level"]
            if match["id"] not in reopened:
                updated_ids.append(match["id"])
            continue
        finding.pop("same_as", None)
        letter = PRE_LETTER if finding["pre_existing"] else LEVEL_LETTER[finding["level"]]
        finding["id"] = next_id(st, letter)
        finding["status"] = "open"
        finding["created_at"] = now_iso()
        st["findings"].append(finding)
        new_ids.append(finding["id"])
    return new_ids, updated_ids, suppressed, reopened


def enforce_low_cap(st, cap):
    lows = [f for f in st["findings"] if f["status"] == "open" and f["level"] == "low" and not f["pre_existing"]]
    if len(lows) <= cap:
        return 0
    lows.sort(key=lambda f: (-(f.get("confidence") or 0), int(re.sub(r"\D", "", f["id"]) or 0)))
    dropped = lows[cap:]
    drop_ids = {f["id"] for f in dropped}
    st["findings"] = [f for f in st["findings"] if f["id"] not in drop_ids]
    st["low_hidden"] = st.get("low_hidden", 0) + len(dropped)
    return len(dropped)


def open_findings(st, levels=None, include_pre=True):
    out = []
    for f in st["findings"]:
        if f["status"] != "open":
            continue
        if f["pre_existing"] and not include_pre:
            continue
        if levels and f["level"] not in levels and not (f["pre_existing"] and include_pre):
            continue
        out.append(f)
    return sorted(out, key=finding_sort_key)


def finding_sort_key(f):
    group = 4 if f.get("pre_existing") else LEVELS.index(f["level"])
    num = int(re.sub(r"\D", "", f["id"]) or 0)
    return (group, num)


def counts(st):
    c = Counter()
    for f in st["findings"]:
        if f["status"] == "open":
            c["pre" if f["pre_existing"] else f["level"]] += 1
        else:
            c[f["status"]] += 1
    return c


# ----------------------------------------------------------------------------- push readiness

def push_readiness(root, st, tip_tree, base_commit):
    """Same rule as the pre-push check: blocked while a merge blocker is open; needs review
    when changed files differ from what the last merge-blocker review saw."""
    blockers = [f for f in open_findings(st, ["blocker"], include_pre=False)]
    if blockers:
        return {"verdict": "blocked", "blockers": [f["id"] for f in blockers], "unreviewed": []}
    base_tree = tree_of(root, base_commit) if base_commit else EMPTY_TREE
    change = {e["path"] for e in changed_entries(root, base_tree, tip_tree) if e["status"] != "D"}
    change = {p for p in change if classify(p, load_config(root))["category"] != "skip"}
    if not change:
        return {"verdict": "allowed", "blockers": [], "unreviewed": []}
    review = st.get("blocker_review") or {}
    if not review.get("tree"):
        return {"verdict": "needs-review", "blockers": [], "unreviewed": sorted(change)}
    differs = {e["path"] for e in changed_entries(root, review["tree"], tip_tree) if e["status"] != "D"}
    unreviewed = sorted(change & differs)
    if unreviewed:
        return {"verdict": "needs-review", "blockers": [], "unreviewed": unreviewed}
    return {"verdict": "allowed", "blockers": [], "unreviewed": []}


def readiness_line(ready):
    if ready["verdict"] == "blocked":
        ids = ", ".join(ready["blockers"])
        return "Push: blocked until {} {} fixed or dismissed".format(ids, "is" if len(ready["blockers"]) == 1 else "are")
    if ready["verdict"] == "needs-review":
        n = len(ready["unreviewed"])
        return "Push: {} changed file{} not yet checked for merge blockers (run /botgauge:review-blockers)".format(
            n, "" if n == 1 else "s")
    return "Push: allowed"


# ----------------------------------------------------------------------------- rendering

def wrap(label, text, indent=6):
    if not text:
        return []
    prefix = " " * indent + (label + ": " if label else "")
    body = " ".join(str(text).split()) if label != "Output" else str(text)
    if label == "Output":
        lines = [" " * indent + "Output:"]
        lines += [" " * (indent + 2) + line for line in body.splitlines()[-12:]]
        return lines
    return textwrap.wrap(body, width=100, initial_indent=prefix,
                         subsequent_indent=" " * (indent + (len(label) + 2 if label else 0)))


def render_finding(f, detail=True):
    loc = f.get("file") or ""
    if f.get("line"):
        loc += ":{}".format(f["line"])
    lines = ["  {:<4} {}  · {}".format(f["id"], loc, f.get("area", ""))]
    lines += wrap("", f.get("title"))
    if detail:
        if f.get("area") == "checks":
            problem = f.get("problem") or ""
            first, _, rest = problem.partition("\n")
            lines += wrap("Problem", first)
            if rest.strip():
                lines += wrap("Output", rest)
        else:
            lines += wrap("Problem", f.get("problem"))
        lines += wrap("When", f.get("trigger"))
        lines += wrap("Exploit", f.get("exploit_scenario"))
        lines += wrap("Fix", f.get("fix"))
        if f.get("other_locations"):
            lines += wrap("Also at", ", ".join(str(x) for x in f["other_locations"]))
    return lines


def count_phrase(c, keys=("blocker", "high", "medium", "low")):
    names = {"blocker": ("merge blocker", "merge blockers"), "high": ("high", "high"),
             "medium": ("medium", "medium"), "low": ("low", "low")}
    parts = ["{} {}".format(c[k], names[k][0] if c[k] == 1 else names[k][1]) for k in keys if c.get(k)]
    return ", ".join(parts)


def render_findings_block(findings, detail=True):
    lines = []
    groups = [(lvl, [f for f in findings if not f["pre_existing"] and f["level"] == lvl]) for lvl in LEVELS]
    groups.append(("pre", [f for f in findings if f["pre_existing"]]))
    for key, items in groups:
        if not items:
            continue
        title = HEADING.get(key, "Pre-existing (in code this change didn't touch; never blocks)")
        lines.append("")
        lines.append("{} {}".format(ICON[key], title))
        for f in items:
            lines += render_finding(f, detail)
    return lines


def render_report(root, st, level, mode, new_ids, suppressed, reopened, dropped, ready, scope_info):
    c_open = Counter()
    shown_levels = REVIEW_COVERS.get(level, list(LEVELS)) if mode == "review" else list(LEVELS)
    for f in open_findings(st):
        c_open["pre" if f["pre_existing"] else f["level"]] += 1
    shown = [f for f in open_findings(st)
             if f["pre_existing"] or f["level"] in shown_levels or f["level"] == "blocker"]
    if mode == "review" and level != "all":
        shown = [f for f in shown if not f["pre_existing"] or f["id"] in new_ids]
    to_fix = sum(c_open[k] for k in shown_levels) + (c_open["blocker"] if "blocker" not in shown_levels else 0)
    label = {"all": "", "blockers": " (merge blockers)", "high": " (high priority)",
             "medium": " (medium)", "low": " (low)"}.get(level, "")
    lines = []
    if mode == "fix":
        fixed = scope_info.get("fixed", [])
        still = scope_info.get("still_open", [])
        head = "botgauge fix: {} fixed".format(len(fixed))
        if still:
            head += ", {} still open ({})".format(len(still), ", ".join(still))
        if new_ids:
            head += ", {} new from the fix".format(len(new_ids))
        lines.append(head + ".")
        if fixed:
            lines.append("Fixed: " + ", ".join(fixed))
    elif to_fix == 0:
        lines.append("botgauge review{}: nothing to fix.".format(label))
    else:
        phrase = count_phrase(c_open, [k for k in LEVELS if k in shown_levels or k == "blocker"])
        lines.append("botgauge review{}: {} to fix — {}{}.".format(
            label, to_fix, phrase, " · {} pre-existing".format(c_open["pre"]) if c_open["pre"] else ""))
    if scope_info.get("checked"):
        lines.append(scope_info["checked"])
    lines.append(readiness_line(ready))
    if shown:
        lines += render_findings_block(shown)
    notes = []
    if suppressed:
        notes.append("{} finding{} you dismissed earlier {} not raised again.".format(
            len(suppressed), "" if len(suppressed) == 1 else "s", "was" if len(suppressed) == 1 else "were"))
    if reopened:
        notes.append("Came back after being marked fixed: {}.".format(", ".join(reopened)))
    if dropped:
        notes.append("{} more low finding{} not shown (low findings are capped).".format(
            dropped, "" if dropped == 1 else "s"))
    other = [k for k in LEVELS if k not in shown_levels and k != "blocker" and c_open.get(k)]
    if other and mode == "review":
        notes.append("Also open: {} (see /botgauge:status).".format(count_phrase(c_open, other)))
    step_back = sorted(a for a, n in (st.get("rounds") or {}).items() if n >= 3)
    if step_back:
        notes.append("⚠ {}: new merge blockers or high-priority findings {} rounds in a row. Step back and "
                     "rethink the approach before patching again.".format(", ".join(step_back), 3))
    if notes:
        lines.append("")
        lines += notes
    nxt = []
    if c_open["blocker"]:
        nxt.append("/botgauge:fix-blockers")
    if c_open["high"]:
        nxt.append("/botgauge:fix-high")
    if c_open["medium"] and ("medium" in shown_levels or mode == "fix"):
        nxt.append("/botgauge:fix-medium")
    if c_open["low"] and ("low" in shown_levels or mode == "fix"):
        nxt.append("/botgauge:fix-low")
    if nxt:
        lines.append("")
        lines.append("Next: {} · not a real problem? /botgauge:dismiss <ID> wrong|intentional|not-worth-it \"why\"".format(
            " · ".join(nxt)))
    return "\n".join(lines)


# ----------------------------------------------------------------------------- save

def describe_checked(root, cfg, branch, base_commit, snapshot, areas, depth, incremental):
    try:
        base_tree = tree_of(root, base_commit) if base_commit else EMPTY_TREE
        entries = [e for e in changed_entries(root, base_tree, snapshot)
                   if e["status"] != "D" and not e.get("binary") and classify(e["path"], cfg)["category"] != "skip"]
    except BgError:
        return None
    parts = ["Checked {} file{} (+{} −{}) on {}".format(
        len(entries), "" if len(entries) == 1 else "s", sum(e["added"] for e in entries),
        sum(e["deleted"] for e in entries), branch)]
    if areas:
        parts.append(", ".join(areas))
    if depth:
        parts.append("{} depth".format(depth))
    if incremental:
        parts.append("only changes since the last review")
    return " · ".join(parts)


def save_payload(root, cfg, payload, level, snapshot, areas_run, branch=None, base_ref=None, base_commit=None,
                 depth=None, from_tree=None, checked=None, complete=True):
    """Merge a review's or a fix's results into the branch's findings; return the report text."""
    branch = branch or branch_name(root)
    st = load_state(root, branch)
    mode = "fix" if level == "fix" else "review"
    created_checks, closed_checks = [], []
    if payload.get("checks") is not None:
        created_checks, closed_checks = apply_checks(st, payload["checks"], snapshot)
    fixed = list(closed_checks)
    for fid in payload.get("resolved", []) or []:
        for f in st["findings"]:
            if f["id"] == fid and f["status"] == "open":
                f["status"] = "fixed"
                f["fixed_at"] = now_iso()
                fixed.append(fid)
    new_ids, updated, suppressed, reopened = merge_findings(st, payload.get("findings", []) or [])
    new_ids = created_checks + new_ids
    dropped = enforce_low_cap(st, int(cfg.get("low_cap", 5)))
    st_new = [f for f in st["findings"] if f["id"] in new_ids]

    now = now_iso()
    reviewed = [a for a in areas_run if a != "checks"]
    incremental = bool(reviewed) and mode == "review" and all(
        st["coverage"].get(a, {}).get(lvl) for a in reviewed for lvl in REVIEW_COVERS[level])
    if mode == "review":
        for area in areas_run:
            cov = st["coverage"].setdefault(area, {})
            for lvl in REVIEW_COVERS[level]:
                cov[lvl] = snapshot
        if level in ("all", "blockers") and complete:
            st["blocker_review"] = {"tree": snapshot, "at": now, "level": level}
    else:
        for area in areas_run:
            cov = st["coverage"].setdefault(area, {})
            for lvl in ("blocker", "high"):
                if cov.get(lvl) == from_tree:
                    cov[lvl] = snapshot
        review = st.get("blocker_review")
        if review and review.get("tree") == from_tree and complete:
            review["tree"] = snapshot
            review["at"] = now
    rounds = st.setdefault("rounds", {})
    for area in areas_run:
        serious = [f for f in st_new if f.get("area") == area and f["level"] in ("blocker", "high")
                   and not f["pre_existing"]]
        rounds[area] = rounds.get(area, 0) + 1 if serious else 0
    info = {"level": level, "areas": areas_run, "snapshot": snapshot, "at": now,
            "base_ref": base_ref, "base_commit": base_commit, "depth": depth}
    if mode == "review":
        st["last_review"] = info
    else:
        st["last_fix"] = info
    save_state(root, st)
    log_event(root, {"event": "save", "mode": mode, "level": level, "branch": branch,
                     "new": new_ids, "fixed": fixed, "suppressed": suppressed, "reopened": reopened})

    base_for_ready = base_commit or (st.get("last_review") or {}).get("base_commit")
    ready = push_readiness(root, st, snapshot, base_for_ready)
    scope_info = {"fixed": fixed,
                  "still_open": [i for i in (payload.get("still_open") or [])
                                 if any(f["id"] == i and f["status"] == "open" for f in st["findings"])]}
    if checked:
        scope_info["checked"] = checked
    elif mode == "review":
        scope_info["checked"] = describe_checked(root, cfg, branch, base_for_ready, snapshot, areas_run,
                                                 depth, incremental)
    return render_report(root, st, level if mode == "review" else "all", mode, new_ids, suppressed,
                         reopened, dropped, ready, scope_info)


def cmd_save(args):
    root = require_root()
    if args.json is not None:
        raw = args.json
    elif args.input:
        with open(args.input, encoding="utf-8") as fh:
            raw = fh.read()
    else:
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        raise BgError("save expects a JSON payload: {}".format(exc))
    areas_run = [a for a in (args.areas or "").replace(",", " ").split() if a]
    print(save_payload(root, load_config(root), payload, args.level, args.snapshot, areas_run, branch=args.branch,
                       base_ref=args.base_ref, base_commit=args.base_commit, depth=args.depth,
                       from_tree=args.from_tree, checked=args.checked))

# ----------------------------------------------------------------------------- status, list, dismiss

def parse_level_arg(value):
    value = (value or "").strip().lower()
    if not value or value == "all":
        return None
    mapping = {"blockers": "blocker", "blocker": "blocker", "merge-blockers": "blocker",
               "high": "high", "medium": "medium", "low": "low", "pre-existing": "pre", "pre": "pre"}
    if value not in mapping:
        raise BgError("unknown level '{}': use blockers, high, medium, low or pre-existing".format(value))
    return mapping[value]


def status_text(root, branch, level_word=None, detail=False):
    st = load_state(root, branch)
    level = parse_level_arg(level_word)
    snapshot = snapshot_tree(root)
    tip = "HEAD" if has_head(root) else None
    base_ref, base_commit = resolve_base(root, "HEAD") if tip else (None, None)
    ready = push_readiness(root, st, snapshot, base_commit)
    c = counts(st)
    lines = ["botgauge status — branch {}".format(branch)]
    last = st.get("last_review")
    if last:
        lines.append("Last review: {} · {} · areas: {}".format(
            last.get("at", "?").replace("T", " ")[:16], "all levels" if last.get("level") == "all"
            else last.get("level"), ", ".join(last.get("areas") or []) or "none"))
    else:
        lines.append("No review yet on this branch. Run /botgauge:review.")
    open_bits = count_phrase(c) or "nothing"
    lines.append("Open: {}{} · fixed {} · dismissed {}".format(
        open_bits, " · {} pre-existing".format(c["pre"]) if c["pre"] else "", c["fixed"], c["dismissed"]))
    lines.append(readiness_line(ready))
    if ready["verdict"] == "needs-review":
        lines.append("  not yet checked: " + ", ".join(ready["unreviewed"][:8]) +
                     (" …" if len(ready["unreviewed"]) > 8 else ""))
    if level == "pre":
        items = [f for f in open_findings(st) if f["pre_existing"]]
    elif level:
        items = [f for f in open_findings(st, [level], include_pre=False)]
    else:
        items = open_findings(st)
    if items:
        lines += render_findings_block(items, detail=detail)
    elif level:
        lines.append("")
        lines.append("No open {} findings.".format({"blocker": "merge-blocker", "pre": "pre-existing"}.get(level, level)))
    return "\n".join(lines)


def cmd_status(args):
    root = require_root()
    print(status_text(root, args.branch or branch_name(root), args.level, args.detail))


def cmd_list(args):
    root = require_root()
    branch = args.branch or branch_name(root)
    st = load_state(root, branch)
    ids = [i.strip().upper() for i in (args.ids or "").replace(",", " ").split() if i.strip()]
    if ids:
        by_id = {f["id"]: f for f in st["findings"]}
        unknown = [i for i in ids if i not in by_id]
        chosen = [by_id[i] for i in ids if i in by_id and by_id[i]["status"] == "open"]
        not_open = [i for i in ids if i in by_id and by_id[i]["status"] != "open"]
    else:
        level = parse_level_arg(args.level)
        chosen = open_findings(st, [level] if level and level != "pre" else None,
                               include_pre=(level == "pre"))
        if level == "pre":
            chosen = [f for f in chosen if f["pre_existing"]]
        unknown, not_open = [], []
    areas = sorted({f.get("area") for f in chosen})
    step_back = sorted(a for a, n in (st.get("rounds") or {}).items() if n >= 3 and a in areas)
    print(json.dumps({"branch": branch, "snapshot": snapshot_tree(root), "findings": chosen,
                      "unknown_ids": unknown, "not_open_ids": not_open, "step_back": step_back},
                     indent=1, ensure_ascii=False))


def cmd_dismiss(args):
    root = require_root()
    branch = args.branch or branch_name(root)
    st = load_state(root, branch)
    category = args.category.lower()
    if category not in DISMISS_CATEGORIES:
        raise BgError("reason type must be one of: {}".format(", ".join(DISMISS_CATEGORIES)))
    reason = " ".join(args.reason).strip()
    if not reason:
        raise BgError("say why in a few words, e.g. /botgauge:dismiss H2 intentional \"retries are idempotent\"")
    fid = args.id.upper()
    for f in st["findings"]:
        if f["id"] == fid:
            if f["status"] != "open":
                raise BgError("{} is already {}".format(fid, f["status"]))
            f["status"] = "dismissed"
            f["dismissed_as"] = category
            f["dismiss_reason"] = reason
            f["dismissed_at"] = now_iso()
            save_state(root, st)
            log_event(root, {"event": "dismiss", "id": fid, "level": f["level"], "area": f.get("area"),
                             "category": category, "branch": branch})
            msg = "{} dismissed ({}): {}".format(fid, category, reason)
            if f["level"] == "blocker" and not f["pre_existing"]:
                msg += "\nIt no longer blocks the push. The dismissal is recorded in .botgauge/local/log.jsonl."
            print(msg)
            return
    raise BgError("no finding {} on branch {} (see /botgauge:status)".format(fid, branch))


def cmd_snapshot(args):
    print(snapshot_tree(require_root()))


def cmd_changed(args):
    root = require_root()
    cfg = load_config(root)
    entries = [e for e in changed_entries(root, args.from_tree, args.to_tree) if e["status"] != "D"]
    for e in entries:
        e.update(classify(e["path"], cfg))
    reviewable = [e for e in entries if e["category"] != "skip"]
    run_dir = os.path.join(ensure_local(root), "run")
    os.makedirs(run_dir, exist_ok=True)
    diff = write_diff(root, args.from_tree, args.to_tree, [e["path"] for e in reviewable],
                      os.path.join(run_dir, "fix.diff"))
    print(json.dumps({"files": reviewable, "diff_file": diff}, indent=1))


# ----------------------------------------------------------------------------- detect (for setup)

def read_text(path, limit=200000):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def cmd_detect(args):
    root = require_root()
    files = [p for p in run_git(["ls-files"], root).stdout.splitlines() if p]
    langs = Counter(CODE_EXT[os.path.splitext(p)[1].lower()] for p in files
                    if os.path.splitext(p)[1].lower() in CODE_EXT and not any_glob(p, DEFAULT_SKIP))

    py_files = [p for p in files if p.endswith(".py") and not any_glob(p, DEFAULT_SKIP)][:600]
    styles = Counter()
    with_docstring = 0
    for p in py_files:
        text = read_text(os.path.join(root, p), 100000)
        if re.search(r'^\s*(?:r|u|R|U)?("""|\'\'\')', text, re.M):
            with_docstring += 1
        if re.search(r"^\s+(Args|Returns|Raises|Yields|Attributes):\s*$", text, re.M):
            styles["google"] += 1
        if re.search(r"^\s+(Parameters|Returns)\s*\n\s+-{3,}\s*$", text, re.M):
            styles["numpy"] += 1
        if re.search(r":param\s|:returns?:|:raises?\s", text):
            styles["sphinx"] += 1
    js_files = [p for p in files if os.path.splitext(p)[1] in (".ts", ".tsx", ".js", ".jsx")
                and not any_glob(p, DEFAULT_SKIP)][:600]
    jsdoc = sum(1 for p in js_files if re.search(r"/\*\*[\s\S]*?@(param|returns?)\b",
                                                  read_text(os.path.join(root, p), 100000)))

    manifests = {}
    for p in files:
        name = os.path.basename(p)
        if name in ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", "setup.py", "setup.cfg",
                    "Makefile", "justfile", "tox.ini", "noxfile.py", ".pre-commit-config.yaml",
                    "pyrightconfig.json", "mypy.ini", "tsconfig.json", "eslint.config.js",
                    "eslint.config.mjs", ".eslintrc.json", ".eslintrc.js", ".prettierrc", "biome.json"):
            manifests.setdefault(name, []).append(p)

    python_projects = []
    for p in manifests.get("pyproject.toml", [])[:10]:
        text = read_text(os.path.join(root, p))
        name = re.search(r'^\s*name\s*=\s*["\']([^"\']+)', text, re.M)
        tools = sorted(set(re.findall(r"^\[tool\.([a-z0-9_-]+)", text, re.M)))
        python_projects.append({
            "path": p, "dir": os.path.dirname(p) or ".", "name": name.group(1) if name else None,
            "tools": tools, "has_project_table": "[project]" in text,
            "private_classifier": "Private :: Do Not Upload" in text,
            "mentions": sorted({t for t in ("ruff", "black", "isort", "flake8", "pylint", "mypy", "pyright",
                                             "pytest", "pydocstyle", "interrogate") if t in text}),
        })
    node_projects = []
    for p in manifests.get("package.json", [])[:10]:
        data = read_json(os.path.join(root, p), {}) or {}
        deps = dict(data.get("dependencies") or {}, **(data.get("devDependencies") or {}))
        directory = os.path.dirname(p) or "."
        lock = next((m for m, f in (("pnpm", "pnpm-lock.yaml"), ("yarn", "yarn.lock"), ("bun", "bun.lockb"),
                                   ("npm", "package-lock.json"))
                     if os.path.exists(os.path.join(root, directory, f))), "npm")
        node_projects.append({
            "path": p, "dir": directory, "name": data.get("name"), "private": data.get("private"),
            "scripts": data.get("scripts") or {}, "package_manager": lock,
            "frameworks": sorted(k for k in ("react", "next", "vue", "nuxt", "svelte", "@angular/core",
                                             "express", "fastify", "@nestjs/core", "@tanstack/react-query",
                                             "swr", "axios", "@reduxjs/toolkit", "@apollo/client")
                                 if k in deps),
            "tools": sorted(k for k in ("eslint", "prettier", "typescript", "jest", "vitest", "@biomejs/biome",
                                        "playwright", "@playwright/test", "cypress") if k in deps),
        })

    workflows = [p for p in files if p.startswith(".github/workflows/")]
    run_lines = []
    for p in workflows[:20]:
        for line in read_text(os.path.join(root, p)).splitlines():
            stripped = line.strip()
            if stripped.startswith("run:") or stripped.startswith("- run:"):
                run_lines.append("{}: {}".format(p, stripped.split("run:", 1)[1].strip()[:200]))
    guidance = [p for p in ("CLAUDE.md", "AGENTS.md", "REVIEW.md", "CONTRIBUTING.md", "README.md",
                            "docs/CONTRIBUTING.md", ".github/CONTRIBUTING.md")
                if os.path.exists(os.path.join(root, p))]

    dirs = Counter()
    for p in files:
        parts = p.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            if SENSITIVE_RE.search(parts[i - 1]):
                dirs[d] += 1
    sensitive_dirs = [d for d, _ in dirs.most_common(25)]
    sensitive_files = [p for p in files if SENSITIVE_RE.search(os.path.basename(p))
                       and os.path.splitext(p)[1] in CODE_EXT][:25]

    hooks_path = run_git(["config", "--get", "core.hooksPath"], root, check=False).stdout.strip()
    published_python = [pp["path"] for pp in python_projects
                        if pp["has_project_table"] and not pp["private_classifier"]]
    published_node = [np_["path"] for np_ in node_projects if np_["private"] is not True and np_["name"]]
    result = {
        "repo": root, "remote": run_git(["remote", "get-url", "origin"], root, check=False).stdout.strip(),
        "default_branch": default_remote_branch(root), "tracked_files": len(files),
        "languages": dict(langs.most_common()),
        "python_docstrings": {"files_checked": len(py_files), "files_with_docstrings": with_docstring,
                              "styles": dict(styles)},
        "js_doc_comments": {"files_checked": len(js_files), "files_with_param_docs": jsdoc},
        "manifests": manifests, "python_projects": python_projects, "node_projects": node_projects,
        "go_modules": manifests.get("go.mod", []),
        "ci_workflows": workflows, "ci_run_lines": run_lines[:60], "guidance_files": guidance,
        "frontend_hint": [np_["dir"] for np_ in node_projects
                          if set(np_["frameworks"]) & {"react", "next", "vue", "nuxt", "svelte", "@angular/core"}],
        "sensitive_dirs": sensitive_dirs, "sensitive_files": sensitive_files,
        "published_hint": {"python": published_python, "node": published_node, "go": manifests.get("go.mod", [])},
        "git_hooks": {"core.hooksPath": hooks_path or None,
                      "husky": os.path.isdir(os.path.join(root, ".husky")),
                      "pre_commit_config": os.path.exists(os.path.join(root, ".pre-commit-config.yaml")),
                      "botgauge_installed": hook_status(root)["installed"]},
        "existing_config": read_json(config_path(root)),
        "tools_on_path": {t: bool(shutil.which(t)) for t in ("ruff", "black", "mypy", "pyright", "pytest",
                                                                 "npx", "npm", "pnpm", "yarn", "go", "gofmt")},
    }
    print(json.dumps(result, indent=1, ensure_ascii=False))


# ----------------------------------------------------------------------------- git pre-push hook

def hooks_dir(root):
    path = git_out(["rev-parse", "--git-path", "hooks"], root)
    return path if os.path.isabs(path) else os.path.join(root, path)


SHIM = """#!/bin/sh
# {marker}: runs this repo's committed check (.botgauge/pre-push) before git push.
# Installed by /botgauge:setup. Skip once with: git push --no-verify
hookdir="$(cd "$(dirname "$0")" && pwd)"
root="$(git rev-parse --show-toplevel)" || exit 0
input="$(mktemp "${{TMPDIR:-/tmp}}/botgauge-push.XXXXXX")" || exit 1
trap 'rm -f "$input"' EXIT
cat > "$input"
if [ -x "$hookdir/pre-push.botgauge-chained" ]; then
  "$hookdir/pre-push.botgauge-chained" "$@" < "$input" || exit $?
fi
if [ -f "$root/.botgauge/pre-push" ]; then
  python3 "$root/.botgauge/pre-push" "$@" < "$input" || exit $?
fi
exit 0
"""


def hook_status(root):
    husky_hook = os.path.join(root, ".husky", "pre-push")
    if os.path.exists(husky_hook) and ".botgauge/pre-push" in read_text(husky_hook):
        return {"installed": True, "where": husky_hook, "kind": "husky"}
    hook = os.path.join(hooks_dir(root), "pre-push")
    if os.path.exists(hook) and SHIM_MARKER in read_text(hook):
        return {"installed": True, "where": hook, "kind": "shim"}
    return {"installed": False, "where": hook, "kind": None}


def gate_version(path):
    match = re.search(r"^BOTGAUGE_GATE_VERSION\s*=\s*(\d+)", read_text(path), re.M)
    return int(match.group(1)) if match else None


def cmd_install_hook(args):
    root = require_root()
    committed = []
    gate = os.path.join(root, ".botgauge", "pre-push")
    os.makedirs(os.path.dirname(gate), exist_ok=True)
    if args.remove:
        status = hook_status(root)
        if status["kind"] == "shim":
            os.remove(status["where"])
            chained = status["where"] + ".botgauge-chained"
            if os.path.exists(chained):
                os.replace(chained, status["where"])
        elif status["kind"] == "husky":
            text = read_text(status["where"])
            kept = [line for line in text.splitlines() if ".botgauge/pre-push" not in line]
            with open(status["where"], "w", encoding="utf-8") as fh:
                fh.write("\n".join(kept) + "\n")
            committed.append(os.path.relpath(status["where"], root))
        print(json.dumps({"removed": status["installed"], "where": status["where"], "commit": committed}))
        return
    if not os.path.exists(gate) or args.update or gate_version(gate) != gate_version(GATE_TEMPLATE):
        shutil.copyfile(GATE_TEMPLATE, gate)
        os.chmod(gate, 0o755)
        committed.append(".botgauge/pre-push")
    gitignore = os.path.join(root, ".botgauge", ".gitignore")
    if "local/" not in read_text(gitignore).split():
        with open(gitignore, "a", encoding="utf-8") as fh:
            fh.write("local/\n")
        committed.append(".botgauge/.gitignore")
    ensure_local(root)
    status = hook_status(root)
    chained = False
    if status["installed"]:
        where, kind = status["where"], status["kind"]
    elif os.path.isdir(os.path.join(root, ".husky")):
        where, kind = os.path.join(root, ".husky", "pre-push"), "husky"
        text = read_text(where)
        with open(where, "a", encoding="utf-8") as fh:
            if not text:
                fh.write("#!/usr/bin/env sh\n")
            elif not text.endswith("\n"):
                fh.write("\n")
            fh.write('python3 .botgauge/pre-push "$@"\n')
        os.chmod(where, 0o755)
        committed.append(os.path.relpath(where, root))
    else:
        directory = hooks_dir(root)
        os.makedirs(directory, exist_ok=True)
        where, kind = os.path.join(directory, "pre-push"), "shim"
        if os.path.exists(where):
            os.replace(where, where + ".botgauge-chained")
            chained = True
        with open(where, "w", encoding="utf-8") as fh:
            fh.write(SHIM.format(marker=SHIM_MARKER))
        os.chmod(where, 0o755)
        if not os.path.relpath(where, root).startswith(".git" + os.sep):
            committed.append(os.path.relpath(where, root))
    print(json.dumps({"installed": True, "where": where, "kind": kind, "chained_existing_hook": chained,
                      "commit": committed}, indent=1))


def cmd_hook_status(args):
    root = require_root()
    status = hook_status(root)
    gate = os.path.join(root, ".botgauge", "pre-push")
    status["gate_present"] = os.path.exists(gate)
    status["gate_version"] = gate_version(gate) if os.path.exists(gate) else None
    status["plugin_gate_version"] = gate_version(GATE_TEMPLATE)
    print(json.dumps(status, indent=1))


# ----------------------------------------------------------------------------- Claude Code hooks

def cmd_hook_post_edit(args):
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path:
        return
    cwd = data.get("cwd") or os.getcwd()
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    if not os.path.isfile(path):
        return
    root = find_root(os.path.dirname(path))
    if not root or not os.path.exists(config_path(root)):
        return
    path = os.path.realpath(path)
    rel = os.path.relpath(path, os.path.realpath(root))
    if rel.startswith(".."):
        return
    cfg = load_config(root)
    if any_glob(rel, DEFAULT_SKIP) or any_glob(rel, cfg.get("paths", {}).get("skip")):
        return
    ext = os.path.splitext(rel)[1].lower()
    on_edit = cfg.get("on_edit") or {}
    commands = on_edit.get(ext) or on_edit.get(CODE_EXT.get(ext, "-")) or []
    if ext in (".tsx", ".jsx") and not commands:
        commands = on_edit.get("typescript" if ext == ".tsx" else "javascript") or []
    problems = []
    for command in commands:
        try:
            argv = [part.replace("{file}", path) for part in shlex.split(command)]
        except ValueError:
            continue
        try:
            proc = subprocess.run(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  universal_newlines=True, timeout=90)
        except (FileNotFoundError, PermissionError):
            continue
        except subprocess.TimeoutExpired:
            problems.append("`{}` timed out".format(command.replace("{file}", rel)))
            continue
        if proc.returncode != 0:
            problems.append("`{}` (exit {}):\n{}".format(command.replace("{file}", rel), proc.returncode,
                                                         tail(proc.stdout.replace(path, rel), 40, 3000)))
    if problems:
        print(json.dumps({"decision": "block", "reason": "botgauge: the formatter or linter reported problems in "
                          "{} after this edit. Fix them before moving on:\n\n{}".format(rel, "\n\n".join(problems))}))


def cmd_hook_session_start(args):
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        data = {}
    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    root = find_root(cwd)
    if not root or not os.path.exists(config_path(root)):
        return
    notes = []
    try:
        status = hook_status(root)
    except BgError:
        return
    gate = os.path.join(root, ".botgauge", "pre-push")
    if not status["installed"]:
        notes.append("This repo uses the botgauge pre-push check, but it is not switched on in this clone. "
                     "When it fits naturally, tell the user once that /botgauge:setup switches it on "
                     "(it stops a push while a merge blocker is open).")
    elif os.path.exists(gate) and (gate_version(gate) or 0) < (gate_version(GATE_TEMPLATE) or 0):
        notes.append("This repo's .botgauge/pre-push is older than the installed botgauge plugin; "
                     "/botgauge:setup --update refreshes it.")
    if notes:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": "botgauge: " + " ".join(notes)}}))


# ----------------------------------------------------------------------------- running reviewers

REVIEWERS_DIR = os.path.join(PLUGIN_ROOT, "reviewers")
AREA_REVIEWER = {"bugs": "bugs-reviewer", "security": "security-reviewer", "api": "api-reviewer",
                 "docs": "docs-reviewer", "tests": "tests-reviewer", "quality": "quality-reviewer",
                 "performance": "performance-reviewer", "frontend-api": "frontend-api-reviewer"}
AREA_ORDER = ["security", "bugs", "api", "frontend-api", "performance", "tests", "docs", "quality", "checks"]


def is_claude_binary(path):
    return bool(path) and os.path.basename(path) in ("claude", "claude.exe") and os.path.isfile(path) \
        and os.access(path, os.X_OK)


def find_claude():
    """The Claude Code binary that runs each reviewer.

    BOTGAUGE_CLAUDE overrides it (the tests point it at a fake). Inside Claude Code, CLAUDE_CODE_EXECPATH names
    the binary running this session. That copy has every flag the reviewers use, and it's the only copy for people
    who use just the VS Code extension, which doesn't put `claude` on PATH. The name check skips the variable
    when it names a runtime such as node rather than Claude Code itself.
    """
    override = os.environ.get("BOTGAUGE_CLAUDE")
    if override:
        return override
    running = os.environ.get("CLAUDE_CODE_EXECPATH")
    if is_claude_binary(running):
        return running
    found = shutil.which("claude")
    if found:
        return found
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, ".claude", "local", "claude"), os.path.join(home, ".local", "bin", "claude"),
                 "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if is_claude_binary(cand):
            return cand
    return None


def load_reviewer(name):
    """A reviewer's instructions, model, effort and tools, from reviewers/<name>.md."""
    path = os.path.join(REVIEWERS_DIR, name + ".md")
    text = read_text(path)
    if not text.startswith("---"):
        raise BgError("reviewer definition not found: {}".format(path))
    _, front, body = text.split("---", 2)
    meta = {}
    for line in front.strip().splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    tools = [t.strip() for t in meta.get("tools", "Read, Grep, Glob").split(",") if t.strip()]
    return {"name": name, "model": meta.get("model") or "sonnet", "effort": meta.get("effort"),
            "tools": tools, "body": body.strip()}


def run_reviewer(root, name, prompt, model=None, timeout=420, budget=3.0):
    """Run one reviewer as its own headless, read-only Claude Code session. Each runs on its own,
    so it needs no permission from the session that started the review."""
    spec = load_reviewer(name)
    claude = find_claude()
    if not claude:
        return {"ok": False, "text": "", "cost": 0.0, "seconds": 0, "error": "Claude Code wasn't found (no `claude` command on PATH)"}
    cmd = [claude, "-p", prompt, "--strict-mcp-config", "--append-system-prompt", spec["body"],
           "--model", model or spec["model"], "--permission-mode", "dontAsk", "--output-format", "json",
           "--max-budget-usd", "{:.2f}".format(budget)]
    if spec.get("effort"):
        cmd += ["--effort", spec["effort"]]
    cmd += ["--tools"] + spec["tools"]
    start = time.time()
    try:
        proc = subprocess.run(cmd, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "text": "", "cost": 0.0, "seconds": timeout,
                "error": "timed out after {}s".format(timeout)}
    except OSError as exc:
        return {"ok": False, "text": "", "cost": 0.0, "seconds": 0, "error": str(exc)}
    raw = proc.stdout or ""
    data = {}
    first = raw.find("{")
    if first >= 0:
        try:
            data = json.loads(raw[first:])
        except ValueError:
            data = {}
    ok = proc.returncode == 0 and bool(data) and not data.get("is_error")
    return {"ok": ok, "text": data.get("result") or "", "cost": float(data.get("total_cost_usd") or 0.0),
            "seconds": round(time.time() - start, 1), "error": "" if ok else tail(proc.stderr or raw, 5, 400)}


def extract_json(text):
    """The JSON object in a reviewer's answer: a ```json block, or the outermost {...}."""
    if not text:
        return None
    candidates = [m.group(1) for m in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.S)]
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first:last + 1])
    for cand in candidates:
        try:
            value = json.loads(cand)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


class Runner(object):
    """Runs reviewer calls in parallel and adds up what they cost."""

    def __init__(self, root, cfg, quiet=False):
        settings = cfg.get("review") or {}
        self.root = root
        self.workers = max(1, int(settings.get("parallel", 4)))
        self.timeout = int(settings.get("reviewer_timeout", 420))
        self.verifier_timeout = int(settings.get("verifier_timeout", 240))
        self.budget = float(settings.get("max_budget_per_call", 3.0))
        self.quiet = quiet
        self.cost = 0.0
        self.calls = 0
        self.lock = threading.Lock()

    def say(self, message):
        if not self.quiet:
            print("botgauge: " + message, file=sys.stderr)
            sys.stderr.flush()

    def run(self, jobs, timeout=None):
        """jobs: (reviewer name, prompt, model or None). Returns (result, parsed JSON or None) for
        each job, trying once more when the answer isn't usable JSON."""
        timeout = timeout or self.timeout

        def one(job):
            name, prompt, model = job
            result, data = None, None
            for _ in range(2):
                result = run_reviewer(self.root, name, prompt, model, timeout, self.budget)
                data = extract_json(result["text"]) if result["ok"] else None
                with self.lock:
                    self.cost += result["cost"]
                    self.calls += 1
                if data is not None:
                    break
            return result, data

        if not jobs:
            return []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(jobs))) as pool:
            return list(pool.map(one, jobs))


def bullet_findings(items, with_reason=False):
    lines = []
    for f in items[:40]:
        line = "  - {} {}:{} {}".format(f.get("id"), f.get("file", ""), f.get("line", ""), f.get("title", ""))
        if with_reason and f.get("dismiss_reason"):
            line += " ({}: {})".format(f.get("dismissed_as"), f.get("dismiss_reason"))
        lines.append(line)
    return lines


def reviewer_prompt(plan, areas, area_plan):
    """The task a reviewer gets: the same fields for every area."""
    files = area_plan["files"]
    lines = ["Area: {}".format(areas[0]) if len(areas) == 1 else "Areas to cover: {}".format(", ".join(areas)),
             "Review level: {}. Report findings at these levels: {}. Always report a merge blocker if you "
             "find one, whatever the level.".format(plan["level"], ", ".join(plan["covers_levels"])),
             "Repo: {}   Branch: {}".format(plan["repo"], plan["branch"]),
             "Files ({}): {}".format(area_plan["mode"], ", ".join(files)),
             "Diff to review: {} ({})".format(area_plan["diff_file"], "the whole change" if area_plan["mode"] == "full"
                                              else "only what changed since this area was last reviewed")]
    if area_plan["diff_file"] != plan["full_diff"]:
        lines.append("Whole change, for context: {}".format(plan["full_diff"]))
    lines.append("What the commits say they do: {}".format(
        "; ".join(plan["commits"][:15]) if plan["commits"] else "uncommitted work"))
    rules = plan["config"]["review_rules"]
    lines.append("Repo rules to read first: {}. REVIEW.md overrides your defaults.".format(
        ", ".join(rules) if rules else "none"))
    lines.append("Published package: {}. Docstring style: {}.".format(
        "yes" if plan["config"]["published"] else "no", json.dumps(plan["config"]["docstring_style"])))
    relevant = [f for f in plan["open_findings"] if f.get("area") in areas or f.get("file") in files]
    lines.append('Already open (don\'t report again; if the code now fixes one, put its ID in "resolved"):'
                 + ("" if relevant else " none"))
    lines += bullet_findings(relevant)
    dismissed = [f for f in plan["dismissed_findings"] if f.get("area") in areas or f.get("file") in files]
    lines.append("Dismissed by the team (never report again):" + ("" if dismissed else " none"))
    lines += bullet_findings(dismissed, with_reason=True)
    if len(areas) > 1 or set(areas) & {"bugs", "security", "api"}:
        history = {p: h for p, h in (plan.get("history") or {}).items() if p in files}
        if history:
            lines.append("History of these files before this change (check the change doesn't bring back a bug "
                         "that was fixed here before):")
            for path, h in history.items():
                label, items = ("earlier fixes", h["past_fixes"]) if h["past_fixes"] else ("recent commits", h["recent"][:3])
                lines.append("  - {} ({}): {}".format(path, label, " | ".join(items)))
    if "security" in areas and plan.get("secret_candidates"):
        lines.append("Secret-scanner hits on added lines (confirm each; drop placeholders and obvious test values): "
                     + "; ".join("{}:{} {}".format(h["file"], h["line"], h["kind"]) for h in plan["secret_candidates"]))
    if plan.get("reviewing_commit"):
        lines.append("This reviews commit {}. The working tree may hold newer edits; the diff is what "
                     "changed.".format(plan["reviewing_commit"]))
    lines.append("Answer with the JSON described in your instructions, and nothing else.")
    return "\n".join(lines)


def verifier_prompt(root, finding, diff_file, mode="verify"):
    shown = {k: v for k, v in finding.items() if k not in ("status", "created_at", "votes", "unverified")}
    return ("Mode: {}\nRepo: {}\nDiff: {}\nFinding:\n{}\nAnswer with the JSON described in your instructions, "
            "and nothing else.".format(mode, root, diff_file or "(none)", json.dumps(shown, indent=1, ensure_ascii=False)))


def merge_duplicates(findings):
    """Reviewers for different areas sometimes report one problem twice (a fetch loop is both a bug and a
    frontend-API problem). Keep the most severe report and fold the other into it.

    Only the wording decides: similar text on nearby lines, or nearly the same title anywhere in the file.
    Position alone never merges, because different problems often sit on neighbouring lines, and a lost
    finding costs far more than a duplicate. A folded report from elsewhere in the file keeps its location
    in other_locations, so the fix still reaches it."""
    def rank(f):
        level = LEVELS.index(f["level"]) if f.get("level") in LEVELS else len(LEVELS)
        area = AREA_ORDER.index(f["area"]) if f.get("area") in AREA_ORDER else len(AREA_ORDER)
        return (1 if f.get("pre_existing") else 0, level, area)

    merged = []
    for f in sorted(findings, key=rank):
        text = "{} {}".format(f.get("title", ""), f.get("problem", ""))
        same, gap = None, 99
        for kept in merged:
            if kept.get("file") != f.get("file"):
                continue
            try:
                gap = abs(int(kept.get("line") or 0) - int(f.get("line") or 0))
            except (TypeError, ValueError):
                gap = 99
            sim = similarity(text, "{} {}".format(kept.get("title", ""), kept.get("problem", "")))
            if (gap <= 3 and sim >= 0.3) or similarity(f.get("title"), kept.get("title")) >= 0.8:
                same = kept
                break
        if same is None:
            merged.append(dict(f))
            continue
        if same.get("area") != f.get("area") and f.get("title") and f["title"] not in (same.get("problem") or ""):
            same["problem"] = "{} Also: {}.".format(same.get("problem") or "", f["title"].rstrip(".")).strip()
        locations = list(same.get("other_locations") or [])
        extra = list(f.get("other_locations") or [])
        if gap > 3 and f.get("line"):
            extra.insert(0, "{}:{}".format(f.get("file"), f.get("line")))
        for loc in extra:
            if loc not in locations:
                locations.append(loc)
        if locations:
            same["other_locations"] = locations
    return merged


def as_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def vote(runner, root, findings, diff_file, depth):
    """Verify findings before anyone sees them. A merge blocker stops someone's push, so it needs a
    majority of three independent verifiers; security findings always get three. Two verifiers run
    first, and the third only when they disagree: if two agree, a third vote can't change a
    majority of three, so the outcome is the same as asking all three."""
    plans = {}
    for idx, f in enumerate(findings):
        if f.get("pre_existing"):
            continue
        if f.get("area") == "security":
            plans[idx] = ("security-verifier", 3)
        elif f["level"] == "blocker":
            plans[idx] = ("finding-verifier", 3)
        elif f["level"] == "high":
            plans[idx] = ("finding-verifier", 3 if depth == "deep" else 1)
        elif f["level"] == "medium" and depth == "deep":
            plans[idx] = ("finding-verifier", 1)
    if not plans:
        return findings

    def is_yes(kind, verdict):
        if kind == "security-verifier":
            return verdict.get("keep") is True and as_number(verdict.get("confidence")) >= 8
        return verdict.get("verdict") == "real"

    ballots = {idx: [] for idx in plans}

    def ask(wanted):
        jobs, owners = [], []
        for idx, count in wanted:
            prompt = verifier_prompt(root, findings[idx], diff_file)
            for _ in range(count):
                jobs.append((plans[idx][0], prompt, None))
                owners.append(idx)
        for idx, (_, data) in zip(owners, runner.run(jobs, timeout=runner.verifier_timeout)):
            if data is not None:
                ballots[idx].append(data)

    runner.say("verifying {} finding{}…".format(len(plans), "" if len(plans) == 1 else "s"))
    ask([(idx, 2 if count == 3 else 1) for idx, (_, count) in plans.items()])
    tie_breaks = []
    for idx, (kind, count) in plans.items():
        if count != 3:
            continue
        yes = sum(1 for v in ballots[idx] if is_yes(kind, v))
        no = len(ballots[idx]) - yes
        if yes < 2 and no < 2:
            tie_breaks.append((idx, max(1, 3 - len(ballots[idx]) - (1 if len(ballots[idx]) == 2 else 0))))
    if tie_breaks:
        ask(tie_breaks)

    kept = []
    for idx, f in enumerate(findings):
        if idx not in plans:
            kept.append(f)
            continue
        kind = plans[idx][0]
        votes = ballots[idx]
        if not votes:
            f["unverified"] = True
            kept.append(f)
            continue
        yes = [v for v in votes if is_yes(kind, v)]
        if len(yes) < len(votes) // 2 + 1:
            continue
        if kind == "finding-verifier":
            levels = [v.get("level") for v in yes if v.get("level") in LEVELS]
            if levels:
                tally = Counter(levels)
                top = max(tally.values())
                f["level"] = max((lvl for lvl, n in tally.items() if n == top), key=LEVELS.index)
        if sum(1 for v in yes if v.get("pre_existing")) * 2 > len(yes):
            f["pre_existing"] = True
        fixes = [v.get("fix") for v in yes if v.get("fix")]
        if fixes and len(fixes) * 2 > len(yes):
            f["fix"] = fixes[0]
        f["votes"] = "{}/{}".format(len(yes), len(votes))
        kept.append(f)
    return kept


def confirm_resolved(runner, root, st, claimed, diff_file):
    """Reviewers may say an open finding is now fixed. Merge blockers and high findings are only
    closed when a verifier agrees."""
    by_id = {f["id"]: f for f in st["findings"] if f["status"] == "open"}
    accepted, jobs, owners = [], [], []
    for fid in sorted(set(claimed)):
        f = by_id.get(fid)
        if not f or f.get("area") == "checks":
            continue
        if f["level"] in ("blocker", "high") and not f.get("pre_existing"):
            jobs.append(("finding-verifier", verifier_prompt(root, f, diff_file, mode="recheck"), None))
            owners.append(fid)
        else:
            accepted.append(fid)
    for fid, (_, data) in zip(owners, runner.run(jobs, timeout=runner.verifier_timeout)):
        if data and data.get("resolved") is True:
            accepted.append(fid)
    return accepted


def apply_review_targets(args):
    """Let `review` take the same arguments users type after /botgauge:review: area names and
    paths in any order, plus --quick or --deep."""
    areas = [a for a in (args.areas or "").replace(",", " ").split() if a]
    paths = list(args.path or [])
    for target in args.targets or []:
        for part in target.replace(",", " ").split():
            (areas if part in AREAS else paths).append(part)
    args.areas = ",".join(areas)
    args.path = paths or None
    if args.quick:
        args.depth = "quick"
    elif args.deep:
        args.depth = "deep"
    return args


def cmd_review(args):
    root = require_root()
    cfg = load_config(root)
    plan = compute_scope(apply_review_targets(args))
    branch = plan["branch"]
    runner = Runner(root, cfg, quiet=args.quiet)
    if plan["nothing_to_review"]:
        print("Nothing new to review since the last review.\n")
        print(status_text(root, branch))
        return
    checks = None
    if (plan["areas"].get("checks") or {}).get("mode") == "run":
        runner.say("running the repo's checks…")
        checks = run_checks_list(root, cfg)
    areas = [a for a in AREA_ORDER if a != "checks" and (plan["areas"].get(a) or {}).get("mode") in ("full", "incremental")]
    jobs, job_areas = [], []
    if areas and plan["depth"] == "quick":
        files = sorted({path for a in areas for path in plan["areas"][a]["files"]})
        full = any(plan["areas"][a]["mode"] == "full" for a in areas)
        area_plan = {"mode": "full" if full else "incremental", "files": files,
                     "diff_file": plan["full_diff"] if full else plan["areas"][areas[0]]["diff_file"]}
        jobs.append(("quick-reviewer", reviewer_prompt(plan, areas, area_plan), None))
        job_areas.append(areas)
    else:
        for a in areas:
            model = "opus" if plan["depth"] == "deep" and a in ("api", "frontend-api") else None
            jobs.append((AREA_REVIEWER[a], reviewer_prompt(plan, [a], plan["areas"][a]), model))
            job_areas.append([a])
    if jobs:
        runner.say("reviewing {} at {} depth ({} reviewer{} in parallel)…".format(
            ", ".join(areas), plan["depth"], len(jobs), "" if len(jobs) == 1 else "s"))
    findings, claimed, failed = [], [], []
    for areas_of_job, (result, data) in zip(job_areas, runner.run(jobs)):
        if data is None:
            failed += areas_of_job
            runner.say("the {} review failed: {}".format(", ".join(areas_of_job), result.get("error") or "no usable answer"))
            continue
        for raw in data.get("findings") or []:
            if not isinstance(raw, dict):
                continue
            if raw.get("area") not in AREAS:
                raw["area"] = areas_of_job[0]
            findings.append(normalize_finding(raw))
        claimed += [r for r in data.get("resolved") or [] if isinstance(r, str)]
    findings = merge_duplicates(findings)
    findings = vote(runner, root, findings, plan["full_diff"], plan["depth"])
    st = load_state(root, branch)
    resolved = confirm_resolved(runner, root, st, claimed, plan["full_diff"]) if claimed else []
    payload = {"findings": findings, "resolved": resolved}
    if checks is not None:
        payload["checks"] = checks
    done = [a for a in areas if a not in failed] + (["checks"] if checks is not None else [])
    print(save_payload(root, cfg, payload, plan["level"], plan["snapshot"], done, branch=branch,
                       base_ref=plan["base_ref"], base_commit=plan["base_commit"], depth=plan["depth"],
                       complete=not failed))
    if failed:
        print("\nNot reviewed this time, because the reviewer failed twice: {}. Run the review again.".format(
            ", ".join(failed)))
    print("\nCost: ${:.2f} across {} Claude call{} (at API prices; on a Claude plan it counts toward usage).".format(
        runner.cost, runner.calls, "" if runner.calls == 1 else "s"))
    log_event(root, {"event": "review_cost", "level": plan["level"], "depth": plan["depth"], "branch": branch,
                     "cost": round(runner.cost, 4), "calls": runner.calls, "failed": failed})


def cmd_recheck(args):
    """After a fix: confirm each finding is resolved, look for new problems in the fix itself, run the
    checks, and save."""
    root = require_root()
    cfg = load_config(root)
    branch = args.branch or branch_name(root)
    st = load_state(root, branch)
    ids = [i.strip().upper() for i in (args.ids or "").replace(",", " ").split() if i.strip()]
    targets = [f for f in st["findings"] if f["id"] in ids and f["status"] == "open"]
    before = args.from_tree
    after = snapshot_tree(root)
    runner = Runner(root, cfg, quiet=args.quiet)
    entries = [e for e in changed_entries(root, before, after)
               if e["status"] != "D" and classify(e["path"], cfg)["category"] != "skip"]
    run_dir = os.path.join(ensure_local(root), "run")
    os.makedirs(run_dir, exist_ok=True)
    diff_file = write_diff(root, before, after, [e["path"] for e in entries], os.path.join(run_dir, "fix.diff"))
    checks = None
    if cfg.get("checks"):
        runner.say("running the repo's checks…")
        checks = run_checks_list(root, cfg, for_fix=True)
    jobs, owners = [], []
    for f in targets:
        if f.get("area") == "checks":
            continue
        jobs.append(("finding-verifier", verifier_prompt(root, f, diff_file, mode="recheck"), None))
        owners.append(f["id"])
    areas = sorted({f["area"] for f in targets if f.get("area") not in (None, "checks")} | {"security"})
    if entries:
        prompt = "\n".join([
            "Areas to cover: {}".format(", ".join(areas)),
            "Only review the fix changes in {}. Report only merge blockers or high-priority problems these changes "
            "introduce.".format(diff_file),
            "Repo: {}   Branch: {}".format(root, branch),
            "Files: {}".format(", ".join(e["path"] for e in entries)),
            "Answer with the JSON described in your instructions, and nothing else."])
        jobs.append(("quick-reviewer", prompt, None))
    if jobs:
        runner.say("rechecking {} finding{} and the fix itself…".format(len(owners), "" if len(owners) == 1 else "s"))
    results = runner.run(jobs, timeout=runner.verifier_timeout)
    resolved, still_open, new = [], [], []
    for fid, (_, data) in zip(owners, results[:len(owners)]):
        (resolved if data and data.get("resolved") is True else still_open).append(fid)
    if entries and len(results) > len(owners):
        data = results[-1][1] or {}
        new = [normalize_finding(f) for f in data.get("findings") or []
               if isinstance(f, dict) and str(f.get("level", "")).lower() in ("blocker", "high")]
        new = vote(runner, root, merge_duplicates(new), diff_file, "standard")
    payload = {"resolved": resolved, "still_open": still_open, "findings": new}
    if checks is not None:
        payload["checks"] = checks
    print(save_payload(root, cfg, payload, "fix", after, areas, branch=branch, from_tree=before))
    print("\nCost: ${:.2f} across {} Claude call{} (at API prices; on a Claude plan it counts toward usage).".format(
        runner.cost, runner.calls, "" if runner.calls == 1 else "s"))


# ----------------------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(prog="bg.py", description="botgauge review helper")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("scope")
    p.add_argument("--level", default="all", choices=REVIEW_LEVELS)
    p.add_argument("--areas", default="")
    p.add_argument("--full", action="store_true")
    p.add_argument("--path", action="append")
    p.add_argument("--depth", choices=("quick", "standard", "deep"))
    p.add_argument("--commit")
    p.add_argument("--base")
    p.add_argument("--branch")
    p.set_defaults(func=cmd_scope)

    for name in ("review",):
        p = sub.add_parser(name)
        p.add_argument("--level", default="all", choices=REVIEW_LEVELS)
        p.add_argument("--areas", default="")
        p.add_argument("--full", action="store_true")
        p.add_argument("--path", action="append")
        p.add_argument("--depth", choices=("quick", "standard", "deep"))
        p.add_argument("--commit")
        p.add_argument("--base")
        p.add_argument("--branch")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--quick", action="store_true")
        p.add_argument("--deep", action="store_true")
        p.add_argument("--from-pre-push", action="store_true", help="started by the git pre-push check")
        p.add_argument("targets", nargs="*", help="area names and/or paths")
        p.set_defaults(func=cmd_review)

    p = sub.add_parser("recheck")
    p.add_argument("--from", dest="from_tree", required=True)
    p.add_argument("--ids", default="")
    p.add_argument("--branch")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_recheck)

    p = sub.add_parser("run-checks")
    p.add_argument("--for-fix", action="store_true")
    p.add_argument("--only")
    p.set_defaults(func=cmd_run_checks)

    p = sub.add_parser("save")
    p.add_argument("--level", required=True, choices=REVIEW_LEVELS + ("fix",))
    p.add_argument("--snapshot", required=True)
    p.add_argument("--areas", default="")
    p.add_argument("--from-tree")
    p.add_argument("--base-ref")
    p.add_argument("--base-commit")
    p.add_argument("--depth")
    p.add_argument("--checked", help="override the 'Checked …' line shown under the summary")
    p.add_argument("--json", help="the payload as one line of JSON (instead of stdin)")
    p.add_argument("--branch")
    p.add_argument("--input")
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("status")
    p.add_argument("level", nargs="?")
    p.add_argument("--detail", action="store_true")
    p.add_argument("--branch")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("list")
    p.add_argument("--level")
    p.add_argument("--ids")
    p.add_argument("--branch")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("dismiss")
    p.add_argument("id")
    p.add_argument("category")
    p.add_argument("reason", nargs="*")
    p.add_argument("--branch")
    p.set_defaults(func=cmd_dismiss)

    p = sub.add_parser("snapshot")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("changed")
    p.add_argument("from_tree")
    p.add_argument("to_tree")
    p.set_defaults(func=cmd_changed)

    p = sub.add_parser("detect")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("install-hook")
    p.add_argument("--update", action="store_true")
    p.add_argument("--remove", action="store_true")
    p.set_defaults(func=cmd_install_hook)

    p = sub.add_parser("hook-status")
    p.set_defaults(func=cmd_hook_status)

    p = sub.add_parser("hook-post-edit")
    p.set_defaults(func=cmd_hook_post_edit)

    p = sub.add_parser("hook-session-start")
    p.set_defaults(func=cmd_hook_session_start)

    args, extra = parser.parse_known_args(argv)
    if extra:
        # `review` takes area names and paths anywhere among its options (security --deep src/).
        unknown = [x for x in extra if x.startswith("-")]
        if args.cmd != "review" or unknown:
            parser.error("unrecognized arguments: {}".format(" ".join(unknown or extra)))
        args.targets = list(args.targets or []) + extra
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        args.func(args)
    except BgError as exc:
        if args.cmd and args.cmd.startswith("hook-"):
            return 0
        print("botgauge: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
