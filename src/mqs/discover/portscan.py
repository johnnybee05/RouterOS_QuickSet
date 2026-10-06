"""Knocking on the ports a MikroTik usually answers on, slowly and on purpose.

This is the weakest source in the set and the only one that is off by default. A port
that answers proves nothing — 8291 is Winbox by convention, anyone may listen on 8729,
and 22 is every device on the network. What a scan is actually good for is *where to try
logging in*: a subnet full of antennas that have discovery turned off will show up here
and nowhere else.

It is also the one source that touches machines nobody asked about, so it is rate limited
by default and takes the subnets it is told rather than guessing any. A survey of a
customer's network should not look like something else.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network, ip_network

from mqs.discover.candidates import Sighting, Source

# The ports worth knocking on, and what each one usually is. None of them is evidence.
API_SSL = 8729
SSH = 122  # the port this application's own hardening moves SSH to
SSH_DEFAULT = 22
WINBOX = 8291
DEFAULT_PORTS = (API_SSL, SSH, SSH_DEFAULT, WINBOX)
DEFAULT_TIMEOUT_S = 0.5
DEFAULT_WORKERS = 16
# Addresses per second, across all workers. Deliberately unhurried: a /24 takes under a
# minute at this rate and nothing downstream notices it happening.
DEFAULT_RATE = 50.0
MAX_ADDRESSES = 4096  # a /20. Beyond this somebody has typed the wrong prefix.


class TooManyAddresses(ValueError):
    """The subnets add up to more than a scan should ever be pointed at by accident."""


@dataclass(frozen=True)
class OpenPort:
    address: str
    port: int


@dataclass
class ScanLimits:
    ports: tuple[int, ...] = DEFAULT_PORTS
    timeout_s: float = DEFAULT_TIMEOUT_S
    workers: int = DEFAULT_WORKERS
    rate: float = DEFAULT_RATE  # addresses per second, 0 = as fast as it will go
    max_addresses: int = MAX_ADDRESSES


def addresses(subnets: Iterable[str], *, limit: int = MAX_ADDRESSES) -> list[IPv4Address]:
    """Every host address in ``subnets``, refused outright if there are too many.

    A single host is written as an address and means that address; `/32` works too. The
    limit is the guard against a typo turning a survey of one office into a scan of a /8.
    """
    found: list[IPv4Address] = []
    seen: set[IPv4Address] = set()
    for text in subnets:
        network = _network(text)
        # Asked of the prefix, not of a list built from it: enumerating a /8 to find out
        # it is too big means sixteen million addresses in memory first, and a window
        # that stops answering while that happens.
        if network.num_addresses > limit:
            raise TooManyAddresses(
                f"{text} holds {network.num_addresses} addresses, more than the limit "
                f"of {limit}; name smaller subnets")
        for host in network.hosts() or [network.network_address]:
            if host in seen:
                continue
            seen.add(host)
            found.append(host)
            if len(found) > limit:
                raise TooManyAddresses(
                    f"{len(found)} addresses in all is more than the limit of {limit}; "
                    "name fewer subnets")
    return found


def _network(text: str) -> IPv4Network:
    try:
        return ip_network(text.strip(), strict=False)  # type: ignore[return-value]
    except ValueError as exc:
        raise TooManyAddresses(f"not a subnet or address: {text!r}") from exc


def _knock(address: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((address, port), timeout=timeout_s):
            return True
    except (OSError, ValueError):
        return False


@dataclass
class _Pacer:
    """Keeps the scan to its rate without a sleep per worker."""

    rate: float
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic
    _next: float = field(default=0.0)

    def wait(self) -> None:
        if self.rate <= 0:
            return
        moment = self.now()
        if self._next and moment < self._next:
            self.sleep(self._next - moment)
            moment = self._next
        self._next = moment + 1.0 / self.rate


def scan(
    subnets: Sequence[str],
    limits: ScanLimits | None = None,
    *,
    knock: Callable[[str, int, float], bool] = _knock,
    on_progress: Callable[[int, int], None] = lambda done, total: None,
    cancelled: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> list[OpenPort]:
    """Which of ``subnets`` answer on which ports, paced and interruptible.

    An address is dropped as soon as one of its ports answers: the point is to find
    somewhere to try logging in, and a second open port on the same host adds nothing to
    that.
    """
    limits = limits or ScanLimits()
    hosts = addresses(subnets, limit=limits.max_addresses)
    pacer = _Pacer(limits.rate, sleep=sleep, now=now)
    found: list[OpenPort] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, limits.workers)) as pool:
        for result in pool.map(
            lambda host: _first_open(str(host), limits, knock, pacer, cancelled),
            hosts,
        ):
            done += 1
            on_progress(done, len(hosts))
            if result is not None:
                found.append(result)
    return found


def _first_open(address: str, limits: ScanLimits,
                knock: Callable[[str, int, float], bool], pacer: _Pacer,
                cancelled: Callable[[], bool]) -> OpenPort | None:
    if cancelled():
        return None
    pacer.wait()
    for port in limits.ports:
        if cancelled():
            return None
        if knock(address, port, limits.timeout_s):
            return OpenPort(address, port)
    return None


def to_sightings(open_ports: Iterable[OpenPort], *, found_at: str = "") -> list[Sighting]:
    """Each answering address as a candidate that proves nothing.

    `Source.SCAN` does not count towards being a MikroTik, so these stay UNKNOWN until
    some other source — or a login — says otherwise. That is the whole point of keeping
    them: they are the list of addresses worth trying.
    """
    return [Sighting(source=Source.SCAN, address=port.address, found_at=found_at,
                     note=f"port {port.port}")
            for port in open_ports]
