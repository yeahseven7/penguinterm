"""Peer-to-peer transfer between two running instances.

A = source instance (--base-a), B = target instance (--base-b, TLS ok).
B's owner creates an editor account for A; A uploads a file, adds B as a
saved server, pushes the file to B, and we verify B recorded it in its inbox.

Usage:
  python tests/peer_transfer_e2e.py --base-a http://127.0.0.1:8899 \
      --base-b https://127.0.0.1:8898 --owner-b tlsowner --password-b <pw>
"""
from __future__ import annotations

import argparse
import sys

import httpx


def check(label: str, condition: bool) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        raise SystemExit(f"failed at: {label}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-a", default="http://127.0.0.1:8899")
    parser.add_argument("--base-b", default="https://127.0.0.1:8898")
    parser.add_argument("--owner-a", default="smokeowner")
    parser.add_argument("--password-a", default="smoke-test-pass-123")
    parser.add_argument("--owner-b", default="tlsowner")
    parser.add_argument("--password-b", default="tls-test-pass-123")
    args = parser.parse_args()

    with httpx.Client(verify=False, timeout=30) as client:
        # --- log into both instances
        ra = client.post(f"{args.base_a}/api/auth/login",
                         json={"username": args.owner_a, "password": args.password_a})
        check("login A", ra.status_code == 200)
        ha = {"Authorization": f"Bearer {ra.json()['token']}"}

        rb = client.post(f"{args.base_b}/api/auth/login",
                         json={"username": args.owner_b, "password": args.password_b})
        check("login B", rb.status_code == 200)
        hb = {"Authorization": f"Bearer {rb.json()['token']}"}

        # --- B creates an editor account for A's user
        account = "from_instance_a"
        account_password = "instance-a-secret-123"
        rb = client.post(f"{args.base_b}/api/users", headers=hb,
                         json={"username": account, "password": account_password,
                               "role": "editor"})
        check("B creates editor account for A", rb.status_code in (200, 409))

        # --- A uploads a payload (lands in A's inbox, inside allowed roots)
        payload = b"peer-to-peer payload \x00\x01\x02" * 64
        ra = client.post(f"{args.base_a}/api/transfer/upload", headers=ha,
                         files={"file": ("peer-test.bin", payload, "application/octet-stream")})
        check("A upload", ra.status_code == 200)
        source_path = ra.json()["path"]
        print("       A staged:", source_path)

        # --- A saves B as a server
        host_b = httpx.URL(args.base_b).host
        port_b = httpx.URL(args.base_b).port
        tls_b = args.base_b.startswith("https")
        ra = client.post(f"{args.base_a}/api/servers", headers=ha,
                         json={"label": "instance-b", "host": host_b,
                               "port": port_b, "tls": tls_b, "username": account})
        check("A saves B", ra.status_code == 200)
        server_id = ra.json()["id"]

        # --- A probes B (read-only health check)
        ra = client.post(f"{args.base_a}/api/servers/{server_id}/test", headers=ha)
        result = ra.json()
        check("A probes B and B answers as PenguinTerm", result.get("reachable") is True)
        print("       probe latency:", result.get("latency_ms"), "ms |", result.get("platform"))

        # --- A pushes the file to B
        ra = client.post(f"{args.base_a}/api/transfer/send-to-peer", headers=ha,
                         json={"server_id": server_id, "username": account,
                               "password": account_password, "path": source_path})
        if ra.status_code != 200:
            print("send-to-peer failed:", ra.status_code, ra.text[:300])
            raise SystemExit("peer push failed")
        push = ra.json()
        check("A pushed the file to B", bool(push.get("sha256")))
        print("       sha256:", push["sha256"][:16], "…")

        # --- B sees the transfer in its history
        rb = client.get(f"{args.base_b}/api/transfers", headers=hb)
        names = [t["filename"] for t in rb.json()["transfers"]]
        check("B recorded the received file", any(n.startswith("peer-test") for n in names))

        # --- B verifies content integrity
        rb = client.get(f"{args.base_b}/api/transfers", headers=hb)
        transfer = next(t for t in rb.json()["transfers"] if t["filename"].startswith("peer-test"))
        check("sha256 matches across instances", transfer["sha256"] == push["sha256"])

    print("\nPeer-to-peer transfer test passed.")


if __name__ == "__main__":
    main()
