"""Self-signed certificate generation for HTTPS/WSS on first run.

Users can drop their own cert.pem/key.pem into the data dir's tls/ folder
(or the config points elsewhere) to use a real certificate instead.
"""
from __future__ import annotations

import datetime
import ipaddress
from pathlib import Path


def ensure_self_signed(tls_dir: Path, host_hint: str = "127.0.0.1") -> tuple[Path, Path]:
    tls_dir.mkdir(parents=True, exist_ok=True)
    cert = tls_dir / "cert.pem"
    key = tls_dir / "key.pem"
    if cert.exists() and key.exists():
        return cert, key

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    san: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        x509.IPAddress(ipaddress.IPv6Address("::1")),
    ]
    # If the configured host looks like an IP or DNS name, add it too.
    try:
        san.append(x509.IPAddress(ipaddress.ip_address(host_hint)))
    except ValueError:
        if host_hint and host_hint not in ("0.0.0.0", "::"):
            san.append(x509.DNSName(host_hint))

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "PenguinTerm Self-Signed"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PenguinTerm"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert_obj = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )

    key.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    key.chmod(0o600)
    cert.write_bytes(cert_obj.public_bytes(serialization.Encoding.PEM))
    return cert, key
