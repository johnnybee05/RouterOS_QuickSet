"""Two-way binding between the hardening form fields and the raw block text.

The text stays the single source of truth. Reading parses it; writing rewrites only the
affected lines (or inserts them into the right menu), leaving everything else untouched.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mqs.generator.hardening.parser import ParsedLine, parse
from mqs.generator.ir import Command, Find, cmd
from mqs.generator.render import render_command, render_inline

DNS_MENU = "/ip dns"
SERVICE_MENU = "/ip service"
SNMP_MENU = "/snmp"
SNMP_COMMUNITY_MENU = "/snmp community"
CLOCK_MENU = "/system clock"
NTP_SERVERS_MENU = "/system ntp client servers"


@dataclass(frozen=True)
class HardeningFields:
    dns_servers: tuple[str, ...] | None = None
    ssh_port: int | None = None
    snmp_community: str | None = None
    ntp_servers: tuple[str, ...] | None = None
    time_zone: str | None = None


def _selector_matches(command: Command, selector: str | None) -> bool:
    if selector is None:
        return command.selector is None
    if command.selector == selector:
        return True
    return isinstance(command.selector, Find) and command.selector.get("name") == selector


def _command_lines(text: str) -> list[ParsedLine]:
    return [pl for pl in parse(text).lines if isinstance(pl.item, Command)]


def _find_line(
    lines: list[ParsedLine], menu: str, verb: str, selector: str | None, key: str | None
) -> ParsedLine | None:
    for pl in reversed(lines):
        command = pl.item
        assert isinstance(command, Command)
        if command.path != menu or command.verb != verb:
            continue
        if not _selector_matches(command, selector):
            continue
        if key is None or command.has_arg(key):
            return pl
    return None


def read_fields(text: str) -> HardeningFields:
    lines = _command_lines(text)

    def set_arg(menu: str, key: str, selector: str | None = None) -> str | None:
        pl = _find_line(lines, menu, "set", selector, key)
        return pl.item.arg(key) if pl else None  # type: ignore[union-attr]

    dns = set_arg(DNS_MENU, "servers")
    port = set_arg(SERVICE_MENU, "port", "ssh")
    community = next(
        (
            pl.item.arg("name")
            for pl in lines
            if pl.item.path == SNMP_COMMUNITY_MENU  # type: ignore[union-attr]
            and pl.item.verb == "add"  # type: ignore[union-attr]
            and pl.item.has_arg("name")  # type: ignore[union-attr]
        ),
        None,
    )
    ntp = [
        pl.item.arg("address")  # type: ignore[union-attr]
        for pl in lines
        if pl.item.path == NTP_SERVERS_MENU and pl.item.verb == "add"  # type: ignore[union-attr]
        and pl.item.has_arg("address")  # type: ignore[union-attr]
    ]
    return HardeningFields(
        dns_servers=tuple(s for s in dns.split(",") if s) if dns is not None else None,
        ssh_port=int(port) if port is not None and port.isdigit() else None,
        snmp_community=community,
        ntp_servers=tuple(a for a in ntp if a) if ntp else None,
        time_zone=set_arg(CLOCK_MENU, "time-zone-name"),
    )


def _replace(text_lines: list[str], pl: ParsedLine, new: Sequence[str]) -> None:
    text_lines[pl.line_no : pl.line_no + pl.line_count] = list(new)


def _insert_into_menu(text: str, menu: str, new_lines: Sequence[str]) -> str:
    """Insert command lines at the end of the last occurrence of `menu`, or append the menu."""
    text_lines = text.split("\n")
    block = parse(text)
    in_menu = [pl for pl in block.lines if pl.menu == menu]
    if in_menu:
        last = in_menu[-1]
        at = last.line_no + last.line_count
        text_lines[at:at] = list(new_lines)
        return "\n".join(text_lines)
    body = text.rstrip("\n")
    prefix = body + "\n" if body else ""
    return prefix + "\n".join([menu, *new_lines]) + "\n"


def _set_arg(text: str, menu: str, key: str, value: str | int, selector: str | None = None) -> str:
    lines = _command_lines(text)
    target = _find_line(lines, menu, "set", selector, key) or _find_line(
        lines, menu, "set", selector, None
    )
    if target is None:
        new = cmd(menu, "set", selector=selector, args={key: value})
        return _insert_into_menu(text, menu, [render_command(new)])
    command = target.item
    assert isinstance(command, Command)
    updated = command.with_args({key: value})
    text_lines = text.split("\n")
    rendered = render_inline(updated) if target.inline else render_command(updated)
    _replace(text_lines, target, [rendered])
    return "\n".join(text_lines)


def set_dns_servers(text: str, servers: Sequence[str]) -> str:
    return _set_arg(text, DNS_MENU, "servers", ",".join(servers))


def set_ssh_port(text: str, port: int) -> str:
    return _set_arg(text, SERVICE_MENU, "port", port, selector="ssh")


def set_time_zone(text: str, time_zone: str) -> str:
    return _set_arg(text, CLOCK_MENU, "time-zone-name", time_zone)


def set_snmp_community(text: str, name: str) -> str:
    lines = _command_lines(text)
    add_line = next(
        (
            pl
            for pl in lines
            if pl.item.path == SNMP_COMMUNITY_MENU  # type: ignore[union-attr]
            and pl.item.verb == "add"  # type: ignore[union-attr]
            and pl.item.has_arg("name")  # type: ignore[union-attr]
        ),
        None,
    )
    if add_line is None:
        new = cmd(SNMP_COMMUNITY_MENU, "add", addresses="::/0", name=name)
        text = _insert_into_menu(text, SNMP_COMMUNITY_MENU, [render_command(new)])
    else:
        command = add_line.item
        assert isinstance(command, Command)
        updated = command.with_args(name=name)
        text_lines = text.split("\n")
        rendered = render_inline(updated) if add_line.inline else render_command(updated)
        _replace(text_lines, add_line, [rendered])
        text = "\n".join(text_lines)
    return _set_arg(text, SNMP_MENU, "trap-community", name)


def set_ntp_servers(text: str, servers: Sequence[str]) -> str:
    new_lines = [render_command(cmd(NTP_SERVERS_MENU, "add", address=s)) for s in servers]
    existing = [
        pl
        for pl in _command_lines(text)
        if pl.item.path == NTP_SERVERS_MENU and pl.item.verb == "add"  # type: ignore[union-attr]
    ]
    if not existing:
        return _insert_into_menu(text, NTP_SERVERS_MENU, new_lines) if new_lines else text
    text_lines = text.split("\n")
    first = existing[0]
    for pl in reversed(existing[1:]):
        _replace(text_lines, pl, [])
    _replace(text_lines, first, new_lines)
    return "\n".join(text_lines)
