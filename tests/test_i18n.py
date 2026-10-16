"""Languages: every catalog says what the Czech source says, with the same placeholders."""

from __future__ import annotations

import re
import string
from types import ModuleType

import pytest

from mqs import i18n, messages, messages_cs, messages_en
from mqs.capabilities.chips import _known_devices
from mqs.issues import Issue
from mqs.ui import strings, strings_cs, strings_en

CZECH_LETTERS = re.compile(r"[áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]")


def _public(module: ModuleType) -> dict[str, object]:
    return {name: value for name, value in vars(module).items()
            if name.isupper() and not name.startswith("_")}


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def _leaves(value: object, path: str):
    """Every string in a catalog value, with where it is."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f"{path}[{key!r}]")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}[{index}]")


def test_english_ui_catalog_has_every_czech_name_and_shape():
    czech, english = _public(strings_cs), _public(strings_en)
    assert set(english) == set(czech)
    for name, value in czech.items():
        other = english[name]
        assert type(other) is type(value), name
        if isinstance(value, dict):
            assert set(other) == set(value), name
        if isinstance(value, list):
            assert len(other) == len(value), name


def test_english_ui_texts_take_the_same_placeholders():
    english = _public(strings_en)
    for name, value in _public(strings_cs).items():
        theirs = dict(_leaves(english[name], name))
        for path, text in _leaves(value, name):
            assert _fields(theirs[path]) == _fields(text), path


def test_english_ui_texts_are_english():
    for name, value in _public(strings_en).items():
        for path, text in _leaves(value, name):
            assert not CZECH_LETTERS.search(text), path


def test_english_messages_cover_every_code_with_the_same_placeholders():
    assert set(messages_en.MESSAGES) == set(messages_cs.MESSAGES)
    for code, text in messages_cs.MESSAGES.items():
        assert _fields(messages_en.MESSAGES[code]) == _fields(text), code
        assert not CZECH_LETTERS.search(messages_en.MESSAGES[code]), code
    assert set(messages_en.STEP_PREFIX) == set(messages_cs.STEP_PREFIX)


def test_every_device_note_reads_in_english():
    i18n.set_language("en")
    notes = [note for entry in _known_devices().values() for note in (entry or {}).get(
        "notes") or []]
    assert notes
    for note in notes:
        assert not CZECH_LETTERS.search(messages.device_note(note)), note


def test_device_notes_stay_as_written_in_czech():
    note = "Atheros8327 (ether1–5). MSTP ani vlan-filtering se neoffloadují."
    assert messages.device_note(note) == note


def test_texts_follow_the_language_in_use():
    issue = Issue("profile.not_found")
    assert strings.DISCONNECTED == strings_cs.DISCONNECTED
    assert issue.message == messages_cs.MESSAGES["profile.not_found"]
    assert messages.step_prefix("failed") == "CHYBA"
    i18n.set_language("en")
    assert strings.DISCONNECTED == "Disconnected"
    assert strings.NAV["device"] == "Device"
    assert issue.message == "The profile does not exist."
    assert messages.step_prefix("failed") == "ERROR"


def test_unknown_names_and_codes():
    i18n.set_language("en")
    assert messages.format_message("no.such.code") == "no.such.code"
    with pytest.raises(AttributeError):
        _ = strings.NO_SUCH_TEXT


def test_unknown_language_falls_back_to_czech():
    assert i18n.set_language("de") == "cs"


@pytest.mark.parametrize(("choice", "env", "system", "expected"), [
    ("en", {"MQS_LANG": "cs"}, "cs_CZ", "en"),  # chosen in the window wins
    ("", {"MQS_LANG": "en"}, "cs_CZ", "en"),  # then the environment
    ("", {"MQS_LANG": "xx"}, "cs_CZ", "cs"),  # a bad one is ignored
    ("", {}, "cs_CZ", "cs"),
    ("", {}, "sk_SK", "cs"),
    ("", {}, "Czech_Czechia", "cs"),  # Windows locale names
    ("", {}, "de_DE", "en"),
    ("", {}, "en_US", "en"),
    ("", {}, "", "cs"),
])
def test_resolve(choice, env, system, expected):
    assert i18n.resolve(choice, env=env, system=system) == expected
