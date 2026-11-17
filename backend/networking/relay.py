"""Optional, self-hostable rendezvous relay.

Forwards opaque WebSocket frames between a HOST and a GUEST that share a
rendezvous code. The relay:

  * never executes commands,
  * never stores files or terminal output,
  * does not understand the PenguinTerm protocol — frames are payload-agnostic.

Trust model (documented in README): a relay operator can observe traffic
metadata, and unless the two endpoints encrypt end-to-end on top, the relay
can also see terminal content. Run your own relay over WSS, or prefer
Tailscale/ZeroTier for CGNAT traversal. The relay is never required.

Run standalone:
    python -m backend.networking.relay --host 0.0.0.0 --port 9443
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from typing import Any

RELAY_VERSION = 1
CODE_TTL_SECONDS = 600


def new_rendezvous_code() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))


class RelayState:
    def __init__(self) -> None:
        self.waiting: dict[str, asyncio.Future] = {}  # code -> future[guest websocket]


async def _pipe(src, dst) -> None:
    try:
        async for message in src:
            await dst.send(message)
    except Exception:
        pass
    finally:
        try:
            await dst.close()
        except Exception:
            pass


async def _pair(host, guest) -> None:
    await host.send(json.dumps({"type": "paired"}))
    await guest.send(json.dumps({"type": "paired"}))
    await asyncio.gather(_pipe(host, guest), _pipe(guest, host))


async def relay_handler(ws, state: RelayState) -> None:
    code = ""
    stored = False
    try:
        hello = await asyncio.wait_for(ws.recv(), timeout=15)
        msg = json.loads(hello)
        role = msg.get("role")
        code = str(msg.get("code") or "").upper().strip()
        if role not in ("host", "guest") or not code:
            await ws.send(json.dumps({"type": "error", "detail": "expected {role, code}"}))
            await ws.close()
            return

        if role == "host":
            if code in state.waiting:
                await ws.send(json.dumps({"type": "error", "detail": "code already in use"}))
                await ws.close()
                return
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            state.waiting[code] = future
            stored = True
            await ws.send(json.dumps({"type": "waiting", "code": code,
                                      "expires_in": CODE_TTL_SECONDS}))
            try:
                guest = await asyncio.wait_for(asyncio.shield(future),
                                               timeout=CODE_TTL_SECONDS)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                await ws.send(json.dumps({"type": "error", "detail": "rendezvous expired"}))
                await ws.close()
                return
            finally:
                state.waiting.pop(code, None)
                stored = False
            try:
                await _pair(ws, guest)
            finally:
                done = getattr(guest, "relay_done", None)
                if done is not None and not done.done():
                    done.set_result(None)

        else:  # guest
            future = state.waiting.pop(code, None)
            if future is None or future.done():
                await ws.send(json.dumps({"type": "error",
                                          "detail": "no host waiting for that code"}))
                await ws.close()
                return
            loop = asyncio.get_running_loop()
            done: asyncio.Future = loop.create_future()
            ws.relay_done = done  # type: ignore[attr-defined]
            future.set_result(ws)
            # The host side owns the pipes; wait until it signals completion.
            try:
                await done
            except asyncio.CancelledError:
                pass
    except asyncio.TimeoutError:
        try:
            await ws.send(json.dumps({"type": "error", "detail": "handshake timeout"}))
            await ws.close()
        except Exception:
            pass
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass
    finally:
        if stored and code:
            state.waiting.pop(code, None)


async def serve(host: str, port: int) -> None:
    import websockets

    state = RelayState()

    async def handler(ws):
        await relay_handler(ws, state)

    async with websockets.serve(handler, host, port, max_size=2 * 1024 * 1024):
        print(f"PenguinTerm relay v{RELAY_VERSION} listening on ws://{host}:{port}", flush=True)
        print(f"Rendezvous codes expire after {CODE_TTL_SECONDS} seconds.", flush=True)
        print("This relay forwards frames only; it cannot execute commands or store files.",
              flush=True)
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(description="PenguinTerm optional relay")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9443)
    args = parser.parse_args()
    asyncio.run(serve(args.host, args.port))


if __name__ == "__main__":
    main()
