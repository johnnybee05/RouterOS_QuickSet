"""Private key files: whether one is under a passphrase, and loading it with one.

A key kept under a passphrase is the normal case on a workstation, so the window and the
command line both have to know whether a file needs one *before* a login is attempted.
paramiko only says so by raising, and what it raises depends on the file's format, so the
answer is read from the file itself.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

import paramiko

OPENSSH_BEGIN = "-----BEGIN OPENSSH PRIVATE KEY-----"
OPENSSH_END = "-----END OPENSSH PRIVATE KEY-----"
OPENSSH_MAGIC = b"openssh-key-v1\x00"
NO_CIPHER = "none"  # what an OpenSSH-format key without a passphrase names


def load_private_key(path: str, passphrase: str | None = None) -> Any:
    """paramiko's loader, given the passphrase as the bytes it insists on.

    `PKey.from_path(path, "text")` raises TypeError("password must be bytes") in paramiko
    5.0.0 (measured 2026-10-02).
    """
    secret = passphrase.encode("utf-8") if passphrase else None
    return paramiko.PKey.from_path(path, secret)


def key_is_encrypted(path: str) -> bool:
    """Whether the private key file is under a passphrase.

    An unreadable or unrecognised file answers "no", and the attempt to load it then
    reports what is really wrong with it instead of asking for a passphrase it has no
    use for.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if "ENCRYPTED PRIVATE KEY" in text:  # PKCS#8
        return True
    if "Proc-Type:" in text and "ENCRYPTED" in text:  # legacy PEM, as ssh-keygen -m PEM
        return True
    if OPENSSH_BEGIN in text:
        cipher = _openssh_cipher(text)
        return bool(cipher) and cipher != NO_CIPHER
    return False


def _openssh_cipher(text: str) -> str:
    """The cipher of an OpenSSH-format key: "none" without a passphrase, "" if unreadable.

    The base64 body starts with the magic, then a uint32 length and that many bytes of
    cipher name ("aes256-ctr" for a key `ssh-keygen` put a passphrase on).
    """
    body = text.split(OPENSSH_BEGIN, 1)[1].split(OPENSSH_END, 1)[0]
    try:
        blob = base64.b64decode("".join(body.split()), validate=True)
    except (binascii.Error, ValueError):
        return ""
    if not blob.startswith(OPENSSH_MAGIC):
        return ""
    rest = blob[len(OPENSSH_MAGIC):]
    length = int.from_bytes(rest[:4], "big")
    return rest[4:4 + length].decode("ascii", errors="replace")
