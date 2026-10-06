"""Updating more than one device, in an order that does not cut off the next one.

Everything here exists because of one asymmetry: a batch that stops too early costs an
evening, and a batch that carries on past a failure costs a site visit. So it stops.

Three rules, in the order they matter:

1. **Order.** Leaves before the devices they hang off, from `discover/topology.py`. An AP
   updated before its stations takes them offline for the two minutes it is rebooting,
   and a station that then fails has failed out of reach.
2. **Stop on the first failed verification.** Not "log it and continue": a radio that did
   not come back on one device is the best evidence available that the same package set
   is wrong for the ones behind it.
3. **One at a time unless told otherwise.** Parallelism is a convenience for a shelf of
   CPEs and a hazard on a backbone, so the default is 1 and anything higher is a decision.

Planning is pure and happens for every device before any device is touched, which is what
makes a dry run worth running: a batch that would fail on its fourth device says so
before it reboots the first.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum

from mqs.apply.packages import UpdateDir
from mqs.apply.version_change import VersionPlan, plan_version
from mqs.discover.candidates import Candidate
from mqs.discover.topology import Topology, UpdateOrder, update_order
from mqs.issues import Issue, Severity
from mqs.model.inventory import WirelessDriver, WirelessRole
from mqs.model.version import RosVersion

DEFAULT_PARALLEL = 1
# Above this, "limited parallelism" stops being limited. A number this high on a network
# of antennas means somebody has mistaken the flag for a speed control.
MAX_PARALLEL = 8


class Outcome(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"  # the batch stopped before reaching it
    UNCHANGED = "unchanged"  # nothing to install: already on the target version
    BLOCKED = "blocked"  # its own plan refused before anything started


@dataclass
class DeviceStep:
    """One device's place in the batch, and what happened to it."""

    device: Candidate
    plan: VersionPlan | None = None
    outcome: Outcome = Outcome.PENDING
    detail: str = ""
    # Devices that would lose their path if this one failed, for the message when it does.
    behind: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.device.identity or self.device.address or self.device.key

    @property
    def blocked(self) -> bool:
        return self.plan is None or self.plan.blocked

    @property
    def changes(self) -> bool:
        return self.plan is not None and self.plan.changes


@dataclass
class BatchPlan:
    """Every device, in order, with its own route worked out before anything is touched."""

    steps: tuple[DeviceStep, ...] = ()
    order: UpdateOrder = field(default_factory=UpdateOrder)
    target: RosVersion | None = None
    issues: tuple[Issue, ...] = ()

    @property
    def topology(self) -> Topology:
        return self.order.topology

    @property
    def needs_confirmation(self) -> bool:
        """An incomplete tree, or any device whose route is not a plain reboot forward.

        Both mean somebody should read the order before it runs: the first because the
        order may be wrong, the second because a downgrade or a cross-major move is never
        made on a checkbox.
        """
        return self.order.needs_confirmation or any(
            step.plan is not None and step.plan.needs_confirmation for step in self.steps)

    @property
    def blocked(self) -> tuple[DeviceStep, ...]:
        """Steps whose plan refuses.

        A refused plan can still have hops in it — a route to 7.16.1 whose driver package
        is missing names the reboot it would make and *then* refuses — so this asks the
        plan whether it is blocked rather than inferring it from there being nothing to do.
        """
        return tuple(s for s in self.steps if s.blocked)

    @property
    def runnable(self) -> tuple[DeviceStep, ...]:
        return tuple(s for s in self.steps if s.changes and not s.blocked)

    @property
    def ready(self) -> bool:
        """Whether this batch may run at all.

        A device that cannot be planned stops the whole batch rather than being skipped
        past: it is usually the same missing package for everything on the shelf, and
        finding that out after four reboots helps nobody.
        """
        return bool(self.runnable) and not self.blocked


def plan_batch(
    devices: Sequence[Candidate],
    target: RosVersion,
    found_for: Callable[[Candidate], UpdateDir],
    *,
    topology: Topology | None = None,
    here: str = "",
    wifi_package: WirelessDriver | None = None,
    allow_missing: Sequence[str] = (),
) -> BatchPlan:
    """Work out every device's route before touching any of them.

    ``found_for`` gives each device the packages available for *its* architecture, which
    on a mixed fleet is not one directory's worth of answer.
    """
    order = update_order(devices, topology, here=here)
    issues: list[Issue] = []
    if order.skipped:
        issues.append(Issue("fleet.not_confirmed", Severity.WARNING, "", {
            "names": ", ".join(sorted(_name(d) for d in order.skipped)),
            "count": len(order.skipped)}))
    if order.needs_confirmation:
        issues.append(Issue("fleet.order_uncertain", Severity.WARNING, "", {
            "orphans": len(order.topology.orphans),
            "roots": len(order.topology.roots),
            "cycles": len(order.topology.cycles)}))

    steps: list[DeviceStep] = []
    for device in order.devices:
        inventory = device.inventory
        if inventory is None:  # update_order already filtered these out
            continue
        plan = plan_version(inventory, found_for(device), target,
                            wifi_package=wifi_package, allow_missing=list(allow_missing))
        steps.append(DeviceStep(device, plan, behind=_behind(order, device.key)))
    for step in steps:
        if step.blocked:
            step.outcome = Outcome.BLOCKED
            step.detail = _first_error(step.plan)
        elif not step.changes:
            # Already where it should be. Not "skipped", which means the batch stopped
            # before reaching it, and not a failure either.
            step.outcome = Outcome.UNCHANGED
            step.detail = f"already on {target}"
    return BatchPlan(tuple(steps), order, target, tuple(issues))


