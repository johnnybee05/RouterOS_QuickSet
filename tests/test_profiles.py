import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from factories import router_profile
from mqs.generator.hardening import binding
from mqs.messages_cs import MESSAGES
from mqs.model.wan import PppoeWan
from mqs.paths import app_data_dir, backups_dir, logs_dir, profiles_dir
from mqs.profiles.store import (
    ProfileError,
    ProfileStore,
    export_profile,
    import_profile,
    profile_from_json,
    profile_to_json,
    safe_filename,
)


def custom_profile():
    profile = router_profile(identity="PV-R9")
    text = binding.set_ssh_port(profile.services.hardening_text, 2222)
    wan = profile.wan.model_copy(update={
        "settings": PppoeWan(username="isp-user", password=SecretStr("pppoe-secret"))})
    return profile.model_copy(update={
        "wan": wan,
        "services": profile.services.model_copy(update={"hardening_text": text}),
    })


def test_json_envelope_without_secrets():
    text = profile_to_json(custom_profile(), now=datetime(2026, 9, 15, 10, 0, tzinfo=UTC))
    data = json.loads(text)
    assert data["format"] == "mqs-profile" and data["format_version"] == 1
    assert data["saved_at"] == "2026-09-15T10:00:00+00:00"
    assert "pppoe-secret" not in text and '"password"' not in text
    restored = profile_from_json(text)
    assert restored.wan.settings.username == "isp-user"
    assert "set ssh port=2222" in restored.services.hardening_text  # custom block kept


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("{not json", "profile.invalid_json"),
        ('{"format": "other"}', "profile.wrong_format"),
        ("[]", "profile.wrong_format"),
        ('{"format": "mqs-profile", "format_version": 9, "profile": {}}',
         "profile.unsupported_version"),
        ('{"format": "mqs-profile", "format_version": 1, "profile": {"identity": 5}}',
         "profile.invalid"),
    ],
)
def test_invalid_profiles(text, code):
    with pytest.raises(ProfileError) as err:
        profile_from_json(text)
    assert err.value.code == code and code in MESSAGES


def test_store_save_list_load_delete(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    assert store.list() == []
    path = store.save(custom_profile(), "Pobočka Brno")
    assert path.name == "Pobočka Brno.json"
    store.save(router_profile(identity="A"), "alfa")
    assert store.list() == ["alfa", "Pobočka Brno"]
    loaded = store.load("Pobočka Brno")
    assert loaded.name == "Pobočka Brno" and loaded.identity == "PV-R9"
    store.delete("alfa")
    store.delete("alfa")
    assert store.list() == ["Pobočka Brno"]
    assert not list((tmp_path / "profiles").glob("*.tmp"))
    with pytest.raises(ProfileError) as err:
        store.load("missing")
    assert err.value.code == "profile.not_found"
    with pytest.raises(ProfileError):
        store.save(router_profile(), "  ")


def test_export_import_file(tmp_path):
    target = tmp_path / "share" / "kolega.json"
    export_profile(custom_profile(), target)
    assert import_profile(target).identity == "PV-R9"
    with pytest.raises(ProfileError) as err:
        import_profile(tmp_path / "nope.json")
    assert err.value.code == "profile.unreadable"


def test_import_file_with_a_bom(tmp_path):
    """Windows PowerShell 5.1 writes UTF-8 with a BOM, and the import said
    "profile.invalid_json: Unexpected UTF-8 BOM" (2026-09-26)."""
    target = tmp_path / "powershell.json"
    target.write_bytes(b"\xef\xbb\xbf" + profile_to_json(custom_profile()).encode("utf-8"))
    assert import_profile(target).identity == "PV-R9"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a/b:c", "a_b_c"), ("  .hidden. ", "hidden"), ("", "profil"), ("CON", "_CON"),
     ("com1.backup", "_com1.backup"), ("x" * 200, "x" * 120)],
)
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected


def test_paths():
    env = {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}
    assert app_data_dir(env) == Path(r"C:\Users\x\AppData\Local") / "MikrotikQuickSetPlus"
    assert logs_dir(env).name == "logs" and profiles_dir(env).name == "profiles"
    assert backups_dir(env).name == "backups"
    assert app_data_dir({}).name == "MikrotikQuickSetPlus"
    assert app_data_dir().name == "MikrotikQuickSetPlus"
