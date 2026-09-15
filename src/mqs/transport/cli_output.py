"""Sanitising and parsing RouterOS CLI output received over SSH."""

from __future__ import annotations

import re

# CSI sequences, OSC sequences (terminated by BEL or ST) and two-character escapes.
_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Error markers RouterOS prints instead of failing the exec channel. Single source of truth;
# the exact wording differs between versions (NUTNO OVĚŘIT).
ERROR_PATTERNS = (
    re.compile(r"^\s*failure: .*$", re.MULTILINE),
    re.compile(r"^\s*bad command name .*$", re.MULTILINE),
    re.compile(r"^\s*syntax error .*$", re.MULTILINE),
    re.compile(r"^\s*expected .*$", re.MULTILINE),
    re.compile(r"^\s*no such item.*$", re.MULTILINE),
    re.compile(r"^\s*input does not match any value of .*$", re.MULTILINE),
    re.compile(r"^\s*invalid value .*$", re.MULTILINE),
    re.compile(r"^\s*ambiguous value .*$", re.MULTILINE),
    re.compile(r"^\s*value of .* out of range.*$", re.MULTILINE),
)


def sanitize(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n")
    # A lone CR means the terminal rewrote the line; keep only the last rewrite.
    lines = [line.split("\r")[-1] for line in text.split("\n")]
    text = "\n".join(line.rstrip() for line in lines)
    return _CONTROL_RE.sub("", text)


def find_error(text: str) -> str | None:
    for pattern in ERROR_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


_TERSE_PREFIX_RE = re.compile(
    r"^\s*(?P<index>\d+)\s+(?P<flags>(?:[A-Z*]\s*)*?)\s*(?=[a-z][\w.-]*=)"
)
_TERSE_KEY_RE = re.compile(r"(?:^|\s)(?=[a-z][\w.-]*=)")


def parse_terse(text: str) -> list[dict[str, str]]:
    """Parse `print terse` output: one item per line, `key=value` pairs, leading index/flags.

    Values may contain spaces; a new pair starts only where `name=` follows whitespace.
    """
    items: list[dict[str, str]] = []
    for raw in sanitize(text).split("\n"):
        line = raw.strip()
        if not line or line.startswith(("Flags:", "#", ";;;")):
            continue
        item: dict[str, str] = {}
        prefix = _TERSE_PREFIX_RE.match(raw)
        if prefix:
            item["_index"] = prefix.group("index")
            item["_flags"] = "".join(prefix.group("flags").split())
            line = raw[prefix.end():].strip()
        for chunk in _TERSE_KEY_RE.split(line):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            key, _, value = chunk.partition("=")
            item[key] = value
        if len(item) > ("_index" in item) + ("_flags" in item):
            items.append(item)
    return items


_KV_RE = re.compile(r"^\s*(?P<key>[a-z][\w.-]*):\s?(?P<value>.*)$")


def parse_key_value(text: str) -> dict[str, str]:
    """Parse singleton `print` output (`/system resource print`): `  key: value` lines."""
    result: dict[str, str] = {}
    last: str | None = None
    for line in sanitize(text).split("\n"):
        if not line.strip():
            continue
        match = _KV_RE.match(line)
        if match:
            last = match.group("key")
            result[last] = match.group("value").strip()
        elif last is not None:
            result[last] = f"{result[last]} {line.strip()}".strip()
    return result
