"""Utility: close all terminal sessions on a PenguinTerm instance (owner only)."""
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899"
c = httpx.Client(base_url=BASE, timeout=10)
r = c.post("/api/auth/login", json={"username": "smokeowner", "password": "smoke-test-pass-123"})
r.raise_for_status()
h = {"Authorization": "Bearer " + r.json()["token"]}
for s in c.get("/api/sessions", headers=h).json()["sessions"]:
    print("closing", s["title"], s["id"], c.delete(f"/api/sessions/{s['id']}", headers=h).status_code)
print("done")
