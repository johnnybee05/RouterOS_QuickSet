"""Private key files under a passphrase: reading the file, and what the transport does."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from mqs.transport.base import AuthError, ConnectionParams, KeyPassphraseError, TransportKind
from mqs.transport.keys import key_is_encrypted, load_private_key
from mqs.transport.ssh import SshTransport
from test_ssh import FakeClient

PASSPHRASE = "správné heslo"


def write_key(
    path,
    *,
    passphrase: str | None = None,
    encoding=serialization.Encoding.PEM,
    fmt=serialization.PrivateFormat.OpenSSH,
    rsa_key: bool = False,
) -> str:
    key = (rsa.generate_private_key(public_exponent=65537, key_size=2048) if rsa_key
           else ed25519.Ed25519PrivateKey.generate())
    protection = (serialization.BestAvailableEncryption(passphrase.encode()) if passphrase
                  else serialization.NoEncryption())
    path.write_bytes(key.private_bytes(encoding, fmt, protection))
    return str(path)


def test_a_plain_openssh_key_needs_no_passphrase(tmp_path):
    assert key_is_encrypted(write_key(tmp_path / "id_ed25519")) is False


def test_an_openssh_key_under_a_passphrase_is_seen(tmp_path):
    path = write_key(tmp_path / "id_ed25519", passphrase=PASSPHRASE)
    assert "BEGIN OPENSSH PRIVATE KEY" in (tmp_path / "id_ed25519").read_text()
    assert key_is_encrypted(path) is True


def test_a_pkcs8_key_under_a_passphrase_is_seen(tmp_path):
    path = write_key(tmp_path / "id_pkcs8", passphrase=PASSPHRASE,
                     fmt=serialization.PrivateFormat.PKCS8)
    assert key_is_encrypted(path) is True


def test_a_legacy_pem_key_under_a_passphrase_is_seen(tmp_path):
    """`ssh-keygen -m PEM`: "Proc-Type: 4,ENCRYPTED" instead of an OpenSSH cipher name."""
    path = write_key(tmp_path / "id_rsa", passphrase=PASSPHRASE, rsa_key=True,
                     fmt=serialization.PrivateFormat.TraditionalOpenSSL)
    assert "Proc-Type: 4,ENCRYPTED" in (tmp_path / "id_rsa").read_text()
    assert key_is_encrypted(path) is True


@pytest.mark.parametrize("content", ["", "not a key at all", "-----BEGIN OPENSSH PRIVATE KEY-----"])
def test_what_is_not_a_readable_key_is_not_a_passphrase_question(tmp_path, content):
    path = tmp_path / "junk"
    path.write_text(content)
    assert key_is_encrypted(str(path)) is False


def test_a_missing_file_is_not_a_passphrase_question(tmp_path):
    assert key_is_encrypted(str(tmp_path / "nowhere" / "id_ed25519")) is False


def test_the_passphrase_opens_the_key(tmp_path):
    path = write_key(tmp_path / "id_ed25519", passphrase=PASSPHRASE)
    key = load_private_key(path, PASSPHRASE)
    assert key.get_name() == "ssh-ed25519"


def test_a_key_without_its_passphrase_never_reaches_the_device(tmp_path):
    """Nothing is sent: the file is read here, and the window can ask before connecting."""
    made: list[FakeClient] = []
    path = write_key(tmp_path / "id_ed25519", passphrase=PASSPHRASE)
    params = ConnectionParams(host="h", username="bee", key_filename=path)
    transport = SshTransport(params, client_factory=lambda: made.append(FakeClient()))
    with pytest.raises(KeyPassphraseError) as caught:
        transport.connect()
    assert caught.value.wrong is False
    assert caught.value.kind == TransportKind.SSH
    assert made == []


def test_a_wrong_passphrase_says_it_is_the_passphrase(tmp_path):
    """paramiko 5.0.0 calls it "Valid PEM but no BEGIN/END delimiters", which it is not."""
    path = write_key(tmp_path / "id_ed25519", passphrase=PASSPHRASE)
    params = ConnectionParams(host="h", username="bee", key_filename=path,
                              key_passphrase="jiné heslo")
    transport = SshTransport(params, client_factory=FakeClient)
    with pytest.raises(KeyPassphraseError) as caught:
        transport.connect()
    assert caught.value.wrong is True


def test_a_plain_key_that_cannot_be_read_is_not_blamed_on_a_passphrase(tmp_path):
    path = tmp_path / "id_ed25519"
    path.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nnonsense\n")
    params = ConnectionParams(host="h", username="bee", key_filename=str(path))
    transport = SshTransport(params, client_factory=FakeClient)
    with pytest.raises(AuthError) as caught:
        transport.connect()
    assert not isinstance(caught.value, KeyPassphraseError)


def test_the_passphrase_goes_to_the_loader_and_not_to_the_device(tmp_path):
    seen: list[tuple[str, str | None]] = []
    client = FakeClient()
    path = write_key(tmp_path / "id_ed25519", passphrase=PASSPHRASE)
    params = ConnectionParams(host="h", username="bee", key_filename=path,
                              key_passphrase=PASSPHRASE)

    def loader(file: str, secret: str | None = None):
        seen.append((file, secret))
        return "the key"

    transport = SshTransport(params, client_factory=lambda: client, key_loader=loader)
    transport.connect()
    assert seen == [(path, PASSPHRASE)]
    assert client.connect_kwargs["pkey"] == "the key"
    # A key and no account password: the passphrase is the key file's, never a login.
    assert client.connect_kwargs["password"] is None


def test_a_plain_key_is_loaded_with_no_passphrase(tmp_path):
    seen: list[str | None] = []
    path = write_key(tmp_path / "id_ed25519")
    params = ConnectionParams(host="h", username="bee", key_filename=path,
                              key_passphrase="")

    def loader(file: str, secret: str | None = None):
        seen.append(secret)
        return "the key"

    SshTransport(params, client_factory=FakeClient, key_loader=loader).connect()
    assert seen == [None]
