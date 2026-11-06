"""Tests for the botgauge helper (scripts/bg.py) and the pre-push check (templates/pre-push.py).

Run from the repo root:  python3 -m unittest discover -s tests -v
Each test builds throwaway git repos in a temp folder; nothing calls Claude.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "plugins", "botgauge")
BG = os.path.join(PLUGIN, "scripts", "bg.py")


def sh(cmd, cwd, stdin=None, check=True, env=None):
    proc = subprocess.run(cmd, cwd=cwd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, env=env)
    if check and proc.returncode != 0:
        raise AssertionError("{} failed ({}):\n{}\n{}".format(cmd, proc.returncode, proc.stdout, proc.stderr))
    return proc


def bg(args, cwd, stdin=None, check=True):
    return sh([sys.executable, BG] + args, cwd, stdin=stdin, check=check)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


class RepoCase(unittest.TestCase):
    """A clone with a bare 'origin', main pushed, and a feature branch checked out."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="botgauge-test-")
        sh(["git", "init", "-q", "--bare", "remote.git"], self.tmp)
        sh(["git", "clone", "-q", "remote.git", "work"], self.tmp)
        self.repo = os.path.join(self.tmp, "work")
        for key, value in (("user.email", "t@example.com"), ("user.name", "tester"), ("init.defaultBranch", "main")):
            sh(["git", "config", key, value], self.repo)
        write(self.path("src/app.py"), 'def add(a, b):\n    """Add."""\n    return a + b\n')
        write(self.path("web/src/App.tsx"), "export function App() { return null }\n")
        write(self.path("package-lock.json"), "{}\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-qm", "initial"], self.repo)
        sh(["git", "push", "-q", "origin", "HEAD:main"], self.repo)
        sh(["git", "remote", "set-head", "origin", "main"], self.repo)
        sh(["git", "checkout", "-qb", "feature"], self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, rel):
        return os.path.join(self.repo, rel)

    def scope(self, *args):
        return json.loads(bg(["scope"] + list(args), self.repo).stdout)

    def save(self, level, snapshot, payload, areas="bugs,security", extra=()):
        return bg(["save", "--level", level, "--snapshot", snapshot, "--areas", areas] + list(extra),
                  self.repo, stdin=json.dumps(payload)).stdout

    def state(self):
        folder = self.path(".botgauge/local/state")
        files = os.listdir(folder)
        with open(os.path.join(folder, files[0])) as fh:
            return json.load(fh)


