"""Parser for the restricted RouterOS script subset used in the editable hardening block.

Supported: menu path lines (`/ip service`), inline commands (`/ip dns set servers=...`),
`add` / `set` / `remove` / `enable` / `disable` with `key=value` arguments, `[ find ... ]`
selectors, numeric indexes, item names (`set ssh port=122`), comments and export-style
backslash line continuation. Anything else is kept verbatim as a RawLine and reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mqs.generator.ir import Command, Comment, Find, Item, RawLine, Selector, normalize_path
from mqs.issues import Issue, Severity

VERBS = frozenset({"add", "set", "remove", "enable", "disable"})
_SELECTOR_VERBS = frozenset({"set", "remove", "enable", "disable"})


@dataclass(frozen=True)
class ParsedLine:
    line_no: int  # 0-based index of the first physical line
    line_count: int  # physical lines consumed (continuations)
    menu: str | None  # menu context after this line
    item: Item | None  # None for a bare menu path line
    inline: bool = False  # command written together with its path on one line


@dataclass(frozen=True)
class ParseProblem:
    line_no: int
    text: str
    reason: str  # code: "tokenize", "unknown_verb", "no_menu", "bad_argument", "scripting"

    def to_issue(self) -> Issue:
        return Issue(
            f"hardening.parse.{self.reason}",
            Severity.WARNING,
            "services.hardening_text",
            {"line": self.line_no + 1, "text": self.text},
        )


@dataclass
class ParsedBlock:
    lines: list[ParsedLine] = field(default_factory=list)
    problems: list[ParseProblem] = field(default_factory=list)

    @property
    def items(self) -> list[Item]:
        return [pl.item for pl in self.lines if pl.item is not None]

    @property
    def commands(self) -> list[Command]:
        return [i for i in self.items if isinstance(i, Command)]


class TokenizeError(ValueError):
    pass


def tokenize(text: str) -> list[str]:
    """Split on whitespace outside quotes and brackets; tokens keep their quotes."""
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    in_quotes = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_quotes:
            current.append(ch)
            if ch == "\\" and i + 1 < len(text):
                current.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_quotes = False
        elif ch == '"':
            in_quotes = True
            current.append(ch)
        elif ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth -= 1
            if depth < 0:
                raise TokenizeError("unbalanced ]")
            current.append(ch)
        elif ch.isspace() and depth == 0:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
        i += 1
    if in_quotes:
        raise TokenizeError("unterminated quote")
    if depth:
        raise TokenizeError("unbalanced [")
    if current:
        tokens.append("".join(current))
    return tokens


_ESCAPES = {'"': '"', "\\": "\\", "$": "$", "?": "?", "n": "\n", "t": "\t", "r": "\r"}


def unquote(value: str) -> str:
    if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        return value
    body = value[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            out.append(_ESCAPES.get(nxt, "\\" + nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_find(token: str) -> Find:
    inner = token[1:-1].strip()
    if not inner.startswith("find"):
        raise ValueError("selector is not a find expression")
    expression = inner[len("find"):].strip()
    if not expression:
        return Find()
    try:
        parts = tokenize(expression)
    except TokenizeError:
        return Find(raw=expression)
    conditions = []
    for part in parts:
        key, sep, value = part.partition("=")
        if not sep or not key or not key.replace("-", "").isalnum():
            return Find(raw=expression)
        conditions.append((key, unquote(value)))
    return Find(conditions=tuple(conditions))


def _parse_command(menu: str, tokens: list[str]) -> Command:
    verb, rest = tokens[0], tokens[1:]
    if verb not in VERBS:
        raise ValueError("unknown_verb")
    selector: Selector = None
    if rest and verb in _SELECTOR_VERBS:
        first = rest[0]
        if first.startswith("["):
            try:
                selector = _parse_find(first)
            except ValueError as exc:
                raise ValueError("bad_argument") from exc
            rest = rest[1:]
        elif "=" not in first:
            selector = int(first) if first.isdigit() else unquote(first)
            rest = rest[1:]
    args = []
    for token in rest:
        key, sep, value = token.partition("=")
        if not sep or not key:
            raise ValueError("bad_argument")
        args.append((key, unquote(value)))
    return Command(path=menu, verb=verb, args=tuple(args), selector=selector)


def _logical_lines(text: str) -> list[tuple[int, int, str]]:
    """Join export-style continuations (`...\\` + indented next line)."""
    physical = text.replace("\r\n", "\n").split("\n")
    result = []
    i = 0
    while i < len(physical):
        start = i
        line = physical[i].rstrip()
        while line.endswith("\\") and i + 1 < len(physical):
            i += 1
            line = line[:-1] + physical[i].strip()
        result.append((start, i - start + 1, line))
        i += 1
    return result


def parse(text: str) -> ParsedBlock:
    block = ParsedBlock()
    menu: str | None = None
    for line_no, count, raw in _logical_lines(text):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            block.lines.append(ParsedLine(line_no, count, menu, Comment(stripped[1:].strip())))
            continue
        if stripped.startswith(":"):
            block.lines.append(ParsedLine(line_no, count, menu, RawLine(stripped)))
            block.problems.append(ParseProblem(line_no, stripped, "scripting"))
            continue
        try:
            tokens = tokenize(stripped)
        except TokenizeError:
            block.lines.append(ParsedLine(line_no, count, menu, RawLine(stripped, menu)))
            block.problems.append(ParseProblem(line_no, stripped, "tokenize"))
            continue

        inline = False
        if tokens[0].startswith("/"):
            verb_at = next((n for n, t in enumerate(tokens) if t in VERBS), None)
            path_tokens = tokens if verb_at is None else tokens[:verb_at]
            menu = normalize_path(" ".join(path_tokens))
            if verb_at is None:
                block.lines.append(ParsedLine(line_no, count, menu, None))
                continue
            tokens = tokens[verb_at:]
            inline = True

        if menu is None:
            block.lines.append(ParsedLine(line_no, count, menu, RawLine(stripped)))
            block.problems.append(ParseProblem(line_no, stripped, "no_menu"))
            continue
        try:
            command = _parse_command(menu, tokens)
        except ValueError as exc:
            reason = str(exc) if str(exc) in {"unknown_verb", "bad_argument"} else "bad_argument"
            block.lines.append(ParsedLine(line_no, count, menu, RawLine(stripped, menu), inline))
            block.problems.append(ParseProblem(line_no, stripped, reason))
            continue
        block.lines.append(ParsedLine(line_no, count, menu, command, inline))
    return block
