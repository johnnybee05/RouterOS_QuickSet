"""Both api-ssl TLS branches against a local Python TLS server."""

from __future__ import annotations

import datetime as dt
import socket
import ssl
import threading
from ipaddress import IPv4Address

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import dh, ec
from cryptography.x509.oid import NameOID

from mqs.transport.base import PeerSecurity
from mqs.transport.tls import anonymous_dh_context, peer_security_of, verified_context

pytestmark = pytest.mark.filterwarnings("ignore:Diffie-Hellman over finite fields")


@pytest.fixture(scope="session")
def dh_params_file(tmp_path_factory):
    params = dh.generate_parameters(generator=2, key_size=1024)
    path = tmp_path_factory.mktemp("tls") / "dh.pem"
    path.write_bytes(params.parameter_bytes(serialization.Encoding.PEM,
                                            serialization.ParameterFormat.PKCS3))
    return path


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


@pytest.fixture(scope="session")
def pki(tmp_path_factory):
    """Self-signed CA plus a server certificate for 127.0.0.1."""
    base = tmp_path_factory.mktemp("pki")
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(_name("mqs-test-ca")).issuer_name(_name("mqs-test-ca"))
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("router")).issuer_name(ca_cert.subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(IPv4Address("127.0.0.1"))]),
                       critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path, cert_path, key_path = base / "ca.pem", base / "server.pem", base / "server.key"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return ca_path, cert_path, key_path


class TlsServer:
    """Accepts one connection, completes the handshake, echoes one message."""

    def __init__(self, ctx: ssl.SSLContext) -> None:
        self.ctx = ctx
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        conn, _ = self.sock.accept()
        conn.settimeout(5)
        try:
            with self.ctx.wrap_socket(conn, server_side=True) as tls:
                tls.sendall(tls.recv(16))
        except (ssl.SSLError, OSError) as exc:
            self.error = exc
        finally:
            self.sock.close()

    def join(self) -> None:
        self.thread.join(5)


def _client(ctx: ssl.SSLContext, port: int) -> tuple[PeerSecurity, str]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:  # noqa: SIM117
        with ctx.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
            tls.sendall(b"ping")
            assert tls.recv(16) == b"ping"
            return peer_security_of(tls), tls.cipher()[0]


def _adh_server_context(dh_params_file) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers("ADH:@SECLEVEL=0")
    ctx.load_dh_params(str(dh_params_file))
    return ctx


def test_anonymous_dh_branch(dh_params_file):
    server = TlsServer(_adh_server_context(dh_params_file))
    security, cipher = _client(anonymous_dh_context(), server.port)
    server.join()
    assert security == PeerSecurity.ANONYMOUS_DH
    assert "ADH" in cipher


def test_default_context_refuses_anonymous_dh(dh_params_file):
    server = TlsServer(_adh_server_context(dh_params_file))
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with pytest.raises(ssl.SSLError):
        _client(ctx, server.port)
    server.join()


def _cert_server_context(pki) -> ssl.SSLContext:
    _, cert_path, key_path = pki
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx


def test_verified_branch(pki):
    ca_path, _, _ = pki
    server = TlsServer(_cert_server_context(pki))
    security, _ = _client(verified_context(str(ca_path), check_hostname=True), server.port)
    server.join()
    assert security == PeerSecurity.VERIFIED


def test_verified_branch_rejects_unknown_ca(pki):
    server = TlsServer(_cert_server_context(pki))
    with pytest.raises(ssl.SSLCertVerificationError):
        _client(verified_context(None), server.port)
    server.join()


def test_anonymous_context_settings():
    ctx = anonymous_dh_context()
    assert ctx.verify_mode == ssl.CERT_NONE
    assert not ctx.check_hostname
    assert ctx.maximum_version == ssl.TLSVersion.TLSv1_2
    assert any("ADH" in c["name"] for c in ctx.get_ciphers())


def test_verified_context_settings():
    ctx = verified_context(None)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert not ctx.check_hostname
    assert not ctx.verify_flags & ssl.VERIFY_X509_STRICT
