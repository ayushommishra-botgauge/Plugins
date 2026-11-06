#!/usr/bin/env bash
# Builds "$1/repo" with a branch that changes mean() to divide by n - 1 (wrong result,
# and a ZeroDivisionError for one value).
set -euo pipefail
work="$1"
git init -q --bare "$work/remote.git"
git clone -q "$work/remote.git" "$work/repo" 2>/dev/null
cd "$work/repo"
git config user.email eval@example.com
git config user.name eval
mkdir -p src
cat > src/stats.py <<'EOF'
"""Small statistics helpers."""


def mean(values):
    """Return the arithmetic mean of a non-empty list of numbers."""
    return sum(values) / len(values)
EOF
git add -A
git commit -qm "initial"
git push -q origin HEAD:main 2>/dev/null
git remote set-head origin main >/dev/null 2>&1
git checkout -qb feature/mean
cat > src/stats.py <<'EOF'
"""Small statistics helpers."""


def mean(values):
    """Return the arithmetic mean of a non-empty list of numbers."""
    return sum(values) / (len(values) - 1)
EOF
git commit -qam "Use sample size in mean"