class ScopeTests(RepoCase):
    def test_includes_committed_uncommitted_and_untracked_and_skips_lockfiles(self):
        write(self.path("src/app.py"), 'def add(a, b):\n    """Add."""\n    return a - b\n')
        sh(["git", "commit", "-qam", "change add"], self.repo)
        write(self.path("src/new.py"), "x = 1\n")
        write(self.path("web/src/App.tsx"), "export function App() { return 1 }\n")
        write(self.path("package-lock.json"), '{"a": 1}\n')
        plan = self.scope("--level", "all")
        paths = {f["path"] for f in plan["files"]}
        self.assertEqual(paths, {"src/app.py", "src/new.py", "web/src/App.tsx"})
        self.assertEqual([s["path"] for s in plan["skipped_files"]], ["package-lock.json"])
        self.assertEqual(plan["areas"]["frontend-api"]["files"], ["web/src/App.tsx"])
        self.assertNotIn("web/src/App.tsx", plan["areas"]["performance"]["files"])
        self.assertIn("api", plan["not_applicable"])
        self.assertTrue(plan["uncommitted_changes"])
        # history before this change, with earlier fixes picked out
        self.assertIn("src/app.py", plan["history"])
        self.assertTrue(plan["history"]["src/app.py"]["recent"][0].split(" ", 2)[2] == "initial")
        self.assertEqual(plan["base_ref"], "origin/main")
        # local state must stay out of git
        self.assertNotIn(".botgauge", sh(["git", "status", "--porcelain"], self.repo).stdout)

    def test_history_marks_earlier_fixes(self):
        sh(["git", "checkout", "-q", "main"], self.repo)
        write(self.path("src/app.py"), 'def add(a, b):\n    """Add."""\n    return a + b  # ok\n')
        sh(["git", "commit", "-qam", "Fix overflow bug in add"], self.repo)
        sh(["git", "push", "-q", "origin", "main"], self.repo)
        sh(["git", "checkout", "-q", "-B", "feature2"], self.repo)
        write(self.path("src/app.py"), "def add(a, b):\n    return a - b\n")
        plan = self.scope("--level", "blockers")
        fixes = plan["history"]["src/app.py"]["past_fixes"]
        self.assertEqual(len(fixes), 1)
        self.assertIn("Fix overflow bug in add", fixes[0])

    def test_secret_scan_finds_keys_on_added_lines(self):
        write(self.path("src/app.py"), 'KEY = "sk-ant-abcdefghijklmnopqrstuvwxyz0123"\n')
        plan = self.scope("--level", "blockers")
        self.assertEqual(plan["secret_candidates"], [{"file": "src/app.py", "line": 1, "kind": "Anthropic API key"}])

    def test_reviewed_areas_are_skipped_then_reviewed_incrementally(self):
        write(self.path("src/app.py"), "def add(a, b):\n    return a - b\n")
        write(self.path("src/other.py"), "y = 2\n")
        plan = self.scope("--level", "all")
        self.save("all", plan["snapshot"], {"findings": []},
                  areas=",".join(a for a, p in plan["areas"].items() if p["mode"] != "skip"))
        again = self.scope("--level", "all")
        self.assertTrue(again["nothing_to_review"])
        write(self.path("src/other.py"), "y = 3\n")
        third = self.scope("--level", "all")
        self.assertEqual(third["areas"]["bugs"]["mode"], "incremental")
        self.assertEqual(third["areas"]["bugs"]["files"], ["src/other.py"])
        self.assertTrue(os.path.exists(third["areas"]["bugs"]["diff_file"]))


class FindingsTests(RepoCase):
    def test_ids_dedupe_low_cap_and_dismiss(self):
        snap = bg(["snapshot"], self.repo).stdout.strip()
        lows = [{"area": "quality", "level": "low", "file": "src/app.py", "line": i * 10,
                 "title": "nit number {} about thing{}".format(i, i), "confidence": i / 10.0} for i in range(1, 8)]
        out = self.save("all", snap, {"findings": [
            {"area": "bugs", "level": "blocker", "file": "src/app.py", "line": 3, "title": "add subtracts instead of adding"},
            {"area": "docs", "level": "high", "file": "src/app.py", "line": 2, "title": "docstring lies about the return value"},
            {"area": "bugs", "level": "medium", "pre_existing": True, "file": "src/app.py", "line": 1, "title": "old issue"},
        ] + lows})
        self.assertIn("B1", out)
        self.assertIn("H1", out)
        self.assertIn("P1", out)
        self.assertIn("2 more low findings not shown", out)
        st = self.state()
        self.assertEqual(len([f for f in st["findings"] if f["level"] == "low"]), 5)
        # the same blocker reported again keeps its ID; a dismissed finding is not raised again
        bg(["dismiss", "H1", "intentional", "documented", "elsewhere"], self.repo)
        out = self.save("high", snap, {"findings": [
            {"area": "bugs", "level": "blocker", "file": "src/app.py", "line": 3, "title": "add() subtracts rather than adds"},
            {"area": "docs", "level": "high", "file": "src/app.py", "line": 2, "title": "docstring lies about return value"},
        ]})
        self.assertIn("1 finding you dismissed earlier was not raised again", out)
        st = self.state()
        self.assertEqual(len([f for f in st["findings"] if f["area"] == "bugs" and not f["pre_existing"]]), 1)

    def test_one_line_json_payload_and_automatic_summary_line(self):
        write(self.path("src/app.py"), "def add(a, b):\n    return a - b\n")
        plan = self.scope("--level", "blockers")
        payload = '{"findings": [{"area": "bugs", "level": "blocker", "file": "src/app.py", "line": 2, ' \
                  '"title": "add() doesn\\u0027t add", "problem": "line one\\nline two"}]}'
        out = bg(["save", "--level", "blockers", "--snapshot", plan["snapshot"], "--base-commit",
                  plan["base_commit"], "--areas", "bugs", "--depth", "quick", "--json", payload], self.repo).stdout
        self.assertIn("add() doesn't add", out)
        self.assertIn("Checked 1 file (+1 −2) on feature · bugs · quick depth", out)

    def test_dismiss_needs_a_known_type_and_a_reason(self):
        snap = bg(["snapshot"], self.repo).stdout.strip()
        self.save("all", snap, {"findings": [{"area": "bugs", "level": "high", "file": "a.py", "title": "x"}]})
        self.assertEqual(bg(["dismiss", "H1", "intentional"], self.repo, check=False).returncode, 1)
        self.assertEqual(bg(["dismiss", "H1", "because", "x"], self.repo, check=False).returncode, 1)
        self.assertIn("dismissed", bg(["dismiss", "H1", "wrong", "not", "real"], self.repo).stdout)

    def test_failing_check_is_a_blocker_and_passing_closes_it(self):
        snap = bg(["snapshot"], self.repo).stdout.strip()
        out = self.save("blockers", snap, {"findings": [], "checks": [
            {"name": "tests", "run": "pytest", "ok": False, "exit_code": 1, "output_tail": "1 failed"}]})
        self.assertIn("Check failed: tests", out)
        out = self.save("blockers", snap, {"findings": [], "checks": [{"name": "tests", "run": "pytest", "ok": True}]})
        self.assertIn("nothing to fix", out)

    def test_list_by_level_and_ids(self):
        snap = bg(["snapshot"], self.repo).stdout.strip()
        self.save("all", snap, {"findings": [
            {"area": "bugs", "level": "blocker", "file": "a.py", "title": "one"},
            {"area": "quality", "level": "medium", "file": "b.py", "title": "two"}]})
        data = json.loads(bg(["list", "--level", "blockers"], self.repo).stdout)
        self.assertEqual([f["id"] for f in data["findings"]], ["B1"])
        data = json.loads(bg(["list", "--ids", "M1,Z9"], self.repo).stdout)
        self.assertEqual([f["id"] for f in data["findings"]], ["M1"])
        self.assertEqual(data["unknown_ids"], ["Z9"])


