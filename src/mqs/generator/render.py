"""Serialise IR to RouterOS script text."""

from __future__ import annotations

import re

from mqs.generator.ir import Command, Comment, Find, RawLine, Script

# Values made only of these characters are emitted without quotes, like /export does.
# The '?' and '$' escapes inside quotes are NUTNO OVĚŘIT (docs/DIALECT_NOTES.md).
_BARE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:/,!-]+$")


def quote(value: str) -> str:
    if _BARE_VALUE_RE.match(value):
        return value
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("?", "\\?")
    )
    return f'"{escaped}"'


def render_selector(selector: Find | int | str | None) -> str | None:
    if selector is None:
        return None
    if isinstance(selector, Find):
        if selector.raw is not None:
            return f"[ find {selector.raw} ]"
        conditions = " ".join(f"{k}={quote(v)}" for k, v in selector.conditions)
        return f"[ find {conditions} ]" if conditions else "[ find ]"
    if isinstance(selector, int):
        return str(selector)
    return quote(selector)


def render_command(command: Command) -> str:
    """Command without its menu path: `set [ find default=yes ] disabled=yes`."""
    parts = [command.verb]
    selector = render_selector(command.selector)
    if selector is not None:
        parts.append(selector)
    parts.extend(f"{k}={quote(v)}" for k, v in command.args)
    return " ".join(parts)


def render_inline(item: Command | RawLine) -> str:
    """Single self-contained line including the menu path (for SSH / terminal paste)."""
    if isinstance(item, Command):
        return f"{item.path} {render_command(item)}"
    return f"{item.path} {item.text}" if item.path else item.text


def render_script(script: Script, header: str | None = None) -> str:
    lines: list[str] = []
    if header:
        lines.extend(f"# {line}".rstrip() for line in header.splitlines())
    for section in script.sections:
        if not section.items:
            continue
        lines.append(f"# ---- {section.title} ----")
        current_path: str | None = None  # every section restates its menu path
        for item in section.items:
            if isinstance(item, Comment):
                lines.append(f"# {item.text}".rstrip())
            elif isinstance(item, RawLine):
                if item.path and item.path != current_path:
                    lines.append(item.path)
                    current_path = item.path
                lines.append(item.text)
            else:
                if item.path != current_path:
                    lines.append(item.path)
                    current_path = item.path
                lines.append(render_command(item))
    return "\n".join(lines) + "\n"
