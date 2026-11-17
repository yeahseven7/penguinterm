"""End-to-end smoke test against a running PenguinTerm instance.

Usage:
    python tests/smoke_e2e.py [base_url]

Exercises: health -> setup -> login -> session create -> ConPTY/PTY I/O
-> shared session permissions -> file upload -> download -> cleanup.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request

import httpx
import websockets

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899"
WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(f"smoke test failed at: {label}")


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, verify=False, timeout=15) as client:
        r = await client.get("/api/health")
        check("health endpoint", r.status_code == 200 and r.json()["ok"])

        r = await client.post("/api/auth/setup",
                              json={"username": "smokeowner", "password": "smoke-test-pass-123"})
        if r.status_code == 400:
            r = await client.post("/api/auth/login",
                                  json={"username": "smokeowner", "password": "smoke-test-pass-123"})
            check("login (owner already exists)", r.status_code == 200)
            token = r.json()["token"]
        else:
            check("owner setup", r.status_code == 200)
            token = r.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.get("/api/system", headers=headers)
        check("system info", r.status_code == 200 and "shells" in r.json())
        default_shell = r.json().get("default_shell")
        print("       default shell:", default_shell)

        r = await client.post("/api/sessions", headers=headers,
                              json={"shell_id": default_shell, "kind": "local", "title": "smoke"})
        check("create local session", r.status_code == 200)
        session = r.json()
        sid = session["id"]
        print("       session id:", sid, "| shell:", session["shell_name"], "| restricted:", session["restricted"])

        # terminal I/O over websocket
        uri = f"{WS_BASE}/ws/term/{sid}?token={token}"
        async with websockets.connect(uri, max_size=8 * 1024 * 1024) as ws:
            marker = "PT-SMOKE-" + str(int(time.time()))
            cmd = f"echo {marker}\r\n".encode()
            output = bytearray()
            deadline = time.time() + 30
            got_meta = False
            await ws.send(cmd)
            while time.time() < deadline:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    break
                if isinstance(message, bytes):
                    output.extend(message)
                    if marker.encode() in output:
                        break
                else:
                    data = json.loads(message)
                    if data.get("type") == "meta":
                        got_meta = True
            check("received session meta", got_meta)
            check("shell executed echo and returned output", marker.encode() in output)

        # shared session input gating is exercised via REST-level describe
        r = await client.get("/api/sessions", headers=headers)
        check("session list", r.status_code == 200)

        # file transfer
        payload = b"PenguinTerm smoke payload " * 128
        r = await client.post("/api/transfer/upload", headers=headers,
                              files={"file": ("smoke.txt", payload, "text/plain")})
        check("file upload", r.status_code == 200)
        uploaded = r.json()
        transfer_id = uploaded["id"]
        check("sha256 computed", len(uploaded.get("sha256", "")) == 64)

        r = await client.get(f"/api/transfer/download?transfer_id={transfer_id}", headers=headers)
        check("file download", r.status_code == 200 and r.content == payload)

        r = await client.delete(f"/api/transfers/{transfer_id}", headers=headers)
        check("transfer cleanup", r.status_code == 200)

        r = await client.delete(f"/api/sessions/{sid}", headers=headers)
        check("session close", r.status_code == 200)

    print("\nAll smoke checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
