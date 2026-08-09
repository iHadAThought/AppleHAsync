"""TLS helpers, HMAC webhooks, auth utilities."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import ssl
from pathlib import Path
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)


def hmac_sha256_hex(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_hmac(secret: str, body: bytes, signature: str) -> bool:
    expected = hmac_sha256_hex(secret, body)
    return hmac.compare_digest(expected, signature or "")


def cert_spki_sha256(cert_pem: bytes) -> str:
    """SHA-256 of DER certificate (simple pin; not pure SPKI but stable)."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        cert = x509.load_pem_x509_certificate(cert_pem)
        der = cert.public_bytes(serialization.Encoding.DER)
        return hashlib.sha256(der).hexdigest()
    except Exception:
        return hashlib.sha256(cert_pem).hexdigest()


def generate_self_signed_cert(certs_dir: Path, common_name: str = "apple-hasync") -> tuple[Path, Path]:
    """Generate a local self-signed cert for the agent HTTPS listener."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    certs_dir.mkdir(parents=True, exist_ok=True)
    key_path = certs_dir / "agent.key"
    cert_path = certs_dir / "agent.crt"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]
            ),
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
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.chmod(0o600)
    cert_path.chmod(0o600)
    return cert_path, key_path


def build_ssl_context(
    *,
    cert_file: str | Path | None,
    key_file: str | Path | None,
) -> ssl.SSLContext | None:
    if not cert_file or not key_file:
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert_file), str(key_file))
    return ctx


def client_ip_allowed(client_ip: str | None, allowlist: list[str]) -> bool:
    # Empty allowlist intentionally allows all clients (not fail-closed). Prefer
    # binding to 127.0.0.1 or setting explicit CIDRs when exposing on LAN.
    if not allowlist:
        return True
    if not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


async def test_ha_connection(
    *,
    base_url: str,
    token: str,
    verify_tls: bool = True,
    ca_path: str | None = None,
    allow_insecure_http: bool = False,
) -> dict[str, Any]:
    if base_url.startswith("http://") and not allow_insecure_http:
        return {
            "ok": False,
            "error": "HTTPS required (set allow_insecure_http to override for lab use)",
        }
    verify: bool | str = verify_tls
    if ca_path:
        verify = ca_path
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(verify=verify, timeout=10.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/", headers=headers)
            return {
                "ok": resp.status_code == 200,
                "status_code": resp.status_code,
                "body": resp.text[:200],
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def post_ha_webhook(
    *,
    base_url: str,
    webhook_id: str,
    webhook_secret: str,
    token: str,
    payload: bytes,
    verify_tls: bool = True,
    ca_path: str | None = None,
    allow_insecure_http: bool = False,
) -> dict[str, Any]:
    if base_url.startswith("http://") and not allow_insecure_http:
        return {"ok": False, "error": "HTTPS required"}
    if not webhook_id:
        return {"ok": False, "error": "webhook_id not configured"}
    verify: bool | str = ca_path if ca_path else verify_tls
    # HA rejects refreshes without a configured secret; always sign when set.
    if not webhook_secret:
        return {"ok": False, "error": "webhook_secret not configured"}
    sig = hmac_sha256_hex(webhook_secret, payload)
    headers = {
        "Content-Type": "application/json",
        "X-Apple-HASync-Signature": sig,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{base_url.rstrip('/')}/api/webhook/{webhook_id}"
    try:
        async with httpx.AsyncClient(verify=verify, timeout=10.0) as client:
            resp = await client.post(url, content=payload, headers=headers)
            return {"ok": 200 <= resp.status_code < 300, "status_code": resp.status_code}
    except Exception as exc:
        _LOGGER.warning("Webhook to %s failed: %s", base_url, exc)
        return {"ok": False, "error": str(exc)}
