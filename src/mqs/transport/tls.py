"""TLS contexts for RouterOS api-ssl.

With `/ip service api-ssl certificate=none` RouterOS offers only anonymous Diffie-Hellman
ciphers. OpenSSL 3 refuses those by default, so a dedicated context is needed. ADH does not
exist in TLS 1.3, therefore the anonymous context is capped to TLS 1.2 (NUTNO OVĚŘIT on a
device, see docs/DIALECT_NOTES.md).
"""

from __future__ import annotations

import ssl

from mqs.transport.base import PeerSecurity

ANONYMOUS_DH_CIPHERS = "ADH:@SECLEVEL=0"


def anonymous_dh_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers(ANONYMOUS_DH_CIPHERS)
    return ctx


def verified_context(ca_file: str | None = None, *, check_hostname: bool = False) -> ssl.SSLContext:
    """Certificate chain is always verified. Devices are usually addressed by IP, so the
    hostname check is off by default and can be enabled when the certificate matches."""
    ctx = ssl.create_default_context(cafile=ca_file)
    ctx.check_hostname = check_hostname
    ctx.verify_mode = ssl.CERT_REQUIRED
    # Python 3.13 enables strict X.509 checks (e.g. mandatory Authority Key Identifier).
    # Certificates generated on RouterOS may not satisfy them; the chain is still verified.
    # NUTNO OVĚŘIT with a device-generated certificate.
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


def peer_security_of(sock: ssl.SSLSocket) -> PeerSecurity:
    cipher = sock.cipher()
    name = cipher[0] if cipher else ""
    if "ADH" in name or sock.getpeercert(binary_form=True) is None:
        return PeerSecurity.ANONYMOUS_DH
    return PeerSecurity.VERIFIED
