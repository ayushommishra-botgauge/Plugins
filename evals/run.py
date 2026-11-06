#!/usr/bin/env python3
"""Measure how well botgauge finds real problems, and what a review costs.

Each case is a repo state with known problems (or none). For each case the runner builds or
clones the repo, runs a botgauge review command through Claude Code in headless mode, reads
the findings botgauge saved, and scores them against what the case expects:

  caught       expected problems that a finding matched
  missed       expected problems no finding matched
  unexpected   merge-blocker or high findings that match nothing expected. These are
               possible false alarms; read them, because some may be real.

With --baseline it also runs Claude Code's built-in /code-review on the same state and
scores its text output the same way (by file name and keywords), for comparison.

This spends Claude usage: roughly $0.50-2 per case per run in API terms, more with
--baseline. Results are written to evals/results/ (not committed).

Usage:
  python3 evals/run.py                          all cases, once
  python3 evals/run.py --case shop --runs 3     one case, three runs (to see the variance)
  python3 evals/run.py --baseline               also run /code-review for comparison
  python3 evals/run.py --keep                   keep the temporary repos for inspection

Case files live in evals/cases/*.json; see evals/README.md for the format.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "plugins", "botgauge")
DEVNULL = subprocess.DEVNULL


def load_cases(names):
    cases = []
    for path in sorted(glob.glob(os.path.join(HERE, "cases", "*.json"))):
        case_id = os.path.splitext(os.path.basename(path))[0]
        if names and case_id not in names:
            continue
        with open(path) as fh:
            case = json.load(fh)
        case["id"] = case_id
        cases.append(case)
    return cases


def prepare(case, workdir):
    """Build or clone the case's repo. Returns (repo path, extra review arguments, base..commit range)."""
    repo = os.path.join(workdir, "repo")
    if case.get("fixture"):
        subprocess.run(["bash", os.path.join(HERE, case["fixture"]), workdir], check=True, stdout=DEVNULL)
        extra, rev_range = [], None
    else:
        src = case["repo"]
        subprocess.run(["git", "clone", "-q", os.path.expanduser(src["path"]), repo], check=True)
        subprocess.run(["git", "-C", repo, "checkout", "-q", src["commit"]], check=True)
        subprocess.run(["git", "-C", repo, "remote", "remove", "origin"], check=False)
        extra = ["--commit", src["commit"], "--base", src["base"]]
        rev_range = "{}..{}".format(src["base"], src["commit"])
    if case.get("config"):
        os.makedirs(os.path.join(repo, ".botgauge"), exist_ok=True)
        with open(os.path.join(repo, ".botgauge", "config.json"), "w") as fh:
            json.dump(case["config"], fh)
    return repo, extra, rev_range


