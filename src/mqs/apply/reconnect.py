"""Reconnect after the SSH port changed or the device rebooted."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from mqs.model.bridge import LanAddressing
from mqs.model.profile import DeviceProfile
from mqs.model.wan import WanType
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
    alternatives: tuple[str, ...] = ()  # further addresses to try, in order

    @property
    def hosts(self) -> tuple[str, ...]:
        return (self.params.host, *self.alternatives)


def _static_lan(profile: DeviceProfile) -> str | None:
    if profile.lan.addressing != LanAddressing.STATIC or profile.lan.address is None:
        return None
    return str(profile.lan.address.ip)


def _static_wan(profile: DeviceProfile) -> str | None:
    if not profile.wan.enabled or profile.wan.settings.type != WanType.STATIC:
        return None
    return str(profile.wan.settings.address.ip)


def address_after_apply(profile: DeviceProfile, port: str | None) -> str | None:
    """The address the generated configuration puts on `port`, if it can be known."""
    if not port:
        return None
    if profile.wan.enabled and port == profile.wan.interface:
        return _static_wan(profile)  # DHCP and PPPoE addresses cannot be predicted
    if port == profile.bridge.name or port in profile.bridge.ports:
        return _static_lan(profile)
    return None


def reconnect_hosts(
    profile: DeviceProfile,
    *,
    current_host: str,
    session_port: str | None = None,
    expiring: Sequence[str] = (),
) -> list[str]:
    """Addresses worth trying after the apply, likeliest first.

    The address in use is the obvious first choice and usually right, but not always: a
    reset wipes every address the device had, including the temporary one connect-by-MAC
    adds to reach a device that had none. That address is then the one address certain
    not to answer, so `expiring` moves it to the back of the queue - back, not away,
    because when nothing else can be predicted it is still the only thing left to try.
    """
    gone = {address.split("/")[0] for address in expiring if address}
    candidates = [
        address_after_apply(profile, session_port),  # what our own port will carry
        current_host,
        _static_lan(profile),
        _static_wan(profile),
    ]
    ranked = [h for h in candidates if h and h not in gone]
    ranked += [h for h in candidates if h and h in gone]
    return list(dict.fromkeys(ranked))


class ReconnectFailed(TransportError):
    def __init__(self, target: ReconnectTarget, attempts: int, last_error: str) -> None:
        super().__init__(
            f"reconnect to {', '.join(target.hosts)} failed after {attempts} attempts "
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
    """AUTO transport (API-SSL first, then SSH on the new port) until `timeout_s` elapses.

    Every address in `target.hosts` is tried before backing off, so a round costs one
    sweep and the first address that answers wins. A refused login does not abort the
    sweep - the address may belong to something else entirely now - but it is what gets
    reported if no address works, because "wrong password" is the more useful answer.
    """
    start = clock()
    delay = initial_delay_s
    attempt = 0
    while True:
        attempt += 1
        auth_error: AuthError | None = None
        for host in target.hosts:
            try:
                outcome = connect(replace(target.params, host=host), TransportChoice.AUTO,
                                  api_port=target.api_port, ssh_port=target.ssh_port)
                return outcome.transport
            except AuthError as exc:
                auth_error = exc
                last_error = f"{host}: {exc}"
                on_attempt(attempt, last_error)
            except (ConnectFailed, ServiceUnavailable, ConnectTimeout) as exc:
                last_error = f"{host}: {exc}"
                on_attempt(attempt, last_error)
        if auth_error is not None:
            raise auth_error  # credentials are not going to get better by waiting
        if clock() - start + delay > timeout_s:
            raise ReconnectFailed(target, attempt, last_error)
        if cancelled():
            raise Cancelled("reconnect cancelled")
        sleep(delay)
        delay = min(delay * 1.5, max_delay_s)