def _behind(order: UpdateOrder, key: str) -> tuple[str, ...]:
    """Devices whose path to this PC runs through ``key``."""
    tree = order.topology
    found: list[str] = []
    stack = list(tree.children_of(key))
    while stack:
        child = stack.pop()
        if child in found:
            continue
        found.append(child)
        stack.extend(tree.children_of(child))
    return tuple(sorted(found))


def _name(device: Candidate) -> str:
    return device.identity or device.address or device.key


def _first_error(plan: VersionPlan | None) -> str:
    if plan is None:
        return "no plan"
    error = next((i for i in plan.issues if i.severity == Severity.ERROR), None)
    return error.message if error else ""


# ------------------------------------------------------------------ running it


@dataclass
class BatchReport:
    steps: tuple[DeviceStep, ...] = ()
    stopped_by: DeviceStep | None = None

    @property
    def succeeded(self) -> bool:
        return self.stopped_by is None and all(
            s.outcome in (Outcome.DONE, Outcome.SKIPPED, Outcome.UNCHANGED)
            for s in self.steps)

    def of(self, outcome: Outcome) -> tuple[DeviceStep, ...]:
        return tuple(s for s in self.steps if s.outcome is outcome)


@dataclass
class BatchRunner:
    """Walks the plan, in groups of ``parallel``, and stops at the first failure.

    ``run_one`` does the actual work on one device and returns nothing on success or
    raises on failure; everything about transports, uploads and reboots lives there.
    """

    plan: BatchPlan
    run_one: Callable[[DeviceStep], None]
    parallel: int = DEFAULT_PARALLEL
    on_step: Callable[[DeviceStep], None] = lambda step: None
    cancelled: Callable[[], bool] = lambda: False

    def run(self) -> BatchReport:
        steps = list(self.plan.runnable)
        stopped: DeviceStep | None = None
        for group in self._groups(steps):
            if stopped is not None or self.cancelled():
                break
            failed = self._run_group(group)
            if failed is not None:
                stopped = failed
        for step in steps:
            if step.outcome is Outcome.PENDING:
                step.outcome = Outcome.SKIPPED
                step.detail = (f"the batch stopped at {stopped.name}" if stopped
                               else "cancelled")
        return BatchReport(tuple(self.plan.steps), stopped)

    def _groups(self, steps: Sequence[DeviceStep]) -> list[list[DeviceStep]]:
        """Devices that may run at once: never a parent with anything behind it.

        Parallelism is capped and, more importantly, a device with devices behind it runs
        alone. Two CPEs on a shelf are independent; an AP and the station on it are not,
        and rebooting both at once is how a batch loses the station.
        """
        width = max(1, min(self.parallel, MAX_PARALLEL))
        groups: list[list[DeviceStep]] = []
        current: list[DeviceStep] = []
        for step in steps:
            if step.behind:
                if current:
                    groups.append(current)
                    current = []
                groups.append([step])
                continue
            current.append(step)
            if len(current) == width:
                groups.append(current)
                current = []
        if current:
            groups.append(current)
        return groups

    def _run_group(self, group: Sequence[DeviceStep]) -> DeviceStep | None:
        if len(group) == 1:
            return self._run_step(group[0])
        with ThreadPoolExecutor(max_workers=len(group)) as pool:
            failures = [f for f in pool.map(self._run_step, group) if f is not None]
        return failures[0] if failures else None

    def _run_step(self, step: DeviceStep) -> DeviceStep | None:
        if self.cancelled():
            return None
        step.outcome = Outcome.RUNNING
        self.on_step(step)
        try:
            self.run_one(step)
        except Exception as exc:  # any failure on one device stops the batch
            step.outcome = Outcome.FAILED
            step.detail = str(exc)
            self.on_step(step)
            return step
        step.outcome = Outcome.DONE
        self.on_step(step)
        return None


def dry_run_lines(plan: BatchPlan) -> list[str]:
    """The whole batch as text, for reading before it is allowed to happen."""
    lines: list[str] = []
    for number, step in enumerate(plan.steps, start=1):
        role = _role(step.device)
        where = f" via {len(step.behind)} behind" if step.behind else ""
        lines.append(f"{number}. {step.name} ({role}){where}")
        if step.plan is None or not step.plan.changes:
            lines.append(f"     {_first_error(step.plan) or 'nothing to install'}")
            continue
        for hop in step.plan.hops:
            lines.append(f"     {hop.version}: "
                         f"{', '.join(p.file_name for p in hop.packages)}")
        if step.plan.drivers:
            lines.append(f"     wireless driver: {', '.join(sorted(step.plan.drivers))}")
    for device in plan.order.skipped:
        lines.append(f"-  {_name(device)}: not confirmed, left out")
    return lines


def _role(device: Candidate) -> str:
    roles = device.radios.roles
    if WirelessRole.AP in roles:
        return "AP"
    if WirelessRole.STATION in roles:
        return "station"
    return "no radio" if device.inventory else "?"
