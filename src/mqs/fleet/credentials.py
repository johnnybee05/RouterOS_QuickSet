"""Which user and password each device took, remembered for the next login and the batch.

A network of three hundred antennas is rarely one password: the window holds one set of
credentials, and the devices that turned it away were logged in with another. Whatever a
device accepted on the *Hromadný update* page is kept against that device, tried first the
next time, and used by the batch for that device alone.

A device is recognised the way the inventory recognises it: by its serial number when both
sides know it, or else by a MAC address in common — before a login a scan knows only the
MAC it heard, and the login records every interface's. An address is a last resort for a
device known by nothing else.

The file is encrypted with Windows DPAPI under the current Windows user: copied to another
account or machine it opens to nothing, and the store then starts empty. Where there is no
DPAPI the credentials live in memory only, for as long as the window is open.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mqs.discover.candidates import Candidate

CREDENTIALS_NAME = "credentials.bin"
FORMAT = 1
_ENTROPY = b"MikrotikQuickSetPlus/credentials"

Cipher = Callable[[bytes], bytes]


@dataclass(frozen=True)
class Remembered:
    """The user and password one device accepted, and what the device is known by."""

    username: str
    password: str = field(default="", repr=False)
    serial: str = ""
    macs: tuple[str, ...] = ()
    address: str = ""
    identity: str = ""

    def matches(self, device: Candidate) -> bool:
        if self.serial and device.serial_number:
            return self.serial == device.serial_number
        if self.macs and device.macs:
            return bool(set(self.macs) & set(device.macs))
        if not device.serial_number and not device.macs:
            return bool(self.address) and self.address == device.address
        return False


class CredentialStore:
    """Remembered credentials, one entry per device, saved after every change."""

    def __init__(self, path: Path | None, *, protect: Cipher | None = None,
                 unprotect: Cipher | None = None) -> None:
        self.path = path
        if protect is None and unprotect is None and sys.platform == "win32":
            protect, unprotect = dpapi_protect, dpapi_unprotect
        # Without a cipher nothing touches the disk: a password is never written in clear.
        self._protect, self._unprotect = protect, unprotect
        self.entries: list[Remembered] = []
        self.load_error = ""
        self.load()

    @property
    def persistent(self) -> bool:
        return self.path is not None and self._protect is not None

    def __len__(self) -> int:
        return len(self.entries)

    def find(self, device: Candidate) -> Remembered | None:
        return next((e for e in self.entries if e.matches(device)), None)

    def remember(self, device: Candidate, username: str, password: str) -> None:
        """What ``device`` (as read by the login that just worked) accepted.

        Every entry the device answers to goes: one device, one entry, and the newest
        login is the one that holds.
        """
        entry = Remembered(username=username, password=password,
                           serial=device.serial_number, macs=tuple(device.macs),
                           address=device.address, identity=device.identity)
        self.entries = [*(e for e in self.entries if not e.matches(device)), entry]
        self.save()

    def forget_all(self) -> None:
        self.entries = []
        self.load_error = ""
        if self.path is not None:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()

    # ------------------------------------------------------------ the file

    def load(self) -> None:
        path, unprotect = self.path, self._unprotect
        if path is None or unprotect is None or not path.exists():
            return
        try:
            data = json.loads(unprotect(path.read_bytes()))
            if data.get("format") != FORMAT:
                raise ValueError(f"unknown format {data.get('format')!r}")
            self.entries = [Remembered(username=d["username"], password=d.get("password", ""),
                                       serial=d.get("serial", ""),
                                       macs=tuple(d.get("macs", ())),
                                       address=d.get("address", ""),
                                       identity=d.get("identity", ""))
                            for d in data.get("devices", [])]
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # Another user's file, or a damaged one: it is not readable, so start empty.
            self.entries = []
            self.load_error = f"{type(exc).__name__}: {exc}"

    def save(self) -> None:
        path, protect = self.path, self._protect
        if path is None or protect is None:
            return
        data = {"format": FORMAT, "devices": [
            {"username": e.username, "password": e.password, "serial": e.serial,
             "macs": list(e.macs), "address": e.address, "identity": e.identity}
            for e in self.entries]}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(protect(json.dumps(data).encode("utf-8")))
        os.replace(temporary, path)


# ------------------------------------------------------------ Windows DPAPI


def _dpapi(name: str, data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> tuple[Blob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = getattr(crypt32, name)
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    source, _keep = blob(data)
    entropy, _keep_entropy = blob(_ENTROPY)
    out = Blob()
    ui_forbidden = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                    ui_forbidden, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), f"{name} failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def dpapi_protect(data: bytes) -> bytes:
    return _dpapi("CryptProtectData", data)


def dpapi_unprotect(data: bytes) -> bytes:
    return _dpapi("CryptUnprotectData", data)
