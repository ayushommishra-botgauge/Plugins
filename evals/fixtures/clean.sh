#!/usr/bin/env bash
# Builds "$1/repo" with a branch that adds a correct, documented and tested helper.
# A good review finds no merge blockers here: anything it reports is a false alarm.
set -euo pipefail
work="$1"
git init -q --bare "$work/remote.git"
git clone -q "$work/remote.git" "$work/repo" 2>/dev/null
cd "$work/repo"
git config user.email eval@example.com
git config user.name eval
mkdir -p src tests
cat > src/text.py <<'EOF'
"""Text helpers."""


def slugify(text):
    """Return a lowercase, dash-separated version of ``text`` for use in URLs."""
    return "-".join(text.lower().split())
EOF
git add -A
git commit -qm "initial"
git push -q origin HEAD:main 2>/dev/null
git remote set-head origin main >/dev/null 2>&1
git checkout -qb feature/truncate
cat > src/text.py <<'EOF'
"""Text helpers."""


def slugify(text):
    """Return a lowercase, dash-separated version of ``text`` for use in URLs."""
    return "-".join(text.lower().split())


def truncate(text, limit, suffix="…"):
    """Shorten ``text`` to at most ``limit`` characters, ending with ``suffix`` when cut.

    Args:
        text: The text to shorten.
        limit: The maximum length of the result, including the suffix. Must be at least
            the length of ``suffix``.
        suffix: What to put at the end of shortened text.

    Returns:
        ``text`` unchanged when it fits, otherwise its first characters followed by ``suffix``.

    Raises:
        ValueError: If ``limit`` is shorter than ``suffix``.
    """
    if limit < len(suffix):
        raise ValueError("limit must be at least len(suffix)")
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)] + suffix
EOF
cat > tests/test_text.py <<'EOF'
import pytest

from src.text import truncate


def test_short_text_is_unchanged():
    assert truncate("abc", 5) == "abc"


def test_long_text_is_cut_with_suffix():
    assert truncate("abcdefgh", 5) == "abcd…"


def test_exact_length_is_unchanged():
    assert truncate("abcde", 5) == "abcde"


def test_limit_shorter_than_suffix_raises():
    with pytest.raises(ValueError):
        truncate("abc", 0)
EOF
git add -A
git commit -qm "Add truncate() for long titles"
