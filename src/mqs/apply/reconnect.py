"""Reconnect after the SSH port changed or the device rebooted."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mqs.transport.base import (
    AuthError,
    Cancelled,
    ConnectionParams,
    ConnectTimeout,
    ServiceUnavailable,
    Transport,
    TransportError,
)
from mqs.transport.factory import ConnectFailed, TransportChoice, connect_transport


@dataclass(frozen=True)
class ReconnectTarget:
    params: ConnectionParams
    api_port: int
    ssh_port: int


class ReconnectFailed(TransportError):
    def __init__(self, target: ReconnectTarget, attempts: int, last_error: str) -> None:
        super().__init__(
            f"reconnect to {target.params.host} failed after {attempts} attempts "
            f"(api-ssl {target.api_port}, ssh {target.ssh_port}): {last_error}"
        )
        self.target = target
        self.attempts = attempts
        self.last_error = last_error


def reconnect_with_retry(
    target: ReconnectTarget,
    *,
    timeout_s: float,
    connect: Callable[..., Any] = connect_transport,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    cancelled: Callable[[], bool] = lambda: False,
    on_attempt: Callable[[int, str], None] = lambda attempt, error: None,
    initial_delay_s: float = 2.0,
    max_delay_s: float = 15.0,
) -> Transport:
    """AUTO transport (API-SSL first, then SSH on the new port) until `timeout_s` elapses."""
    start = clock()
    delay = initial_delay_s
    attempt = 0
    while True:
        attempt += 1
        try:
            outcome = connect(target.params, TransportChoice.AUTO,
                              api_port=target.api_port, ssh_port=target.ssh_port)
            return outcome.transport
        except AuthError:
            raise
        except (ConnectFailed, ServiceUnavailable, ConnectTimeout) as exc:
            last_error = str(exc)
            on_attempt(attempt, last_error)
        if clock() - start + delay > timeout_s:
            raise ReconnectFailed(target, attempt, last_error)
        if cancelled():
            raise Cancelled("reconnect cancelled")
        sleep(delay)
        delay = min(delay * 1.5, max_delay_s)
