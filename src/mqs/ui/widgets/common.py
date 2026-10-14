"""Small reusable widgets and helpers."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import IntEnum

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QPalette,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QStyle,
    QWidget,
)

from mqs.apply.events import StepStatus
from mqs.issues import Issue, Severity
from mqs.ui.theme import ERROR_RED, OK_GREEN, WARNING_ORANGE

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


class LogLevel(IntEnum):
    """A log line's color, kept in its text block's user state."""

    NORMAL = 0
    OK = 1
    WARNING = 2
    ERROR = 3
    MUTED = 4


STEP_LEVEL = {
    StepStatus.RUNNING: LogLevel.NORMAL, StepStatus.OK: LogLevel.OK,
    StepStatus.WARNING: LogLevel.WARNING, StepStatus.FAILED: LogLevel.ERROR,
    StepStatus.SKIPPED: LogLevel.MUTED,
}
SEVERITY_LEVEL = {
    Severity.ERROR: LogLevel.ERROR, Severity.WARNING: LogLevel.WARNING,
    Severity.INFO: LogLevel.NORMAL,
}

# The second and later lines of a multi-line entry carry no timestamp.
_CONTINUATION = 8
_STAMP_LENGTH = len("00:00:00")

_LOG_COLORS = {
    False: {LogLevel.OK: OK_GREEN, LogLevel.WARNING: WARNING_ORANGE,
            LogLevel.ERROR: ERROR_RED, LogLevel.MUTED: "#757575"},
    True: {LogLevel.OK: "#7ee787", LogLevel.WARNING: "#ffab70",
           LogLevel.ERROR: "#ff7b72", LogLevel.MUTED: "#8b949e"},
}


class _LogHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self._formats: dict[LogLevel, QTextCharFormat] = {}
        self.set_dark(False)

    def set_dark(self, dark: bool) -> None:
        self._formats = {}
        for level, color in _LOG_COLORS[dark].items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if level == LogLevel.ERROR:
                fmt.setFontWeight(QFont.Weight.Bold)
            self._formats[level] = fmt
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        state = self.currentBlock().userState()
        if state < 0:
            return
        start = 0
        if not state & _CONTINUATION:
            self.setFormat(0, _STAMP_LENGTH, self._formats[LogLevel.MUTED])
            start = _STAMP_LENGTH
        fmt = self._formats.get(LogLevel(state & ~_CONTINUATION))
        if fmt is not None:
            self.setFormat(start, len(text) - start, fmt)


class LogView(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)
        self.setFont(monospace_font())
        self._highlighter = _LogHighlighter(self.document())
        self._follow_palette()

    def append_line(self, text: str, level: int = LogLevel.NORMAL) -> None:
        lines = text.count("\n") + 1
        self.appendPlainText(f"{datetime.now():%H:%M:%S}  {text}")
        block = self.document().lastBlock()
        for index in range(lines):  # from the entry's last line back to its first
            block.setUserState(level | (_CONTINUATION if index < lines - 1 else 0))
            self._highlighter.rehighlightBlock(block)
            block = block.previous()
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def append_issue(self, issue: Issue) -> None:
        self.append_line(issue.message, SEVERITY_LEVEL[issue.severity])

    def level_at(self, line: int) -> LogLevel | None:
        """The level of a line, None for one appended without append_line (used by tests)."""
        state = self.document().findBlockByNumber(line).userState()
        return None if state < 0 else LogLevel(state & ~_CONTINUATION)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self._follow_palette()

    def _follow_palette(self) -> None:
        # The window follows the Windows light/dark setting while it runs; the log's
        # colors go with it, the lines already written included.
        base = self.palette().color(QPalette.ColorRole.Base)
        self._highlighter.set_dark(base.lightness() < 128)


def _record_level(record: logging.LogRecord) -> LogLevel:
    if record.levelno >= logging.ERROR:
        return LogLevel.ERROR
    if record.levelno >= logging.WARNING:
        return LogLevel.WARNING
    return LogLevel.NORMAL if record.levelno >= logging.INFO else LogLevel.MUTED


class _LogBridge(QObject):
    line = Signal(str, int)


class QtLogHandler(logging.Handler):
    """Thread-safe logging handler feeding a LogView through a queued signal."""

    def __init__(self, view: LogView) -> None:
        super().__init__()
        self.bridge = _LogBridge()
        self.bridge.line.connect(view.append_line)

    def emit(self, record: logging.LogRecord) -> None:
        self.bridge.line.emit(self.format(record), _record_level(record))


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
