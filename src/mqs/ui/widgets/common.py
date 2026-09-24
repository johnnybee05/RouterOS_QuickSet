"""Small reusable widgets and helpers."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QStyle,
    QWidget,
)

from mqs.issues import Issue, Severity

_SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def set_field_error(widget: QWidget, message: str | None) -> None:
    """Red border + tooltip with the concrete message (real-time validation)."""
    if widget.property("_baseTooltip") is None:
        widget.setProperty("_baseTooltip", widget.toolTip())
    widget.setProperty("fieldError", bool(message))
    widget.setToolTip(message or widget.property("_baseTooltip") or "")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def apply_field_issues(fields: Mapping[str, QWidget], issues: Iterable[Issue]) -> None:
    messages: dict[str, list[str]] = {}
    for issue in issues:
        if issue.field in fields and issue.severity in (Severity.ERROR, Severity.WARNING):
            messages.setdefault(issue.field, []).append(issue.message)
    for name, widget in fields.items():
        text = "\n".join(messages.get(name, []))
        set_field_error(widget, text or None)


def monospace_font():
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)


class IssueList(QListWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlternatingRowColors(True)

    def set_issues(self, issues: Iterable[Issue]) -> None:
        self.clear()
        icons = {
            Severity.ERROR: QStyle.StandardPixmap.SP_MessageBoxCritical,
            Severity.WARNING: QStyle.StandardPixmap.SP_MessageBoxWarning,
            Severity.INFO: QStyle.StandardPixmap.SP_MessageBoxInformation,
        }
        seen: set[tuple[str, str]] = set()
        for issue in sorted(issues, key=lambda i: _SEVERITY_ORDER[i.severity]):
            key = (issue.code, issue.message)
            if key in seen:
                continue
            seen.add(key)
            item = QListWidgetItem(self.style().standardIcon(icons[issue.severity]),
                                   issue.message)
            item.setData(Qt.ItemDataRole.UserRole, issue)
            self.addItem(item)


class LogView(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)
        self.setFont(monospace_font())

    def append_line(self, text: str) -> None:
        self.appendPlainText(f"{datetime.now():%H:%M:%S}  {text}")
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class _LogBridge(QObject):
    line = Signal(str)


class QtLogHandler(logging.Handler):
    """Thread-safe logging handler feeding a LogView through a queued signal."""

    def __init__(self, view: LogView) -> None:
        super().__init__()
        self.bridge = _LogBridge()
        self.bridge.line.connect(view.append_line)

    def emit(self, record: logging.LogRecord) -> None:
        self.bridge.line.emit(self.format(record))


class PortSelector(QListWidget):
    """Checkable port list; some ports can be locked (e.g. the WAN port) with a reason."""

    selectionChanged2 = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False
        self.itemChanged.connect(self._changed)

    def set_ports(self, ports: Iterable[str], checked: Iterable[str],
                  locked: Mapping[str, str] | None = None,
                  default_names: Mapping[str, str] | None = None) -> None:
        """``default_names`` maps a renamed port to its default-name, shown next to it."""
        self._updating = True
        locked = locked or {}
        default_names = default_names or {}
        checked_set = set(checked)
        self.clear()
        for port in ports:
            label = f"{port} ({default_names[port]})" if port in default_names else port
            item = QListWidgetItem(label)
            flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            if port in locked:
                flags = Qt.ItemFlag.ItemIsUserCheckable  # visible but disabled
                item.setToolTip(locked[port])
                item.setText(f"{label} – {locked[port]}")
            item.setFlags(flags)
            item.setData(Qt.ItemDataRole.UserRole, port)
            state = port in checked_set and port not in locked
            item.setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
            self.addItem(item)
        self._updating = False

    def checked_ports(self) -> list[str]:
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())
                if self.item(i).checkState() == Qt.CheckState.Checked]

    def _changed(self, _item: QListWidgetItem) -> None:
        if not self._updating:
            self.selectionChanged2.emit(self.checked_ports())
