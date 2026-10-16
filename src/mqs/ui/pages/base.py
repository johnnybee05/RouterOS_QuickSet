"""Base class and input parsing helpers for pages."""

from __future__ import annotations

from collections.abc import Callable
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from typing import Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLineEdit, QWidget

from mqs.issues import Issue, Severity
from mqs.model.inventory import DeviceInventory
from mqs.model.profile import DeviceProfile
from mqs.ui import strings as S
from mqs.ui.controller import Analysis, ProfileController
from mqs.ui.widgets.common import apply_field_issues

SECRET_PROPERTY = "secret"
SHOW_ACTION_NAME = "show-password"
_EYE_COLOR = "#8a8a8a"  # readable on both the light and the dark theme


class InputError(ValueError):
    pass


def eye_icon(crossed: bool) -> QIcon:
    """An eye, crossed out for "hide"; drawn, so it needs no icon file in the exe."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(_EYE_COLOR)
    painter.setPen(QPen(color, 2.4))
    outline = QPainterPath()
    outline.moveTo(3, 16)
    outline.quadTo(16, 3, 29, 16)
    outline.quadTo(16, 29, 3, 16)
    painter.drawPath(outline)
    painter.setBrush(color)
    painter.drawEllipse(QPointF(16, 16), 4.5, 4.5)
    if crossed:
        painter.drawLine(6, 27, 26, 5)
    painter.end()
    return QIcon(pixmap)


def mark_secret(field: QLineEdit) -> QLineEdit:
    """A password field: hidden, with an eye at its end that shows it in clear.

    The window's *Zobrazení → Zobrazovat hesla* switches every such field at once,
    through :func:`show_secret` as well.
    """
    field.setProperty(SECRET_PROPERTY, True)
    action = QAction(field)
    action.setObjectName(SHOW_ACTION_NAME)
    action.setCheckable(True)
    action.toggled.connect(lambda shown: _show(field, action, shown))
    field.addAction(action, QLineEdit.ActionPosition.TrailingPosition)
    _show(field, action, False)
    return field


def show_secret(field: QLineEdit, shown: bool) -> None:
    action = field.findChild(QAction, SHOW_ACTION_NAME)
    if action is not None:
        action.setChecked(shown)  # its toggled does the rest
        _show(field, action, shown)


def _show(field: QLineEdit, action: QAction, shown: bool) -> None:
    field.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)
    action.setIcon(eye_icon(crossed=shown))
    action.setToolTip(S.PASSWORD_HIDE if shown else S.PASSWORD_SHOW)


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

    def refresh_state(self, profile: DeviceProfile) -> None:
        """Enabled/visible state of widgets that follow the value of another field.

        Runs for every profile change including the page's own edits, which never come
        back through load(); a page that gates widgets on a value it edits itself would
        otherwise never update them. Only touch widget state here, never values — this
        can run while the user is typing.
        """

    # ------------------------------------------------------------ plumbing

    @property
    def has_syntax_errors(self) -> bool:
        return bool(self.syntax_errors)

    def reload(self) -> None:
        self._profile_changed(self.controller.profile, None)

    def _profile_changed(self, profile: DeviceProfile, source: object) -> None:
        self._loading = True
        try:
            if source is not self:  # never overwrite the widget being edited
                self.load(profile)
            self.refresh_state(profile)
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
