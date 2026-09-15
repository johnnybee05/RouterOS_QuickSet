"""QSyntaxHighlighter for RouterOS scripts."""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

_VERBS = r"\b(add|set|remove|enable|disable|print|export|import)\b"

# (pattern, format key); later rules override earlier ones on overlapping text.
_RULES = [
    (r"\b[a-z][\w.-]*(?==)", "key"),
    (_VERBS, "verb"),
    (r"^\s*/[\w-]+(?:[ /](?!(?:add|set|remove|enable|disable|print|export|import)\b)[\w-]+)*",
     "menu"),
    (r"\[\s*find\b[^\]]*\]", "find"),
    (r'"(?:[^"\\]|\\.)*"', "string"),
    (r"^\s*:[\w-]+", "scripting"),
    (r"^\s*#.*$", "comment"),
]

_PALETTES = {
    False: {"menu": "#0b5cad", "verb": "#7b1fa2", "key": "#00796b", "string": "#c75000",
            "find": "#8d6e00", "scripting": "#c62828", "comment": "#757575"},
    True: {"menu": "#6cb6ff", "verb": "#d7a6ff", "key": "#4fd1c5", "string": "#ffab70",
           "find": "#e3c16f", "scripting": "#ff7b72", "comment": "#8b949e"},
}


class RouterOsHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument, dark: bool = False) -> None:
        super().__init__(document)
        self._rules = [(QRegularExpression(p, QRegularExpression.PatternOption.MultilineOption),
                        key) for p, key in _RULES]
        self._formats: dict[str, QTextCharFormat] = {}
        self.set_dark(dark)

    def set_dark(self, dark: bool) -> None:
        self._formats = {}
        for key, color in _PALETTES[dark].items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if key == "menu":
                fmt.setFontWeight(QFont.Weight.Bold)
            if key == "comment":
                fmt.setFontItalic(True)
            self._formats[key] = fmt
        self.rehighlight()

    def format_key_at(self, text: str, position: int) -> str | None:
        """Format applied at `position` of a single line (used by tests)."""
        found = None
        for regex, key in self._rules:
            it = regex.globalMatch(text)
            while it.hasNext():
                match = it.next()
                if match.capturedStart() <= position < match.capturedEnd():
                    found = key
        return found

    def highlightBlock(self, text: str) -> None:
        for regex, key in self._rules:
            it = regex.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), self._formats[key])
