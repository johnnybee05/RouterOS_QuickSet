"""Transport selection: API-SSL by default with automatic fallback to SSH."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from mqs.transport.api_ssl import ApiSslTransport
from mqs.transport.base import (
    AuthError,
    Cancelled,
    ConnectionParams,
    ConnectTimeout,
    ServiceUnavailable,
    Transport,
    TransportError,
    TransportKind,
)
from mqs.transport.ssh import SshTransport


class TransportChoice(StrEnum):
    AUTO = "auto"  # API-SSL, fall back to SSH when api-ssl is unreachable
    API_SSL = "api-ssl"
    SSH = "ssh"


@dataclass(frozen=True)
class ConnectAttempt:
    kind: TransportKind
    port: int
    error: str | None = None


@dataclass
class ConnectOutcome:
    transport: Transport
    attempts: list[ConnectAttempt] = field(default_factory=list)

    @property
    def fell_back(self) -> bool:
        return self.transport.kind == TransportKind.SSH and any(
            a.kind == TransportKind.API_SSL for a in self.attempts
        )


class ConnectFailed(TransportError):
    def __init__(self, attempts: list[ConnectAttempt]) -> None:
        details = "; ".join(f"{a.kind.value}:{a.port} {a.error}" for a in attempts)
        super().__init__(f"no transport available ({details})")
        self.attempts = attempts


def connect_transport(
    params: ConnectionParams,
    choice: TransportChoice = TransportChoice.AUTO,
    *,
    api_port: int | None = None,
    ssh_port: int | None = None,
    api_factory: Callable[[ConnectionParams], Any] = ApiSslTransport,
    ssh_factory: Callable[[ConnectionParams], Any] = SshTransport,
    cancelled: Callable[[], bool] = lambda: False,
) -> ConnectOutcome:
    """Connect with the chosen transport. Authentication errors are not retried on another
    transport with the same credentials, unreachable services are.

    With an SSH key the credentials are not the same: api-ssl has no key login and gets
    only the password, which a key-only account may not even have. A refusal there then
    still leaves SSH to try, and is what gets raised if SSH does not answer either.
    """
    plan: list[tuple[TransportKind, Callable[[ConnectionParams], Any], int | None]] = []
    if choice in (TransportChoice.AUTO, TransportChoice.API_SSL):
        plan.append((TransportKind.API_SSL, api_factory, api_port))
    if choice in (TransportChoice.AUTO, TransportChoice.SSH):
        plan.append((TransportKind.SSH, ssh_factory, ssh_port))

    attempts: list[ConnectAttempt] = []
    refused: AuthError | None = None
    for kind, factory, port in plan:
        if cancelled():
            raise Cancelled("connect cancelled")
        transport = factory(replace(params, port=port))
        try:
            transport.connect()
        except AuthError as exc:
            if kind != TransportKind.API_SSL or not params.key_filename:
                raise
            attempts.append(ConnectAttempt(kind, transport.port, str(exc)))
            refused = exc
            continue
        except (ServiceUnavailable, ConnectTimeout) as exc:
            attempts.append(ConnectAttempt(kind, transport.port, str(exc)))
            continue
        attempts.append(ConnectAttempt(kind, transport.port))
        return ConnectOutcome(transport, attempts)
    if refused is not None:
        raise refused
    raise ConnectFailed(attempts)
