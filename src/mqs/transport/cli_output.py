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
    # A lone CR means the terminal rewrote the line; keep only the last rewrite. A CR at
    # the end rewrites nothing: RouterOS 6.49.10 ends console lines with CR CR LF, and
    # taking what followed that CR as the rewrite emptied every line (2026-09-25).
    lines = [line.rstrip("\r").split("\r")[-1] for line in text.split("\n")]
    text = "\n".join(line.rstrip() for line in lines)
    return _CONTROL_RE.sub("", text)


def find_error(text: str) -> str | None:
    for pattern in ERROR_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


# A flag is one character and may be lower case: 7.20.7 marks an open connection in
# /ip service with "c" ("8 D c name=ssh ..."). Flags from adjacent columns can also run
# together: an inactive bridge port with hw offload is "0 IH interface=ether2 ..." there.
_TERSE_PREFIX_RE = re.compile(
    r"^\s*(?P<index>\d+)\s+(?P<flags>(?:[A-Za-z*]+(?=\s)\s*)*?)\s*(?=[a-z][\w.-]*=)"
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
            # RouterOS 6.49.10 writes an empty value as "" (address="" in /ip service),
            # while a value with spaces stays unquoted (comment=contains all interfaces).
            # Taken literally, every service looked limited to the address '""'.
            item[key] = "" if value == '""' else value
        if len(item) > ("_index" in item) + ("_flags" in item):
            items.append(item)
    return items


# "20:08:06 script,error script error: …", "sep/02 06:09:53 …", "jan/02/1970 01:00:46 …",
# "2026-09-24 21:05:31 …": an optional date, the time, the topics, the message.
_LOG_LINE_RE = re.compile(
    r"^\s*(?P<time>(?:[a-z]{3}/\d{2}(?:/\d{4})?\s+|\d{4}-\d{2}-\d{2}\s+)?\d{2}:\d{2}:\d{2})"
    r"\s+(?P<topics>[a-z0-9-]+(?:,[a-z0-9-]+)*)\s+(?P<message>.*?)\s*$"
)


def parse_log(text: str) -> list[dict[str, str]]:
    """`/log print` lines as rows with time, topics and message, in the log's order.

    RouterOS 6.49.10 prints its log the same way with and without `terse`, not as
    key=value pairs (2026-09-25), so parse_terse finds no message in it.
    """
    rows = []
    for line in sanitize(text).split("\n"):
        match = _LOG_LINE_RE.match(line)
        if match:
            rows.append(match.groupdict())
    return rows


def parse_columns(text: str) -> list[dict[str, str]]:
    """Parse a column table (`/console inspect` over SSH on 7.20.7):

        Columns: TYPE, NAME, NODE-TYPE
        TYPE   NAME        NODE-TYPE
        child  hw-offload  arg

    Cells are cut at the header's column offsets, so an empty cell does not shift the rest.
    Keys are the lower-cased column names.
    """
    lines = sanitize(text).split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith("Columns:")), None)
    if start is None or start + 1 >= len(lines):
        return []
    names = [n.strip() for n in lines[start][len("Columns:"):].split(",")]
    header = lines[start + 1]
    offsets = [header.find(name) for name in names]
    if any(o < 0 for o in offsets):
        return []
    bounds = [*offsets[1:], None]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2:]:
        if not line.strip():
            continue
        rows.append({name.lower(): line[begin:end].strip()
                     for name, begin, end in zip(names, offsets, bounds, strict=True)})
    return rows


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