def run_claude(prompt, cwd, plugin_dir=None, timeout=1800):
    cmd = ["claude", "-p", prompt, "--permission-mode", "dontAsk", "--output-format", "json"]
    if plugin_dir:
        cmd += ["--plugin-dir", plugin_dir]
    start = time.time()
    try:
        proc = subprocess.run(cmd, cwd=cwd, stdin=DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out", "seconds": round(time.time() - start)}
    raw = proc.stdout
    data = {}
    if "{" in raw:
        try:
            data = json.loads(raw[raw.index("{"):])
        except ValueError:
            pass
    return {"ok": proc.returncode == 0 and not data.get("is_error"), "cost": data.get("total_cost_usd"),
            "seconds": round(time.time() - start), "result": data.get("result", ""),
            "permission_denials": len(data.get("permission_denials") or []), "stderr": proc.stderr[-500:]}


def helper_cost(repo):
    """What the reviewer and verifier processes cost: bg.py logs it after every review."""
    total, calls = 0.0, 0
    path = os.path.join(repo, ".botgauge", "local", "log.jsonl")
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event") == "review_cost":
                    total += float(event.get("cost") or 0)
                    calls += int(event.get("calls") or 0)
    return total, calls


def saved_findings(repo):
    found = []
    for path in glob.glob(os.path.join(repo, ".botgauge", "local", "state", "*.json")):
        with open(path) as fh:
            state = json.load(fh)
        found += [f for f in state.get("findings", []) if f.get("status") == "open"]
    return found


def finding_text(f):
    return " ".join(str(f.get(k, "")) for k in ("title", "problem", "trigger", "fix", "category")).lower()


def matches(expect, finding):
    if finding.get("file") != expect["file"]:
        return False
    lines = expect.get("lines")
    if lines and finding.get("line"):
        try:
            if not lines[0] <= int(finding["line"]) <= lines[1]:
                return False
        except (TypeError, ValueError):
            pass
    if expect.get("levels") and finding.get("level") not in expect["levels"]:
        return False
    words = expect.get("any_of")
    return not words or any(w.lower() in finding_text(finding) for w in words)


def score(case, findings):
    expects = case.get("expect", [])
    used = set()
    caught, missed = [], []
    for expect in expects:
        hits = [i for i, f in enumerate(findings) if matches(expect, f)]
        used.update(hits)
        (caught if hits else missed).append(expect.get("label") or expect["file"])
    unexpected = ["{} {}:{} {}".format(f.get("id"), f.get("file"), f.get("line", ""), f.get("title"))
                  for i, f in enumerate(findings)
                  if i not in used and not f.get("pre_existing") and f.get("level") in ("blocker", "high")]
    return {"caught": caught, "missed": missed, "unexpected": unexpected}


def score_text(case, text):
    """Rough scoring for /code-review's text output: file name plus one of the keywords."""
    text = (text or "").lower()
    caught, missed = [], []
    for expect in case.get("expect", []):
        name = os.path.basename(expect["file"]).lower()
        words = expect.get("any_of") or [""]
        ok = name in text and any(w.lower() in text for w in words)
        (caught if ok else missed).append(expect.get("label") or expect["file"])
    return {"caught": caught, "missed": missed, "unexpected": []}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", action="append", help="case id (file name without .json); repeatable")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--baseline", action="store_true", help="also run the built-in /code-review")
    parser.add_argument("--plugin-dir", default=PLUGIN)
    parser.add_argument("--keep", action="store_true", help="keep the temporary repos")
    args = parser.parse_args()

    if not shutil.which("claude"):
        sys.exit("claude isn't on PATH")
    cases = load_cases(args.case)
    if not cases:
        sys.exit("no matching cases in evals/cases/")

    rows = []
    for case in cases:
        for run in range(1, args.runs + 1):
            work = tempfile.mkdtemp(prefix="botgauge-eval-")
            try:
                repo, extra, rev_range = prepare(case, work)
                prompt = "/botgauge:{} {}".format(case.get("command", "review-blockers"), " ".join(extra)).strip()
                print("· {} (run {}): {}".format(case["id"], run, prompt), flush=True)
                result = run_claude(prompt, repo, args.plugin_dir)
                extra_cost, extra_calls = helper_cost(repo)
                main_cost = result.get("cost")
                row = {"case": case["id"], "run": run, "tool": "botgauge",
                       "cost": None if main_cost is None else main_cost + extra_cost,
                       "orchestration_cost": main_cost, "reviewer_cost": extra_cost, "reviewer_calls": extra_calls,
                       "seconds": result.get("seconds"), "ok": result.get("ok"),
                       "permission_denials": result.get("permission_denials")}
                row.update(score(case, saved_findings(repo)))
                # A review that never ran must not pass as "nothing found".
                row["ran"] = extra_calls > 0 or "Nothing new to review" in (result.get("result") or "")
                rows.append(row)
                if args.baseline:
                    base_work = tempfile.mkdtemp(prefix="botgauge-eval-base-")
                    base_repo, _, _ = prepare(case, base_work)
                    target = " " + rev_range if rev_range else ""
                    base = run_claude("/code-review high" + target, base_repo)
                    brow = {"case": case["id"], "run": run, "tool": "/code-review", "cost": base.get("cost"),
                            "seconds": base.get("seconds"), "ok": base.get("ok")}
                    brow.update(score_text(case, base.get("result")))
                    rows.append(brow)
                    if not args.keep:
                        shutil.rmtree(base_work, ignore_errors=True)
            finally:
                if args.keep:
                    print("  kept:", work)
                else:
                    shutil.rmtree(work, ignore_errors=True)

    print()
    print("{:<22} {:>3} {:<13} {:>7} {:>8} {:>6}  {}".format("case", "run", "tool", "caught", "cost $", "secs",
                                                              "unexpected / missed"))
    for r in rows:
        total = len(r["caught"]) + len(r["missed"])
        notes = []
        if r["missed"]:
            notes.append("missed: " + ", ".join(r["missed"]))
        if r["unexpected"]:
            notes.append("unexpected: " + "; ".join(r["unexpected"]))
        if not r.get("ok"):
            notes.append("RUN FAILED")
        if r["tool"] == "botgauge" and not r.get("ran", True):
            notes.append("REVIEW DID NOT RUN")
        if r.get("permission_denials"):
            notes.append("{} permission denials".format(r["permission_denials"]))
        print("{:<22} {:>3} {:<13} {:>7} {:>8} {:>6}  {}".format(
            r["case"][:22], r["run"], r["tool"], "{}/{}".format(len(r["caught"]), total),
            "{:.2f}".format(r["cost"]) if r.get("cost") is not None else "-", r.get("seconds") or "-",
            " · ".join(notes)))
    for tool in sorted({r["tool"] for r in rows}):
        mine = [r for r in rows if r["tool"] == tool]
        expected = sum(len(r["caught"]) + len(r["missed"]) for r in mine)
        caught = sum(len(r["caught"]) for r in mine)
        cost = sum(r["cost"] or 0 for r in mine)
        unexpected = sum(len(r["unexpected"]) for r in mine)
        not_run = sum(1 for r in mine if not r.get("ran", True) or not r.get("ok"))
        print("{}: caught {}/{} ({:.0f}%), {} unexpected, {} run{} failed, ${:.2f} total, ${:.2f} per run".format(
            tool, caught, expected, 100.0 * caught / expected if expected else 100.0, unexpected, not_run,
            "" if not_run == 1 else "s", cost, cost / len(mine) if mine else 0))

    out_dir = os.path.join(HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    with open(out, "w") as fh:
        json.dump(rows, fh, indent=1)
    print("results:", os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()
