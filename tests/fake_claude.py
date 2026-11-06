#!/usr/bin/env python3
"""A stand-in for the `claude` CLI, used by test_bg.py to test the review orchestration
without spending anything. Answers like a reviewer or verifier would, from canned data:

  FAKE_FINDINGS   path to a JSON file: {"<area or 'quick'>": [findings...]}
  FAKE_LOG        path to a file where each call's role is appended, one per line

Verifiers say "real", except for findings whose title contains FALSE ALARM. A title with
DOWNGRADE comes back as level "high". Rechecks always say resolved.
"""

import json
import os
import sys


def arg(name):
    argv = sys.argv[1:]
    return argv[argv.index(name) + 1] if name in argv else ""


def finding_from(prompt):
    text = prompt.split("Finding:", 1)[1] if "Finding:" in prompt else "{}"
    text = text.rsplit("Answer with", 1)[0]
    try:
        return json.loads(text)
    except ValueError:
        return {}


def main():
    prompt = arg("-p")
    system = arg("--append-system-prompt")
    if "false-positive" in system:
        role, answer = "security-verifier", {"keep": True, "confidence": 9, "reason": "fake"}
    elif prompt.startswith("Mode: verify"):
        finding = finding_from(prompt)
        title = finding.get("title", "")
        role = "verify"
        if "FALSE ALARM" in title:
            answer = {"verdict": "not-real", "reason": "fake"}
        else:
            answer = {"verdict": "real", "level": "high" if "DOWNGRADE" in title else finding.get("level"),
                      "reason": "fake"}
    elif prompt.startswith("Mode: recheck"):
        role, answer = "recheck", {"resolved": True, "reason": "fake"}
    else:
        first = prompt.splitlines()[0] if prompt else ""
        key = first.split(":", 1)[1].strip() if first.startswith("Area:") else "quick"
        role = "reviewer:" + key
        with open(os.environ["FAKE_FINDINGS"]) as fh:
            canned = json.load(fh)
        answer = {"findings": canned.get(key, []), "resolved": []}
    if os.environ.get("FAKE_LOG"):
        with open(os.environ["FAKE_LOG"], "a") as fh:
            fh.write(role + "\n")
    print(json.dumps({"type": "result", "is_error": False, "total_cost_usd": 0.01,
                      "result": "```json\n" + json.dumps(answer) + "\n```"}))


if __name__ == "__main__":
    main()
