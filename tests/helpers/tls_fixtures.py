"""
Self-signed TLS material for the live-socket exposure proofs.

Generated at test time rather than committed: a private key in the repository
is a private key in the repository, even a throwaway one, and committing it
invites exactly the copy-paste-into-production that the exposure rules exist
to prevent.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
from pathlib import Path


def write_self_signed_cert(directory: Path) -> tuple[str, str]:
    """Write a localhost cert/key pair into `directory`. Returns (cert, key)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ],
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_path = directory / "test-cert.pem"
    key_path = directory / "test-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    return str(cert_path), str(key_path)