class OrchestrationTests(RepoCase):
    """bg.py review / recheck with a fake `claude` (tests/fake_claude.py), so nothing is spent."""

    def setUp(self):
        super(OrchestrationTests, self).setUp()
        fake = os.path.join(HERE, "fake_claude.py")
        os.chmod(fake, 0o755)
        self.log = os.path.join(self.tmp, "calls.log")
        self.canned = os.path.join(self.tmp, "findings.json")
        self.env = dict(os.environ, BOTGAUGE_CLAUDE=fake, FAKE_LOG=self.log, FAKE_FINDINGS=self.canned)
        write(self.path("src/app.py"), "def add(a, b):\n    return a - b\n")
        write(self.path("web/src/App.tsx"), "export function App() { useEffect(() => { fetch('/a') }); return null }\n")

    def run_bg(self, args):
        return sh([sys.executable, BG] + args, self.repo, env=self.env)

    def calls(self):
        with open(self.log) as fh:
            return [line.strip() for line in fh if line.strip()]

    def test_review_votes_merges_and_saves(self):
        with open(self.canned, "w") as fh:
            json.dump({
                "bugs": [
                    {"area": "bugs", "level": "blocker", "file": "src/app.py", "line": 2, "title": "add subtracts"},
                    {"area": "bugs", "level": "blocker", "file": "src/app.py", "line": 1, "title": "FALSE ALARM here"},
                    {"area": "bugs", "level": "blocker", "file": "web/src/App.tsx", "line": 1,
                     "title": "fetch runs on every render in App", "problem": "effect has no dependency array"},
                ],
                "frontend-api": [
                    {"area": "frontend-api", "level": "blocker", "file": "web/src/App.tsx", "line": 1,
                     "title": "App refetches on every render (DOWNGRADE)", "problem": "the effect has no dependency array so fetch runs every render"},
                ],
                "security": [],
            }, fh)
        proc = self.run_bg(["review", "--level", "blockers", "--depth", "standard"])
        out = proc.stdout
        self.assertIn("B1", out)
        self.assertNotIn("FALSE ALARM", out)
        self.assertIn("Cost: $", out)
        calls = self.calls()
        # three reviewers (bugs, security, frontend-api) and a 3-vote for each distinct blocker candidate
        self.assertEqual(sorted(c for c in calls if c.startswith("reviewer:")),
                         ["reviewer:bugs", "reviewer:frontend-api", "reviewer:security"])
        # add-subtracts, false alarm, merged fetch loop: two agreeing verifiers each, no tie-breaker needed
        self.assertEqual(calls.count("verify"), 6)
        st = self.state()
        titles = sorted(f["title"] for f in st["findings"])
        self.assertEqual(len(titles), 2, titles)  # the duplicate was merged, the false alarm voted out
        self.assertIsNotNone(st["blocker_review"])

    def test_review_takes_user_facing_arguments(self):
        with open(self.canned, "w") as fh:
            json.dump({"security": [], "bugs": [], "frontend-api": [], "quick": []}, fh)
        self.run_bg(["review", "--level", "blockers", "security", "--deep", "src/"])
        self.assertEqual([c for c in self.calls() if c.startswith("reviewer:")], ["reviewer:security"])
        st = self.state()
        self.assertEqual(st["last_review"]["depth"], "deep")
        self.assertEqual(st["last_review"]["areas"], ["security"])

    def test_recheck_resolves_and_reviews_the_fix(self):
        with open(self.canned, "w") as fh:
            json.dump({"bugs": [{"area": "bugs", "level": "blocker", "file": "src/app.py", "line": 2,
                                 "title": "add subtracts"}], "security": [], "frontend-api": [], "quick": []}, fh)
        self.run_bg(["review", "--level", "blockers", "--depth", "standard"])
        before = self.run_bg(["snapshot"]).stdout.strip()
        write(self.path("src/app.py"), "def add(a, b):\n    return a + b\n")
        out = self.run_bg(["recheck", "--from", before, "--ids", "B1"]).stdout
        self.assertIn("Fixed: B1", out)
        self.assertIn("recheck", self.calls())
        self.assertEqual([f["status"] for f in self.state()["findings"]], ["fixed"])

    def test_nothing_new_to_review_spends_nothing(self):
        with open(self.canned, "w") as fh:
            json.dump({"bugs": [], "security": [], "frontend-api": [], "quick": []}, fh)
        self.run_bg(["review", "--level", "blockers"])
        before = len(self.calls())
        out = self.run_bg(["review", "--level", "blockers"]).stdout
        self.assertIn("Nothing new to review", out)
        self.assertEqual(len(self.calls()), before)


