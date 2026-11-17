"""Connection diagnostics.

Honesty rules (spec §13):
  * Never claim an external address is reachable just because the local
    socket is listening. Local listen state and external reachability are
    reported separately.
  * The public IP lookup can fail (offline / service down) — then we report
    "unknown", not a guess.
  * CGNAT detection is heuristic: public IP inside 100.64.0.0/10, or a
    private/LAN default route with a public IP that doesn't match the
    expected WAN address. We report "likely CGNAT", never "definitely".
  * A real external forwarding test requires a vantage point outside the
    network (a configured peer or relay). Without one we say the test is
    unavailable — we do NOT fake it.
"""
from __future__ import annotations

import ipaddress
import socket
import sys
import time
import urllib.request
from typing import Any


def local_ips() -> list[str]:
    ips: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            addr = info[4][0]
            if not addr.startswith(("127.", "::1", "fe80")):
                ips.add(addr.split("%")[0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))  # TEST-NET-1, no traffic is actually sent
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def public_ip(service: str, timeout: float = 4.0) -> str | None:
    try:
        req = urllib.request.Request(service, headers={"User-Agent": "PenguinTerm/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ip = resp.read(64).decode("utf-8", errors="ignore").strip()
            ipaddress.ip_address(ip)
            return ip
    except Exception:
        return None


def is_cgnat(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def is_private(ip: str | None) -> bool:
    if not ip:
        return True
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True


def listening_on(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host if host != "0.0.0.0" else "127.0.0.1", port),
                                      timeout=timeout):
            return True
    except OSError:
        return False


def detect_mesh_vpn() -> dict[str, Any]:
    """Detect Tailscale / ZeroTier presence (best effort, no requirement)."""
    result = {"tailscale": False, "zerotier": False, "tailscale_ips": [], "zerotier_ips": []}
    try:
        names = [n for _, n in socket.if_nameindex()]
    except OSError:
        names = []
    for name in names:
        lowered = name.lower()
        if lowered.startswith(("tailscale", "ts")) or lowered in ("utun-ts",):
            result["tailscale"] = True
        if lowered.startswith(("zt", "zerotier")):
            result["zerotier"] = True
    # IPs in the Tailscale 100.64.0.0/10 range bound locally also imply Tailscale.
    for ip in local_ips():
        try:
            in_cgnat = ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10")
        except ValueError:
            in_cgnat = False
        if in_cgnat:
            result["tailscale"] = True
            result["tailscale_ips"].append(ip)
    return result


def firewall_status() -> str:
    """Best-effort OS firewall detection."""
    try:
        if sys.platform == "win32":
            import subprocess
            out = subprocess.run(
                ["netsh", "advfirewall", "show", "allprofiles", "state"],
                capture_output=True, timeout=6, creationflags=0x08000000)
            text = out.stdout.decode("utf-8", errors="ignore").lower()
            if "state                                 on" in text or text.count("on") > 0:
                return "Windows Firewall is ON (inbound port must be allowed — see install instructions)"
            return "Windows Firewall appears OFF"
        else:
            import subprocess
            for cmd in (["ufw", "status"], ["firewall-cmd", "--state"]):
                try:
                    out = subprocess.run(cmd, capture_output=True, timeout=4)
                    txt = (out.stdout + out.stderr).decode(errors="ignore").strip()
                    if txt:
                        return f"{cmd[0]}: {txt.splitlines()[0][:120]}"
                except (OSError, subprocess.SubprocessError):
                    continue
            return "No ufw/firewalld detected (or insufficient privileges to query)"
    except Exception:
        return "Unknown"


def collect_diagnostics(cfg, active_connections: int = 0) -> dict[str, Any]:
    service = cfg.get("public_ip_service", "https://api.ipify.org")
    host = cfg.get("host", "0.0.0.0")
    port = int(cfg.get("port", 8443))
    started = time.time()
    pub = public_ip(service)
    mesh = detect_mesh_vpn()
    cgnat = is_cgnat(pub)

    notes: list[str] = []
    if pub is None:
        notes.append("Public IP lookup failed (offline or service unavailable). "
                     "External reachability cannot be determined from this host.")
    elif cgnat:
        notes.append("Public IP is in the CGNAT range (100.64.0.0/10). Inbound port "
                     "forwarding will not work; use Tailscale, ZeroTier or the relay instead.")
    else:
        notes.append("Public IP resolved. Inbound reachability still requires a correct "
                     "port-forward/firewall rule and cannot be verified without an external probe.")
    if mesh["tailscale"]:
        notes.append("Tailscale appears to be active — peers can connect via your 100.x address "
                     "without port forwarding.")
    if mesh["zerotier"]:
        notes.append("ZeroTier appears to be active — peers can connect via your ZeroTier network.")

    return {
        "local_ips": local_ips(),
        "public_ip": pub or "unknown",
        "public_ip_known": pub is not None,
        "host": host,
        "port": port,
        "tls": bool(cfg.get("tls", True)),
        "local_listen_ok": listening_on(host, port),
        "active_connections": active_connections,
        "firewall": firewall_status(),
        "mesh_vpn": mesh,
        "cgnat_likely": cgnat,
        "external_test": "unavailable (no external vantage point configured)",
        "notes": notes,
        "diagnostics_duration_ms": int((time.time() - started) * 1000),
        "platform": sys.platform,
    }
