"""Shared-session permission model, end-to-end.

Verifies against a running PenguinTerm instance:
  * owner creates a shared session; editor and viewer accounts created via API
  * viewer (and editor) attach; both receive the same shell output
  * viewer input is REFUSED (can_input=False)
  * editor input is ACCEPTED in input_mode='all', REFUSED after owner locks to 'owner'
  * viewer's attempt to create a local session is refused (remote_create_sessions off)
  * viewer does not see the owner's private local session in the list

Usage:  python tests/shared_session_e2e.py [base_url]
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx
import websockets

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899"
WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")
PASSWORD = "shared-test-pass-123"


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        raise SystemExit(f"test failed at: {label}")


async def ws_collect(token: str, sid: str, out: list, ready: asyncio.Event,
                     send: asyncio.Queue | None):
    uri = f"{WS_BASE}/ws/term/{sid}?token={token}"
    async with websockets.connect(uri, max_size=8 * 1024 * 1024) as ws:
        ready.set()
        sender = None

        async def pump_send():
            while True:
                item = await send.get()
                if item is None:
                    return
                await ws.send(item)

        if send is not None:
            sender = asyncio.create_task(pump_send())
        try:
            while True:
                message = await ws.recv()
                if isinstance(message, bytes):
                    out.append(message)
                else:
                    data = json.loads(message)
                    if data.get("type") == "exit":
                        return
        except Exception:
            return
        finally:
            if sender:
                sender.cancel()


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, verify=False, timeout=15) as client:
        r = await client.post("/api/auth/login",
                              json={"username": "smokeowner", "password": "smoke-test-pass-123"})
        check("owner login", r.status_code == 200)
        owner_token = r.json()["token"]
        oh = {"Authorization": f"Bearer {owner_token}"}

        # create editor + viewer accounts (idempotent-ish)
        for username, role in (("mp_editor", "editor"), ("mp_viewer", "viewer")):
            r = await client.post("/api/users", headers=oh,
                                  json={"username": username, "password": PASSWORD, "role": role})
            check(f"create {role} account", r.status_code in (200, 409))

        r = await client.post("/api/auth/login", json={"username": "mp_editor", "password": PASSWORD})
        check("editor login", r.status_code == 200)
        editor_token = r.json()["token"]
        eh = {"Authorization": f"Bearer {editor_token}"}

        r = await client.post("/api/auth/login", json={"username": "mp_viewer", "password": PASSWORD})
        check("viewer login", r.status_code == 200)
        viewer_token = r.json()["token"]
        vh = {"Authorization": f"Bearer {viewer_token}"}

        # owner creates shared session
        r = await client.post("/api/sessions", headers=oh,
                              json={"kind": "shared", "title": "mp-test", "input_mode": "all"})
        check("create shared session", r.status_code == 200)
        sid = r.json()["id"]

        # viewer cannot create a local session (remote_create_sessions default off)
        r = await client.post("/api/sessions", headers=vh, json={"kind": "local"})
        check("viewer cannot open local session on host", r.status_code == 403)

        # owner opens private local session; viewer must not see it
        r = await client.post("/api/sessions", headers=oh, json={"kind": "local", "title": "owner-private"})
        check("owner private session created", r.status_code == 200)
        private_sid = r.json()["id"]

        r = await client.get("/api/sessions", headers=vh)
        ids = [s["id"] for s in r.json()["sessions"]]
        check("viewer cannot see owner's private session", private_sid not in ids)
        check("viewer can see the shared session", sid in ids)

        # attach editor + viewer to the shared session
        editor_out, viewer_out = [], []
        e_ready, v_ready = asyncio.Event(), asyncio.Event()
        editor_send, viewer_send = asyncio.Queue(), asyncio.Queue()
        e_task = asyncio.create_task(ws_collect(editor_token, sid, editor_out, e_ready, editor_send))
        v_task = asyncio.create_task(ws_collect(viewer_token, sid, viewer_out, v_ready, viewer_send))
        await asyncio.gather(e_ready.wait(), v_ready.wait())
        await asyncio.sleep(1.5)  # let meta/replay messages flow

        marker = f"PT-MP-{int(time.time())}"
        # editor can type; viewer cannot
        await viewer_send.put(f"echo {marker}-viewer\r\n".encode())
        await asyncio.sleep(1.0)
        await editor_send.put(f"echo {marker}-editor\r\n".encode())
        await asyncio.sleep(2.5)

        editor_text = b"".join(editor_out).decode(errors="ignore")
        viewer_text = b"".join(viewer_out).decode(errors="ignore")
        check("editor output reached the shared terminal", f"{marker}-editor" in editor_text)
        check("viewer received the same shared output", f"{marker}-editor" in viewer_text)
        check("viewer input was ignored", f"{marker}-viewer" not in viewer_text)

        # owner locks input to owner; editor input must now be refused
        r = await client.patch(f"/api/sessions/{sid}", headers=oh, json={"input_mode": "owner"})
        check("owner locks shared input", r.status_code == 200)
        lock_marker = f"PT-LOCK-{int(time.time())}"
        await editor_send.put(f"echo {lock_marker}\r\n".encode())
        await asyncio.sleep(2.0)
        editor_text2 = b"".join(editor_out).decode(errors="ignore")
        check("editor input refused while locked", lock_marker not in editor_text2)

        # cleanup
        await editor_send.put(None)
        await viewer_send.put(None)
        await asyncio.sleep(0.3)
        e_task.cancel(); v_task.cancel()
        for cancel in (e_task, v_task):
            try:
                await cancel
            except (asyncio.CancelledError, Exception):
                pass
        await client.delete(f"/api/sessions/{sid}", headers=oh)
        await client.delete(f"/api/sessions/{private_sid}", headers=oh)

    print("\nAll multiplayer checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
