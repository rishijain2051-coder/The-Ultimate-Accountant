"""The office server's own certificate, and clients pinning it (spike N1, PLAN-office.md 3.2).

The office server makes a self-signed certificate for the names and office IP addresses it
answers to. Nothing trusts it but the clients that pinned its SHA-256 when they were set up,
after checking its 24 digits against the office server's screen.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import socket
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.office import settings


def digits(sha256_hex: str) -> str:
    """A certificate's SHA-256 as 24 digits in four groups of six (a device's fingerprint has 12)."""
    d = f"{int(sha256_hex[:32], 16) % 10**24:024d}"
    return " ".join(d[i:i + 6] for i in range(0, 24, 6))


def der_sha256(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def tls_paths() -> tuple[Path, Path]:
    d = settings.office_dir() / "tls"
    d.mkdir(exist_ok=True)
    return d / "office-server.pem", d / "office-server.key"


def local_addresses() -> list[str]:
    found = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    return sorted(found)


def make_certificate() -> str:
    """A new certificate for this PC's names and addresses; its SHA-256 (hex)."""
    host = socket.gethostname().lower()
    names = [x509.DNSName(n) for n in dict.fromkeys(["localhost", host, f"{host}.lan", f"{host}.local"])]
    names += [x509.IPAddress(ipaddress.ip_address(a)) for a in local_addresses()]
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ultimate Accountant office server")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tls_paths()
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return der_sha256(cert.public_bytes(serialization.Encoding.DER))


def own_certificate() -> str | None:
    """SHA-256 (hex) of this office server's certificate, if it has one."""
    cert_path, _ = tls_paths()
    if not cert_path.exists():
        return None
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return der_sha256(cert.public_bytes(serialization.Encoding.DER))


def remote_certificate(host: str, port: int = settings.PORT) -> str:
    """SHA-256 (hex) of the certificate the office server at host:port presents."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # nothing to verify against: we pin it instead
    with socket.create_connection((host, port), timeout=10) as raw, ctx.wrap_socket(raw, server_hostname=host) as s:
        return der_sha256(s.getpeercert(binary_form=True))


def matches(der: bytes, pinned_hex: str) -> bool:
    return bool(pinned_hex) and der_sha256(der) == pinned_hex