class MergeTests(unittest.TestCase):
    """merge_duplicates() directly: findings from different reviewers about one problem become one."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("bg", BG)
        cls.bg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.bg)

    def test_one_problem_reported_by_two_areas_is_one_finding(self):
        merged = self.bg.merge_duplicates([
            {"area": "frontend-api", "level": "high", "file": "web/App.tsx", "line": 4,
             "title": "App refetches on every render",
             "problem": "The effect has no dependency array, so fetch runs on every render."},
            {"area": "bugs", "level": "blocker", "file": "web/App.tsx", "line": 5,
             "title": "fetch runs on every render in App", "problem": "The effect has no dependency array."},
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0]["area"], merged[0]["level"]), ("bugs", "blocker"))
        self.assertIn("Also: App refetches on every render", merged[0]["problem"])

    def test_neighbouring_different_problems_stay_apart(self):
        # Different problems on neighbouring lines, from different areas: none may swallow another.
        merged = self.bg.merge_duplicates([
            {"area": "bugs", "level": "blocker", "file": "src/orders.py", "line": 6,
             "title": "Order totals ignore item quantity",
             "problem": "The total adds up prices only, so any item with a quantity above 1 is undercharged."},
            {"area": "docs", "level": "medium", "file": "src/orders.py", "line": 5,
             "title": "order_total's docstring doesn't mention discount_code",
             "problem": "The new argument and the discounted result aren't described."},
            {"area": "tests", "level": "medium", "file": "src/orders.py", "line": 7,
             "title": "No test covers the discount path", "problem": "Nothing checks that a valid code lowers the total."},
            {"area": "docs", "level": "medium", "file": "src/orders.py", "line": 12,
             "title": "New public function apply_coupon has no docstring",
             "problem": "Callers can't tell what it changes or returns."},
            {"area": "quality", "level": "low", "file": "src/orders.py", "line": 12,
             "title": "code and discount_code name the same thing",
             "problem": "Two names for one idea make the code harder to follow."},
        ])
        self.assertEqual(len(merged), 5)
        self.assertFalse(any("Also:" in (f.get("problem") or "") for f in merged))

    def test_same_problem_elsewhere_in_the_file_keeps_its_location(self):
        merged = self.bg.merge_duplicates([
            {"area": "performance", "level": "high", "file": "api/users.py", "line": 80,
             "title": "N+1 query: one query per user in list_users", "problem": "a"},
            {"area": "bugs", "level": "high", "file": "api/users.py", "line": 20,
             "title": "N+1 query: one query per user in list_users", "problem": "b"},
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["line"], 20)
        self.assertIn("api/users.py:80", merged[0]["other_locations"])

    def test_different_problems_in_one_file_stay_apart(self):
        merged = self.bg.merge_duplicates([
            {"area": "docs", "level": "medium", "file": "a.py", "line": 10, "title": "foo() has no docstring"},
            {"area": "docs", "level": "medium", "file": "a.py", "line": 40, "title": "bar() has no docstring"},
            {"area": "bugs", "level": "high", "file": "a.py", "line": 12, "title": "Off-by-one in the retry loop"},
        ])
        self.assertEqual(len(merged), 3)


class PrePushTests(RepoCase):
    def setUp(self):
        super(PrePushTests, self).setUp()
        write(self.path(".botgauge/config.json"), json.dumps({"pre_push": {"mode": "block", "auto_review": False}}))
        bg(["install-hook"], self.repo)
        write(self.path("src/app.py"), "def add(a, b):\n    return a - b\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-qm", "change"], self.repo)

    def push(self):
        return sh(["git", "push", "origin", "feature"], self.repo, check=False)

    def test_unreviewed_push_is_stopped(self):
        proc = self.push()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not checked for merge blockers yet", proc.stderr)

    def test_open_blocker_stops_push_until_dismissed(self):
        snap = bg(["snapshot"], self.repo).stdout.strip()
        self.save("blockers", snap, {"findings": [
            {"area": "bugs", "level": "blocker", "file": "src/app.py", "line": 2, "title": "add subtracts"}]})
        proc = self.push()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("1 merge blocker open", proc.stderr)
        bg(["dismiss", "B1", "intentional", "test", "fixture"], self.repo)
        self.assertEqual(self.push().returncode, 0)

    def test_change_after_review_needs_review_again(self):
        snap = bg(["snapshot"], self.repo).stdout.strip()
        self.save("blockers", snap, {"findings": []})
        self.assertEqual(self.push().returncode, 0)
        write(self.path("src/app.py"), "def add(a, b):\n    return a * b\n")
        sh(["git", "commit", "-qam", "again"], self.repo)
        proc = self.push()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("src/app.py", proc.stderr)

    def test_warn_mode_and_skip_variable_let_the_push_through(self):
        env = dict(os.environ, BOTGAUGE_SKIP="1")
        self.assertEqual(sh(["git", "push", "origin", "feature"], self.repo, check=False, env=env).returncode, 0)
        write(self.path(".botgauge/config.json"), json.dumps({"pre_push": {"mode": "warn", "auto_review": False}}))
        write(self.path("src/app.py"), "def add(a, b):\n    return 0\n")
        sh(["git", "commit", "-qam", "more"], self.repo)
        proc = self.push()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("warning", proc.stderr)

    def test_existing_hook_is_kept_and_chained(self):
        bg(["install-hook", "--remove"], self.repo)
        hook = self.path(".git/hooks/pre-push")
        write(hook, "#!/bin/sh\necho team-hook-ran >&2\nexit 0\n")
        os.chmod(hook, 0o755)
        result = json.loads(bg(["install-hook"], self.repo).stdout)
        self.assertTrue(result["chained_existing_hook"])
        proc = self.push()
        self.assertIn("team-hook-ran", proc.stderr)


class HookTests(RepoCase):
    def setUp(self):
        super(HookTests, self).setUp()
        write(self.path(".botgauge/config.json"), json.dumps({"on_edit": {"python": ["python3 -m py_compile {file}"]}}))

    def test_post_edit_reports_problems_back_to_claude(self):
        write(self.path("src/bad.py"), "def broken(:\n")
        event = {"tool_name": "Write", "cwd": self.repo, "tool_input": {"file_path": "src/bad.py"}}
        out = bg(["hook-post-edit"], self.repo, stdin=json.dumps(event)).stdout
        result = json.loads(out)
        self.assertEqual(result["decision"], "block")
        self.assertIn("src/bad.py", result["reason"])

    def test_post_edit_is_silent_for_clean_files_and_other_repos(self):
        event = {"tool_name": "Edit", "cwd": self.repo, "tool_input": {"file_path": self.path("src/app.py")}}
        self.assertEqual(bg(["hook-post-edit"], self.repo, stdin=json.dumps(event)).stdout, "")
        other = {"tool_name": "Edit", "cwd": "/", "tool_input": {"file_path": "/etc/hosts"}}
        self.assertEqual(bg(["hook-post-edit"], self.repo, stdin=json.dumps(other)).stdout, "")

    def test_session_start_reminds_only_when_the_check_is_off(self):
        event = json.dumps({"hook_event_name": "SessionStart", "cwd": self.repo})
        out = bg(["hook-session-start"], self.repo, stdin=event).stdout
        self.assertIn("/botgauge:setup", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        bg(["install-hook"], self.repo)
        self.assertEqual(bg(["hook-session-start"], self.repo, stdin=event).stdout, "")


class FindClaudeTests(unittest.TestCase):
    """Where the helper and the push check find Claude Code, including the VS Code extension's own copy."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        cls.scripts = []
        for name, path in (("bg_find", BG), ("pre_push_find", os.path.join(PLUGIN, "templates", "pre-push.py"))):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls.scripts.append(module)
        cls.pre_push = cls.scripts[1]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bg-find-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self.restore_env, dict(os.environ))
        for key in ("BOTGAUGE_CLAUDE", "CLAUDE_CODE_EXECPATH"):
            os.environ.pop(key, None)
        os.environ["HOME"] = os.path.join(self.tmp, "home")  # nothing in the usual install folders
        os.environ["PATH"] = os.path.join(self.tmp, "path")  # nothing on PATH unless a test puts it there

    @staticmethod
    def restore_env(saved):
        os.environ.clear()
        os.environ.update(saved)

    def binary(self, *parts):
        path = os.path.join(self.tmp, *parts)
        write(path, "#!/bin/sh\n")
        os.chmod(path, 0o755)
        return path

    def test_the_running_claude_code_comes_first(self):
        on_path = self.binary("path", "claude")
        running = self.binary("vscode", "native-binary", "claude")
        os.environ["CLAUDE_CODE_EXECPATH"] = running
        for script in self.scripts:
            self.assertEqual(script.find_claude(), running)
        os.environ["CLAUDE_CODE_EXECPATH"] = self.binary("runtime", "node")  # a runtime, not Claude Code
        for script in self.scripts:
            self.assertEqual(script.find_claude(), on_path)

    def test_push_check_finds_the_newest_vscode_extension_copy(self):
        if any(os.path.exists(p) for p in ("/opt/homebrew/bin/claude", "/usr/local/bin/claude")):
            self.skipTest("a system-wide claude is installed, and the push check prefers it")
        tail = ("resources", "native-binary", "claude")
        self.binary("home", ".vscode", "extensions", "anthropic.claude-code-2.1.9-darwin-arm64", *tail)
        newest = self.binary("home", ".vscode", "extensions", "anthropic.claude-code-2.1.318-darwin-arm64", *tail)
        self.assertEqual(self.pre_push.find_claude(), newest)


if __name__ == "__main__":
    unittest.main()
