"""In-memory transport returning canned replies, shared by discovery/apply tests."""

from __future__ import annotations

from mqs.generator.ir import Command
from mqs.transport.base import (
    CommandError,
    ConnectionParams,
    PeerSecurity,
    Reply,
    TransportKind,
    split_path,
)


class FakeTransport:
    def __init__(self, replies: dict[str, Reply] | None = None,
                 kind: TransportKind = TransportKind.API_SSL) -> None:
        self.kind = kind
        self.params = ConnectionParams(host="192.0.2.1", username="admin")
        self.replies = replies or {}
        self.calls: list[tuple[str, dict]] = []
        self.commands: list[Command] = []
        self._connected = False

    @property
    def port(self) -> int:
        return 8729 if self.kind == TransportKind.API_SSL else 22

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def peer_security(self) -> PeerSecurity:
        return PeerSecurity.ANONYMOUS_DH

    def connect(self) -> None:
        self._connected = True

    def close(self) -> None:
        self._connected = False

    def execute(self, path: str, /, **args: object) -> Reply:
        menu, verb = split_path(path)
        key = f"{menu.replace(' ', '/')}/{verb}"
        self.calls.append((key, dict(args)))
        if key not in self.replies:
            raise CommandError(key, "bad command name")
        return [dict(r) for r in self.replies[key]]

    def execute_command(self, command: Command) -> Reply:
        self.commands.append(command)
        return []
