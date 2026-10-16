"""Which language the texts are in.

One language per process, chosen once at start: the window builds its widgets from the
catalog, so a change takes a restart, and the CLI prints in one language from start to end.
Czech is the source — every catalog is checked against it in the tests — and the default
until something chooses otherwise, so code that never asks (the tests, a library user) keeps
the Czech texts it always had.

The choice is "" for automatic: `MQS_LANG` in the environment, then the system locale,
Czech for a Czech or Slovak one and English for anything else.
"""

from __future__ import annotations

import locale
import os
from collections.abc import Mapping

LANGUAGES: dict[str, str] = {"cs": "Čeština", "en": "English"}
DEFAULT = "cs"
AUTO = ""
ENV = "MQS_LANG"

# Locale names that mean Czech: "cs_CZ" from Qt and POSIX, "Czech_Czechia" from Windows.
_CZECH_PREFIXES = ("cs", "sk", "czech", "slovak")

_current = DEFAULT


def language() -> str:
    return _current


def set_language(code: str) -> str:
    """Use ``code`` from now on; an unknown one falls back to the default. Returns the one
    in use."""
    global _current
    _current = code if code in LANGUAGES else DEFAULT
    return _current


def from_locale(name: str | None) -> str:
    if not name:
        return DEFAULT
    lowered = name.lower()
    return "cs" if lowered.startswith(_CZECH_PREFIXES) else "en"


def system_locale() -> str | None:
    try:
        return locale.getlocale()[0]
    except ValueError:
        return None


def resolve(choice: str, *, env: Mapping[str, str] | None = None,
            system: str | None = None) -> str:
    """The language for a stored or given choice. ``system`` is the locale name to use for
    automatic; None asks Python's locale."""
    if choice in LANGUAGES:
        return choice
    environ = os.environ if env is None else env
    wanted = environ.get(ENV, "").strip().lower()
    if wanted in LANGUAGES:
        return wanted
    return from_locale(system if system is not None else system_locale())
