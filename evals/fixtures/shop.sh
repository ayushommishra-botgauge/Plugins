#!/usr/bin/env bash
# Builds "$1/repo": a clone of a bare "$1/remote.git" with main pushed and a branch
# feature/retries that adds three merge blockers: a retry that can charge twice, a SQL
# injection, and a React effect that fetches in an endless loop.
set -euo pipefail
work="$1"
git init -q --bare "$work/remote.git"
git clone -q "$work/remote.git" "$work/repo" 2>/dev/null
cd "$work/repo"
git config user.email eval@example.com
git config user.name eval
mkdir -p src web/src tests
cat > src/payments.py <<'EOF'
"""Payments helpers."""


def charge(client, amount):
    """Charge the customer once and return the charge id."""
    return client.post("/charges", json={"amount": amount}, timeout=5)["id"]
EOF
cat > src/users.py <<'EOF'
"""User lookups."""


def find_user(conn, name):
    """Return the users with this exact name."""
    return conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchall()
EOF
cat > web/src/Orders.tsx <<'EOF'
import { useEffect, useState } from "react";

export function Orders() {
  const [orders, setOrders] = useState([]);
  useEffect(() => {
    fetch("/api/orders").then((r) => r.json()).then(setOrders);
  }, []);
  return <ul>{orders.map((o: any) => <li key={o.id}>{o.id}</li>)}</ul>;
}
EOF
git add -A
git commit -qm "initial shop"
git push -q origin HEAD:main 2>/dev/null
git remote set-head origin main >/dev/null 2>&1
git checkout -qb feature/retries
cat > src/payments.py <<'EOF'
"""Payments helpers."""
import time


def charge(client, amount, retries=3):
    """Charge the customer once and return the charge id."""
    for _ in range(retries):
        try:
            return client.post("/charges", json={"amount": amount}, timeout=5)["id"]
        except Exception:
            time.sleep(1)
    return None
EOF
cat > src/users.py <<'EOF'
"""User lookups."""


def find_user(conn, name):
    """Return the users with this exact name."""
    return conn.execute("SELECT * FROM users WHERE name = '%s'" % name).fetchall()
EOF
cat > web/src/Orders.tsx <<'EOF'
import { useEffect, useState } from "react";

export function Orders() {
  const [orders, setOrders] = useState([]);
  useEffect(() => {
    fetch("/api/orders").then((r) => r.json()).then(setOrders);
  });
  return <ul>{orders.map((o: any) => <li key={o.id}>{o.id}</li>)}</ul>;
}
EOF
git commit -qam "Retry charges on failure; simplify user lookup; refresh orders"
