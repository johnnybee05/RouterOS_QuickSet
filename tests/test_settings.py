"""The window's remembered switches and login form."""

from __future__ import annotations

import sys

import pytest

from mqs.ui.settings import LOGIN_NAME, SETTINGS_NAME, SavedLogin, SettingsStore, WindowSettings


def scrambled(data: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in data)


def store(path) -> SettingsStore:
    return SettingsStore(path, protect=scrambled, unprotect=scrambled)


def test_nothing_saved_yet_is_the_defaults(tmp_path):
    assert store(tmp_path).load_settings() == WindowSettings()
    assert store(tmp_path).load_login() is None


def test_settings_round_trip(tmp_path):
    store(tmp_path).save_settings(WindowSettings(show_passwords=True, remember_login=True))
    assert store(tmp_path).load_settings() == WindowSettings(True, True)


def test_a_damaged_or_odd_settings_file_falls_back_to_the_defaults(tmp_path):
    (tmp_path / SETTINGS_NAME).write_text("{not json", encoding="utf-8")
    assert store(tmp_path).load_settings() == WindowSettings()
    (tmp_path / SETTINGS_NAME).write_text(
        '{"show_passwords": "yes", "remember_login": true, "later": 1}', encoding="utf-8")
    assert store(tmp_path).load_settings() == WindowSettings(remember_login=True)


def test_the_login_is_kept_encrypted(tmp_path):
    login = SavedLogin(host="10.0.0.1", username="bee", password="s3cret", ssh_port=122)
    store(tmp_path).save_login(login)
    assert b"s3cret" not in (tmp_path / LOGIN_NAME).read_bytes()
    assert store(tmp_path).load_login() == login
    assert "s3cret" not in repr(login)


def test_an_unreadable_login_is_nothing_to_restore(tmp_path):
    (tmp_path / LOGIN_NAME).write_bytes(b"\x00garbage")
    assert store(tmp_path).load_login() is None


def test_forgetting_removes_the_file(tmp_path):
    store(tmp_path).save_login(SavedLogin(host="x"))
    store(tmp_path).forget_login()
    assert not (tmp_path / LOGIN_NAME).exists()
    store(tmp_path).forget_login()  # twice is fine


def test_nowhere_to_keep_it_keeps_nothing(tmp_path):
    nowhere = SettingsStore(None)
    assert not nowhere.can_remember_login
    nowhere.save_login(SavedLogin(password="x"))
    assert nowhere.load_login() is None
    assert nowhere.load_settings() == WindowSettings()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_dpapi_round_trip(tmp_path):
    login = SavedLogin(host="10.0.0.1", password="s3cret")
    SettingsStore(tmp_path).save_login(login)
    assert b"s3cret" not in (tmp_path / LOGIN_NAME).read_bytes()
    assert SettingsStore(tmp_path).load_login() == login
