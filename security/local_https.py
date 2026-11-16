"""Local TLS material for Safari (HTTPS-Only requires HTTPS on 127.0.0.1)."""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import ssl
import subprocess
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from settings import app_data_dir

logger = logging.getLogger(__name__)

CERT_FILENAME = "localhost.crt"
KEY_FILENAME = "localhost.key"
CERT_VERSION = 4

_MKCERT_HOSTS = ("127.0.0.1", "localhost")


def localhost_cert_paths() -> tuple[Path, Path]:
    data_dir = app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / CERT_FILENAME, data_dir / KEY_FILENAME


def _cert_is_current(cert_path: Path, key_path: Path, version_path: Path) -> bool:
    if not cert_path.exists() or not key_path.exists():
        return False
    try:
        return int(version_path.read_text().strip()) == CERT_VERSION
    except (OSError, ValueError):
        return False


def _generate_with_mkcert(cert_path: Path, key_path: Path) -> bool:
    mkcert = Path(subprocess.run(["which", "mkcert"], capture_output=True, text=True).stdout.strip())
    if not mkcert.is_file():
        return False
    result = subprocess.run(
        [
            str(mkcert),
            "-key-file",
            str(key_path),
            "-cert-file",
            str(cert_path),
            *_MKCERT_HOSTS,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("mkcert failed: %s", result.stderr.strip())
        return False
    key_path.chmod(0o600)
    cert_path.chmod(0o600)
    return True


def _generate_self_signed(cert_path: Path, key_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Retirement Planner"),
        ]
    )
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert_path.chmod(0o600)


def ensure_localhost_cert() -> tuple[Path, Path]:
    """Create or reuse localhost TLS material."""
    cert_path, key_path = localhost_cert_paths()
    version_path = app_data_dir() / "localhost_cert_version"
    if _cert_is_current(cert_path, key_path, version_path):
        return cert_path, key_path

    cert_path.unlink(missing_ok=True)
    key_path.unlink(missing_ok=True)

    if not _generate_with_mkcert(cert_path, key_path):
        _generate_self_signed(cert_path, key_path)

    version_path.write_text(str(CERT_VERSION))
    return cert_path, key_path


def localhost_ssl_context() -> ssl.SSLContext:
    cert_path, key_path = ensure_localhost_cert()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return context