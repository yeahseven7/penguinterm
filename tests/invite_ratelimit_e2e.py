"""Invite registration + login rate limiting, end-to-end."""
from __future__ import annotations

import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899"
RUN = str(int(time.time() * 1000))[-7:]


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        raise SystemExit(f"failed at: {label}")


with httpx.Client(base_url=BASE, verify=False, timeout=15) as client:
    r = client.post("/api/auth/login",
                    json={"username": "smokeowner", "password": "smoke-test-pass-123"})
    check("owner login", r.status_code == 200)
    h = {"Authorization": f"Bearer {r.json()['token']}"}

    r = client.post("/api/invites", headers=h,
                    json={"role": "editor", "max_uses": 1, "expires_hours": 1})
    check("create invitation", r.status_code == 200)
    invite = r.json()
    check("invitation carries role=editor", invite["role"] == "editor")
    check("invitation has a URL", invite["url"].startswith("http") and "invite=" in invite["url"])
    print("       invite url:", invite["url"])

    r = client.post("/api/auth/register-with-invite", json={
        "invite_token": invite["token"],
        "username": f"friend_{RUN}",
        "password": "friend-pass-12345",
    })
    check("invited user registers", r.status_code == 200)
    friend = r.json()
    check("invited role is editor", friend["user"]["role"] == "editor")

    # single-use: token must now be exhausted
    r = client.post("/api/auth/register-with-invite", json={
        "invite_token": invite["token"],
        "username": "second_user",
        "password": "second-pass-12345",
    })
    check("single-use invite cannot be reused", r.status_code == 403)

    r = client.post("/api/auth/login",
                    json={"username": f"friend_{RUN}", "password": "friend-pass-12345"})
    check("invited user can log in", r.status_code == 200)
    fh = {"Authorization": f"Bearer {r.json()['token']}"}

    r = client.get("/api/diagnostics", headers=fh)
    check("invite-registered user cannot read owner-only diagnostics", r.status_code == 403)

    r = client.post("/api/sessions", headers=fh, json={"kind": "local"})
    check("invited editor cannot spawn local shell on host", r.status_code == 403)

    # rate limiting: 10 bad logins allowed, 11th rejected
    for i in range(1, 12):
        r = client.post("/api/auth/login",
                        json={"username": "ratelimit-probe", "password": "wrong-password-x"})
        if i <= 10:
            check(f"bad login attempt {i} accepted as 401", r.status_code == 401)
        else:
            check("11th bad login is rate-limited (429)", r.status_code == 429)

print("\nInvite & rate-limit checks passed.")
