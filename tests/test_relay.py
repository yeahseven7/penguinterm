"""Relay rendezvous + frame-forwarding test (no PenguinTerm server needed).

Starts a relay on an ephemeral port, connects a HOST and a GUEST sharing a
code, then asserts bidirectional forwarding works and pairing completes.
"""
import asyncio
import json
import sys

import websockets

sys.path.insert(0, ".")
from backend.networking import relay as relay_module


async def main():
    state = relay_module.RelayState()

    async def handler(ws):
        await relay_module.relay_handler(ws, state)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    code = relay_module.new_rendezvous_code()
    print(f"relay on :{port}, rendezvous code {code}")

    async def host():
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"role": "host", "code": code}))
            msg = json.loads(await ws.recv())
            assert msg["type"] == "waiting", msg
            msg = json.loads(await ws.recv())  # paired
            assert msg["type"] == "paired", msg
            await ws.send(b"hello-from-host")
            reply = await ws.recv()
            print("host received:", reply)
            assert reply == b"hello-from-guest"

    async def guest():
        await asyncio.sleep(0.3)  # let host register first
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"role": "guest", "code": code}))
            msg = json.loads(await ws.recv())  # paired
            assert msg["type"] == "paired", msg
            data = await ws.recv()
            print("guest received:", data)
            assert data == b"hello-from-host"
            await ws.send(b"hello-from-guest")
            await asyncio.sleep(0.5)

    await asyncio.gather(host(), guest())
    server.close()
    await server.wait_closed()
    print("relay test OK")


if __name__ == "__main__":
    asyncio.run(main())
