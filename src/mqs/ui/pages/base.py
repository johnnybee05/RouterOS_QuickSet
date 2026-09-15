"""Base class and input parsing helpers for pages."""

from __future__ import annotations

from collections.abc import Callable
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from typing import Any

from PySide6.QtWidgets import QWidget

from mqs.issues import Issue, Severity
from mqs.model.inventory import DeviceInventory
from mqs.model.profile import DeviceProfile
from mqs.ui import strings_cs as S
from mqs.ui.controller import Analysis, ProfileController
from mqs.ui.widgets.common import apply_field_issues


class InputError(ValueError):
    pass


def parse_cidr(text: str) -> IPv4Interface:
    text = text.strip()
    if "/" not in text:
        raise InputError(S.INVALID_CIDR)
    try:
        return IPv4Interface(text)
    except ValueError as exc:
        raise InputError(S.INVALID_CIDR) from exc


def parse_ip(text: str) -> IPv4Address:
    try:
        return IPv4Address(text.strip())
    except ValueError as exc:
        raise InputError(S.INVALID_IP) from exc


def parse_optional_ip(text: str) -> IPv4Address | None:
    return parse_ip(text) if text.strip() else None


def parse_ip_list(text: str) -> list[IPv4Address]:
    try:
        return [IPv4Address(part.strip()) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise InputError(S.INVALID_IP_LIST) from exc


def parse_network(text: str) -> IPv4Network:
    try:
        return IPv4Network(text.strip(), strict=False)
    except ValueError as exc:
        raise InputError(S.INVALID_NETWORK) from exc


def parse_optional_int(text: str, minimum: int, maximum: int) -> int | None:
    text = text.strip()
    if not text:
        return None
    try:
        value = int(text, 0)
    except ValueError as exc:
        raise InputError(S.INVALID_NUMBER.format(minimum=minimum, maximum=maximum)) from exc
    if not minimum <= value <= maximum:
        raise InputError(S.INVALID_NUMBER.format(minimum=minimum, maximum=maximum))
    return value


def split_list(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def update_in(model: Any, path: str, value: Any) -> Any:
    """Copy of a nested pydantic model with `a.b.c` replaced (values must be typed already)."""
    head, _, rest = path.partition(".")
    if not rest:
        return model.model_copy(update={head: value})
    return model.model_copy(update={head: update_in(getattr(model, head), rest, value)})


class Page(QWidget):
    def __init__(self, controller: ProfileController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.fields: dict[str, QWidget] = {}
        self.syntax_errors: dict[str, str] = {}
        self._loading = False
        self._last_analysis: Analysis | None = None
        controller.profileChanged.connect(self._profile_changed)
        controller.analysisReady.connect(self._analysis_ready)
        controller.inventoryChanged.connect(self._inventory_changed)

    # ------------------------------------------------------------ to override

    def load(self, profile: DeviceProfile) -> None:
        """Fill widgets from the profile (called with signals guarded)."""

    def on_analysis(self, analysis: Analysis) -> None:
        """React to validation / generation results."""

    def on_inventory(self, inventory: DeviceInventory | None) -> None:
        """React to a new device inventory or offline target."""

    # ------------------------------------------------------------ plumbing

    @property
    def has_syntax_errors(self) -> bool:
        return bool(self.syntax_errors)

    def reload(self) -> None:
        self._profile_changed(self.controller.profile, None)

    def _profile_changed(self, profile: DeviceProfile, source: object) -> None:
        if source is self:
            return
        self._loading = True
        try:
            self.load(profile)
        finally:
            self._loading = False

    def _inventory_changed(self, inventory: DeviceInventory | None) -> None:
        self._loading = True
        try:
            self.on_inventory(inventory)
        finally:
            self._loading = False

    def _analysis_ready(self, analysis: Analysis) -> None:
        self._last_analysis = analysis
        self._refresh_field_errors()
        self._loading = True
        try:
            self.on_analysis(analysis)
        finally:
            self._loading = False

    def _refresh_field_errors(self) -> None:
        issues = list(self._last_analysis.all_issues) if self._last_analysis else []
        issues += [Issue("ui.syntax", Severity.ERROR, name, {"message": message})
                   for name, message in self.syntax_errors.items()]
        apply_field_issues(self.fields, issues)

    def push(self, change: Callable[[DeviceProfile], DeviceProfile]) -> None:
        if self._loading:
            return
        self.controller.edit(change, source=self)

    def push_value(self, path: str, value: Any) -> None:
        self.push(lambda profile: update_in(profile, path, value))

    def push_parsed(self, path: str, parse: Callable[[], Any]) -> None:
        """Parse input; on success push it, on failure mark the field red immediately."""
        if self._loading:
            return
        try:
            value = parse()
        except InputError as exc:
            self.syntax_errors[path] = str(exc)
            self._refresh_field_errors()
            return
        if self.syntax_errors.pop(path, None) is not None:
            self._refresh_field_errors()
        self.push_value(path, value)
