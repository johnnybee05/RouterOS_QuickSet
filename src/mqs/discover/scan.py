"""Running the sources together, and not letting the slowest one decide how long it takes.

Each source is a thread with its own deadline. A subnet scan that takes two minutes must
not hold up an MNDP listen that was done in five seconds, and a source that hangs
entirely must cost its own timeout and nothing else — the whole reason for having several
is that one can fail.

What comes out is candidates, merged, with every source that saw each device recorded on
it. Logging in is a separate step and an explicit one: it is the only thing that produces
a `CONFIRMED` device, it is the only thing an update may be planned from, and it is the
only part of detection that uses credentials.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from mqs.discover.candidates import (
    Candidate,
    Sighting,
    Source,
    confirmed,
    from_sighting,
    merge_into,
    refused,
)
from mqs.discover.merge import address_conflicts, merge_candidates, merge_sightings
from mqs.discover.portscan import ScanLimits, TooManyAddresses
from mqs.discover.portscan import scan as scan_ports
from mqs.discover.portscan import to_sightings as ports_to_sightings
from mqs.discover.snmp import query as snmp_query
from mqs.discover.snmp import to_sighting as snmp_to_sighting
from mqs.discover.sources import from_mndp

DEFAULT_MNDP_SECONDS = 8.0
DEFAULT_SOURCE_TIMEOUT_S = 180.0
DEFAULT_SNMP_WORKERS = 16


@dataclass
class ScanPlan:
    """Which sources to use. Each is off unless it is asked for, except MNDP.

    MNDP is on by default because it is passive, local and costs nothing: it listens on a
    port the devices are already shouting at. Everything else either needs an address
    range, a community string or credentials, which means somebody decided.
    """

    mndp: bool = True
    mndp_seconds: float = DEFAULT_MNDP_SECONDS
    subnets: tuple[str, ...] = ()
    scan_ports: bool = False
    scan_limits: ScanLimits = field(default_factory=ScanLimits)
    snmp_community: str = ""
    snmp_timeout_s: float = 2.0
    source_timeout_s: float = DEFAULT_SOURCE_TIMEOUT_S

    @property
    def snmp(self) -> bool:
        return bool(self.snmp_community and self.subnets)

    @property
    def anything(self) -> bool:
        return self.mndp or self.scan_ports or self.snmp


@dataclass(frozen=True)
class SourceResult:
    source: Source
    sightings: tuple[Sighting, ...] = ()
    error: str = ""
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass(frozen=True)
class ScanResult:
    devices: tuple[Candidate, ...] = ()
    results: tuple[SourceResult, ...] = ()

    @property
    def failures(self) -> tuple[SourceResult, ...]:
        return tuple(r for r in self.results if not r.ok)

    def of(self, source: Source) -> SourceResult | None:
        return next((r for r in self.results if r.source is source), None)


# ------------------------------------------------------------------ the sources


def mndp_sightings(seconds: float, scanner_factory: Callable[[], object],
                   sleep: Callable[[float], None] = time.sleep) -> list[Sighting]:
    """Listen for announcements, and ask for them once at the start.

    Soliciting is what turns an eight-second listen into a useful one: without it the
    wait is however long the slowest device's announcement interval happens to be.
    """
    scanner = scanner_factory()
    with scanner:  # type: ignore[attr-defined]
        scanner.open()  # type: ignore[attr-defined]
        scanner.solicit()  # type: ignore[attr-defined]
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            scanner.poll(0.5)  # type: ignore[attr-defined]
        devices = scanner.devices()  # type: ignore[attr-defined]
    return [from_mndp(device) for device in devices]


def snmp_sightings(addresses: Sequence[str], community: str, *, timeout_s: float,
                   workers: int = DEFAULT_SNMP_WORKERS,
                   ask: Callable[..., object] = snmp_query) -> list[Sighting]:
    """One GET per address, in parallel. Nothing answering is the normal case."""
    if not addresses:
        return []
    found: list[Sighting] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for reply in pool.map(lambda a: ask(a, community, timeout_s=timeout_s), addresses):
            if reply is not None:
                found.append(snmp_to_sighting(reply))  # type: ignore[arg-type]
    return found


# ------------------------------------------------------------------ running them


def run(
    plan: ScanPlan,
    *,
    mndp_factory: Callable[[], object] | None = None,
    knock: Callable[[str, int, float], bool] | None = None,
    snmp_ask: Callable[..., object] = snmp_query,
    on_source: Callable[[Source, str], None] = lambda source, state: None,
    cancelled: Callable[[], bool] = lambda: False,
    now: Callable[[], float] = time.time,
) -> ScanResult:
    """Every enabled source at once, each with its own deadline.

    A source that raises is a failed source and not a failed scan: its error is recorded
    on the result and the others are still merged. A source that overruns its deadline is
    abandoned — the thread may still be running, which is why nothing here writes to
    shared state.
    """
    jobs: dict[Source, Callable[[], list[Sighting]]] = {}

    if plan.mndp and mndp_factory is not None:
        jobs[Source.MNDP] = lambda: mndp_sightings(plan.mndp_seconds, mndp_factory)
    if plan.scan_ports and plan.subnets:
        def ports() -> list[Sighting]:
            open_ports = scan_ports(
                list(plan.subnets), plan.scan_limits,
                **({"knock": knock} if knock else {}), cancelled=cancelled)
            return ports_to_sightings(open_ports, found_at=", ".join(plan.subnets))
        jobs[Source.SCAN] = ports
    if plan.snmp:
        def snmp() -> list[Sighting]:
            from mqs.discover.portscan import addresses as hosts
            targets = [str(a) for a in hosts(list(plan.subnets),
                                             limit=plan.scan_limits.max_addresses)]
            return snmp_sightings(targets, plan.snmp_community,
                                  timeout_s=plan.snmp_timeout_s, ask=snmp_ask)
        jobs[Source.SNMP] = snmp

    results = _run_jobs(jobs, plan.source_timeout_s, on_source, now)
    sightings = [s for result in results for s in result.sightings]
    devices = address_conflicts(merge_sightings(sightings))
    stamp = now()
    devices = [d.model_copy(update={"last_seen": stamp}) for d in devices]
    return ScanResult(tuple(devices), tuple(results))


def _run_jobs(jobs: dict[Source, Callable[[], list[Sighting]]], timeout_s: float,
              on_source: Callable[[Source, str], None],
              now: Callable[[], float]) -> list[SourceResult]:
    if not jobs:
        return []
    results: list[SourceResult] = []
    started = {source: now() for source in jobs}
    # Deliberately not a `with`: leaving the pool would wait for every thread, including
    # the one that has already overrun its deadline, and the timeout below would mean
    # nothing. The pool is shut down without waiting instead, and a straggler is simply
    # abandoned — which is why nothing in a source writes to anything shared.
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    try:
        futures = {pool.submit(job): source for source, job in jobs.items()}
        for source in jobs:
            on_source(source, "running")
        pending = dict(futures)
        try:
            for future in as_completed(futures, timeout=timeout_s):
                source = futures[future]
                pending.pop(future, None)
                try:
                    found = future.result()
                except (OSError, ValueError, TooManyAddresses) as exc:
                    results.append(SourceResult(source, (), str(exc),
                                                now() - started[source]))
                    on_source(source, "failed")
                    continue
                results.append(SourceResult(source, tuple(found), "",
                                            now() - started[source]))
                on_source(source, "ok")
        except TimeoutError:
            pass
        for future, source in pending.items():
            future.cancel()
            results.append(SourceResult(source, (), f"no answer in {timeout_s:.0f} s",
                                        now() - started[source]))
            on_source(source, "timed out")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return sorted(results, key=lambda r: r.source.value)


# ------------------------------------------------------------------ confirming


def confirm(
    devices: Sequence[Candidate],
    open_device: Callable[[Candidate], object],
    *,
    on_device: Callable[[Candidate, str], None] = lambda device, state: None,
    skip_failed: bool = True,
) -> list[Candidate]:
    """Log in to each device and read it: the only step that produces a CONFIRMED device.

    A device whose login was already refused is skipped by default rather than tried
    again — a device that locks an account out after three goes is worse off for the
    retry, and the engineer wanted to know about the first refusal anyway.
    """
    done: list[Candidate] = []
    for device in devices:
        if skip_failed and device.login_error:
            done.append(device)
            continue
        if device.inventory is not None:
            done.append(device)
            continue
        on_device(device, "connecting")
        try:
            inventory = open_device(device)
        except Exception as exc:  # any transport failure is a refusal, not a crash
            done.append(refused(device, str(exc)))
            on_device(device, "failed")
            continue
        if inventory is None:
            done.append(refused(device, "no answer"))
            on_device(device, "failed")
            continue
        done.append(confirmed(device, inventory,  # type: ignore[arg-type]
                              found_at=device.address))
        on_device(device, "confirmed")
    return merge_candidates(done)


def log_in(
    devices: Sequence[Candidate],
    open_device: Callable[[Candidate], object],
    *,
    on_device: Callable[[Candidate, str], None] = lambda device, state: None,
    cancelled: Callable[[], bool] = lambda: False,
    now: Callable[[], float] = time.time,
) -> list[Candidate]:
    """Log in to each of ``devices`` the engineer picked, and read it again.

    Unlike `confirm`, nothing is skipped: a device picked by hand is read even when it was
    read before (that is how its state gets refreshed) or was refused before (the
    credentials may have been fixed since). A device that refuses now loses what an
    earlier login read, because an update is only ever planned from what can be read
    today. One result per device, in the same order; a device not reached because the
    run was cancelled comes back as it was.
    """
    done: list[Candidate] = []
    for device in devices:
        if cancelled():
            done.append(device)
            continue
        on_device(device, "connecting")
        try:
            inventory = open_device(device)
            if inventory is None:
                raise ValueError("no answer")
        except Exception as exc:  # any transport failure is a refusal, not a crash
            done.append(refused(device.model_copy(update={"inventory": None}), str(exc)))
            on_device(device, "failed")
            continue
        done.append(confirmed(device, inventory,  # type: ignore[arg-type]
                              found_at=device.address).model_copy(update={"last_seen": now()}))
        on_device(device, "confirmed")
    return done


def with_neighbours(devices: Sequence[Candidate],
                    sightings: Sequence[Sighting]) -> list[Candidate]:
    """What the reached devices said about the others, folded into the list.

    A neighbour or a station already known gains the sighting, which is what the update
    order is drawn from; one nobody had heard of yet joins the list unconfirmed.

    Known means a shared MAC, or else the same identity *and* the same address. The MAC
    alone is not enough here: a neighbour table names the MAC of whichever port the
    neighbour was heard on, and MNDP that of the port it announced from — one hAP ac³
    came back as …:18, …:1B and …:1C from three places on one switch (2026-10-12).
    """
    merged = list(devices)
    for sighting in sightings:
        seen = sighting.normalised()
        index = _same_box(merged, seen)
        if index is None:
            merged.append(from_sighting(seen))
        else:
            merged[index] = merge_into(merged[index], seen)
    return merged


def _same_box(devices: Sequence[Candidate], seen: Sighting) -> int | None:
    macs = set(seen.macs)
    for index, device in enumerate(devices):
        if macs & set(device.macs):
            return index
    if not (seen.identity and seen.address):
        return None
    for index, device in enumerate(devices):
        if device.identity == seen.identity and seen.address in device.addresses:
            return index
    return None
