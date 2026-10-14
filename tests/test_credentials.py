"""What each device took to log in to, kept against that device."""

from __future__ import annotations

import sys

import pytest

from mqs.discover.candidates import Candidate, Sighting, Source, from_sighting
from mqs.fleet.credentials import CredentialStore, dpapi_protect, dpapi_unprotect


def scrambled(data: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in data)


def store(path) -> CredentialStore:
    return CredentialStore(path, protect=scrambled, unprotect=scrambled)


def logged_in(serial: str, *macs: str, address: str = "192.0.2.5") -> Candidate:
    """A device as a login leaves it: its serial and every interface's MAC."""
    return Candidate(serial_number=serial, macs=list(macs), addresses=[address],
                     identity=f"dev-{serial}")


def heard(mac: str, address: str = "192.0.2.5") -> Candidate:
    """The same device after a fresh scan: one MAC, no serial yet."""
    return from_sighting(Sighting(source=Source.MNDP, address=address, macs=[mac]))


def test_a_device_heard_again_is_found_by_any_of_its_macs(tmp_path):
    credentials = store(tmp_path / "c.bin")
    credentials.remember(logged_in("S1", "AA:BB:CC:00:00:01", "AA:BB:CC:00:00:02"),
                         "bee", "tajne")
    found = credentials.find(heard("aa-bb-cc-00-00-02"))
    assert found is not None and (found.username, found.password) == ("bee", "tajne")
    assert credentials.find(heard("AA:BB:CC:00:00:09")) is None


def test_the_serial_decides_when_both_sides_know_it(tmp_path):
    credentials = store(tmp_path / "c.bin")
    credentials.remember(logged_in("S1", "AA:BB:CC:00:00:01"), "bee", "a")
    assert credentials.find(logged_in("S1", "AA:BB:CC:00:00:07")) is not None
    # A shared MAC does not make another board the same device.
    assert credentials.find(logged_in("S2", "AA:BB:CC:00:00:01")) is None


def test_an_address_is_used_only_for_a_device_known_by_nothing_else(tmp_path):
    credentials = store(tmp_path / "c.bin")
    credentials.remember(logged_in("S1", "AA:BB:CC:00:00:01", address="192.0.2.7"),
                         "bee", "a")
    assert credentials.find(Candidate(addresses=["192.0.2.7"])) is not None
    assert credentials.find(heard("AA:BB:CC:00:00:09", address="192.0.2.7")) is None


def test_different_devices_keep_different_credentials(tmp_path):
    credentials = store(tmp_path / "c.bin")
    credentials.remember(logged_in("S1", "AA:BB:CC:00:00:01"), "admin", "one")
    credentials.remember(logged_in("S2", "AA:BB:CC:00:00:02"), "bee", "two")
    assert credentials.find(heard("AA:BB:CC:00:00:01")).password == "one"
    assert credentials.find(heard("AA:BB:CC:00:00:02")).username == "bee"


def test_the_newest_login_replaces_what_the_device_took_before(tmp_path):
    credentials = store(tmp_path / "c.bin")
    credentials.remember(logged_in("S1", "AA:BB:CC:00:00:01"), "admin", "old")
    credentials.remember(logged_in("S1", "AA:BB:CC:00:00:01"), "admin", "new")
    assert len(credentials) == 1
    assert credentials.find(heard("AA:BB:CC:00:00:01")).password == "new"


def test_they_survive_a_restart_and_the_file_holds_no_password_in_clear(tmp_path):
    path = tmp_path / "c.bin"
    store(path).remember(logged_in("S1", "AA:BB:CC:00:00:01"), "bee", "tajne-heslo")
    assert b"tajne-heslo" not in path.read_bytes()
    again = store(path)
    assert again.find(heard("AA:BB:CC:00:00:01")).password == "tajne-heslo"


def test_an_unreadable_file_starts_empty_and_says_why(tmp_path):
    path = tmp_path / "c.bin"
    path.write_bytes(b"not ours")
    credentials = store(path)
    assert len(credentials) == 0 and credentials.load_error


def test_a_store_without_a_file_remembers_for_the_session_only():
    credentials = CredentialStore(None)
    credentials.remember(logged_in("S1", "AA:BB:CC:00:00:01"), "bee", "x")
    assert not credentials.persistent
    assert credentials.find(heard("AA:BB:CC:00:00:01")) is not None


def test_forgetting_removes_the_file(tmp_path):
    path = tmp_path / "c.bin"
    credentials = store(path)
    credentials.remember(logged_in("S1", "AA:BB:CC:00:00:01"), "bee", "x")
    credentials.forget_all()
    assert len(credentials) == 0 and not path.exists()
    assert len(store(path)) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_dpapi_round_trip(tmp_path):
    secret = "heslo-ěščř".encode()
    sealed = dpapi_protect(secret)
    assert secret not in sealed and dpapi_unprotect(sealed) == secret
    path = tmp_path / "c.bin"
    CredentialStore(path).remember(logged_in("S1", "AA:BB:CC:00:00:01"), "bee", "x")
    assert CredentialStore(path).find(heard("AA:BB:CC:00:00:01")).username == "bee"
