"""Common transport interface for RouterOS API-SSL and SSH."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from mqs.generator.ir import Command, Find

Reply = list[dict[str, str]]

DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_COMMAND_TIMEOUT = 30.0
API_SSL_PORT = 8729
SSH_PORT = 22


class TransportKind(StrEnum):
    API_SSL = "api-ssl"
    SSH = "ssh"


class PeerSecurity(StrEnum):
    ANONYMOUS_DH = "anonymous-dh"  # UI: "nešifrovaná identita protistrany"
    VERIFIED = "verified"  # UI: "ověřený certifikát"
    SSH_HOST_KEY = "ssh-host-key"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConnectionParams:
    host: str
    username: str
    password: str = field(default="", repr=False)  # memory only, never persisted
    port: int | None = None  # None = transport default
    key_filename: str | None = None
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT
    ca_file: str | None = None
    allow_anonymous_dh: bool = True


class TransportError(Exception):
    """Base class; `str(error)` is a technical English message for logs."""


class ServiceUnavailable(TransportError):
    """Port closed / connection refused / handshake impossible — try another transport."""


class AuthError(TransportError):
    pass


class ConnectTimeout(TransportError):
    pass


class CommandTimeout(TransportError):
    pass


class ConnectionLost(TransportError):
    pass


class Cancelled(TransportError):
    pass


class CommandError(TransportError):
    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


ROOT_MENU = "/"


def split_path(path: str) -> tuple[str, str]:
    """'/system/resource/print' or '/system resource print' -> ('/system resource', 'print').

    Root commands such as '/export' or '/import' return the menu '/'.
    """
    parts = path.replace("/", " ").split()
    if not parts:
        raise ValueError(f"path must contain a command: {path!r}")
    if len(parts) == 1:
        return ROOT_MENU, parts[0]
    return "/" + " ".join(parts[:-1]), parts[-1]


def api_word(menu: str, verb: str) -> str:
    """API word: ('/ip service', 'set') -> '/ip/service/set'; ('/', 'export') -> '/export'."""
    if menu == ROOT_MENU:
        return "/" + verb
    return menu.replace(" ", "/") + "/" + verb


def args_from_kwargs(kwargs: dict[str, object]) -> dict[str, str]:
    """Python keyword names use '_' where RouterOS uses '-'; booleans become yes/no."""
    result: dict[str, str] = {}
    for key, value in kwargs.items():
        name = key.replace("_", "-")
        if isinstance(value, bool):
            result[name] = "yes" if value else "no"
        else:
            result[name] = str(value)
    return result


@runtime_checkable
class Transport(Protocol):
    kind: TransportKind
    params: ConnectionParams

    @property
    def connected(self) -> bool: ...

    @property
    def peer_security(self) -> PeerSecurity: ...

    def connect(self) -> None: ...

    # `path` is positional-only: RouterOS commands may take an argument named `path`.
    def execute(self, path: str, /, **args: object) -> Reply: ...

    def execute_command(self, command: Command) -> Reply: ...

    def close(self) -> None: ...


def command_selector_args(command: Command) -> dict[str, str]:
    """Selector of an IR command expressed as plain args (item name / numbers)."""
    selector = command.selector
    if selector is None:
        return {}
    if isinstance(selector, Find):
        raise ValueError("find selectors must be resolved to .id by the transport")
    return {"numbers": str(selector)}
