"""UI texts in the language in use (mqs.i18n).

`from mqs.ui import strings as S`, then `S.NAME`: every attribute is looked up in the
catalog of the current language when it is read, so the language has to be set before the
window is built — which `mqs.app.main` does. mqs.ui.strings_cs is the source; a name a
translation lacks falls back to it.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from mqs import i18n
from mqs.ui import strings_cs, strings_en

_CATALOGS: dict[str, ModuleType] = {"cs": strings_cs, "en": strings_en}


def catalog() -> ModuleType:
    return _CATALOGS.get(i18n.language(), strings_cs)


def __getattr__(name: str) -> Any:
    if name.startswith("__"):
        raise AttributeError(name)
    try:
        return getattr(catalog(), name)
    except AttributeError:
        return getattr(strings_cs, name)
