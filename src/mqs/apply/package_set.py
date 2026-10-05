"""Which packages a device must end up with, and which versions it has to stop at.

This is the pure half of a wireless-safe update: device state and a target version in,
package names and route stops out. Nothing here opens a socket, reads a device or touches
the Update directory — `apply/version_change.py` does that, with what this module decides.

The one rule everything follows: **the package set is copied from the device, never
guessed from its model.** Which driver a radio runs decides the interface configuration,
so a set that looks right for a "SXT" is worth nothing next to what `/system package
print` and `/interface wireless|wifi|w60g` actually say. Where the state is not enough to
name a package — the one real case being `wifiwave2` on the way to 7.13, where the device
may not say which half of the split it needs — the answer is a question for the user, not
a default.

The rules themselves (driver names, what they are called in which version, the stops) are
data: `mqs/data/wireless_packages.yaml`, overridable per user. See that file for what is
OVĚŘENO and what is not; at the time of writing none of it has met a radio.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from mqs.issues import Issue, Severity
from mqs.model.inventory import (
    DeviceInventory,
    PackageInfo,
    WirelessDriver,
    WirelessMenu,
)
from mqs.model.version import RosVersion

MAX_STOPS = 6  # a route longer than this is a bug in the gates, not a device that needs it
RULES_FILE = "wireless_packages.yaml"


def issue(code: str, severity: Severity = Severity.ERROR, **params: object) -> Issue:
    return Issue(code, severity, "", dict(params))


# ------------------------------------------------------------------------------ the rules


@dataclass(frozen=True)
class DriverRule:
    """A package that drives radios, and the menus it serves."""

    driver: WirelessDriver
    menus: frozenset[WirelessMenu]


@dataclass(frozen=True)
class Rename:
    """What a driver is called in a given range of versions.

    ``needs_choice`` marks the split of `wifiwave2` at 7.13: the rule names both halves
    and something else has to pick, which is `choose_wifi_driver`.
    """

    source: WirelessDriver
    targets: tuple[WirelessDriver, ...]
    at_or_above: RosVersion | None = None
    below: RosVersion | None = None
    needs_choice: bool = False

    def applies_to(self, target: RosVersion) -> bool:
        if self.at_or_above is not None and target < self.at_or_above:
            return False
        return not (self.below is not None and target >= self.below)


@dataclass(frozen=True)
class Gate:
    """A version the route has to stop at before it can go on.

    Applies to a device below ``when_below`` whose target is at or above
    ``when_target_at_least``; the stop is the newest version available in
    [``via_at_least``, ``via_below``).

    ``requires_drivers`` narrows a gate to devices that actually run a wireless driver.
    The 7.12 stop is there so the wireless packages get converted, so a device with no
    radio has nothing to convert and nothing to stop for — which is why the mmips hEX S
    went 6.49.12 -> 6.49.22 -> 7.23.7 in two hops and was right to (OVĚŘENO 2026-09-28,
    docs/VERSION_CHANGE.md §11). An antenna on the same route takes three.
    """

    id: str
    when_below: RosVersion
    when_target_at_least: RosVersion
    via_at_least: RosVersion
    via_below: RosVersion
    code: str
    requires_drivers: bool = False

    def applies(self, current: RosVersion, target: RosVersion,
                has_drivers: bool = True) -> bool:
        if self.requires_drivers and not has_drivers:
            return False
        return current < self.when_below and target >= self.when_target_at_least

    def holds(self, version: RosVersion) -> bool:
        return self.via_at_least <= version < self.via_below


@dataclass(frozen=True)
class SplitRules:
    """How to find out which half of the 7.13 split a `wifiwave2` device needs."""

    ask_the_device_first: bool = True
    by_model_pattern: tuple[tuple[re.Pattern[str], WirelessDriver, str], ...] = ()
    by_model: Mapping[str, WirelessDriver] = field(default_factory=dict)


@dataclass(frozen=True)
class PackageRules:
    drivers: Mapping[WirelessDriver, DriverRule] = field(default_factory=dict)
    renames: tuple[Rename, ...] = ()
    gates: tuple[Gate, ...] = ()
    split: SplitRules = field(default_factory=SplitRules)
    space_reserve: int = 0

    @property
    def driver_names(self) -> frozenset[str]:
        return frozenset(d.value for d in self.drivers)

    def menus_of(self, driver: WirelessDriver) -> frozenset[WirelessMenu]:
        rule = self.drivers.get(driver)
        return rule.menus if rule else frozenset()

    def drivers_for_menu(self, menu: WirelessMenu) -> frozenset[WirelessDriver]:
        return frozenset(d for d, rule in self.drivers.items() if menu in rule.menus)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> PackageRules:
        return cls(
            drivers=_read_drivers(data.get("drivers") or {}),
            renames=_read_renames(data.get("renames") or []),
            gates=_read_gates(data.get("gates") or []),
            split=_read_split(data.get("wifi_split") or {}),
            space_reserve=int(data.get("space_reserve_bytes") or 0),
        )


def _version(text: Any) -> RosVersion | None:
    return RosVersion.parse(str(text)) if text else None


def _driver(name: Any) -> WirelessDriver:
    return WirelessDriver(str(name))


def _read_drivers(data: Mapping[str, Any]) -> dict[WirelessDriver, DriverRule]:
    rules: dict[WirelessDriver, DriverRule] = {}
    for name, entry in data.items():
        driver = _driver(name)
        menus = frozenset(WirelessMenu(m) for m in (entry or {}).get("menus") or [])
        rules[driver] = DriverRule(driver, menus)
    return rules


def _read_renames(data: Sequence[Mapping[str, Any]]) -> tuple[Rename, ...]:
    return tuple(
        Rename(
            source=_driver(entry["from"]),
            targets=tuple(_driver(t) for t in entry.get("to") or []),
            at_or_above=_version(entry.get("at_or_above")),
            below=_version(entry.get("below")),
            needs_choice=bool(entry.get("needs_choice")),
        )
        for entry in data
    )


def _read_gates(data: Sequence[Mapping[str, Any]]) -> tuple[Gate, ...]:
    gates = []
    for entry in data:
        when_below = _version(entry["when_below"])
        target_at_least = _version(entry["when_target_at_least"])
        via_at_least = _version(entry["via_at_least"])
        via_below = _version(entry["via_below"])
        if None in (when_below, target_at_least, via_at_least, via_below):
            continue
        gates.append(Gate(str(entry.get("id") or ""), when_below, target_at_least,
                          via_at_least, via_below, str(entry.get("code") or "version.gate"),
                          bool(entry.get("requires_drivers"))))
    return tuple(gates)


def _read_split(data: Mapping[str, Any]) -> SplitRules:
    patterns = tuple(
        (re.compile(entry["pattern"], re.IGNORECASE), _driver(entry["package"]),
         str(entry.get("why") or ""))
        for entry in data.get("by_model_pattern") or []
    )
    return SplitRules(
        ask_the_device_first=bool(data.get("ask_the_device_first", True)),
        by_model_pattern=patterns,
        by_model={str(k): _driver(v) for k, v in (data.get("by_model") or {}).items()},
    )


def _read_yaml(path_or_name: Path | str) -> dict[str, Any]:
    if isinstance(path_or_name, Path):
        text = path_or_name.read_text(encoding="utf-8")
    else:
        text = files("mqs.data").joinpath(path_or_name).read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


@cache
def default_rules() -> PackageRules:
    """The bundled rules. A user copy in the application data directory wins over them,
    key by key, so a model the application had to ask about stays answered."""
    data = _read_yaml(RULES_FILE)
    return PackageRules.from_mapping(data)


def rules_from_file(path: Path) -> PackageRules:
    return PackageRules.from_mapping(_read_yaml(path))


def merged_rules(override: Path | None, base: PackageRules | None = None) -> PackageRules:
    """The bundled rules with a user file laid over them.

    Only the two tables a user has a reason to extend are merged — the model map and the
    patterns that split `wifiwave2`. Driver names and gates are not something to override
    by accident, so a user file that sets them replaces them outright.
    """
    base = base or default_rules()
    if override is None or not override.is_file():
        return base
    extra = PackageRules.from_mapping(_read_yaml(override))
    return PackageRules(
        drivers=extra.drivers or base.drivers,
        renames=extra.renames or base.renames,
        gates=extra.gates or base.gates,
        split=SplitRules(
            ask_the_device_first=extra.split.ask_the_device_first,
            by_model_pattern=extra.split.by_model_pattern or base.split.by_model_pattern,
            by_model={**base.split.by_model, **extra.split.by_model},
        ),
        space_reserve=extra.space_reserve or base.space_reserve,
    )


# ---------------------------------------------------------------- what the device runs


@dataclass(frozen=True)
class DriverState:
    """The wireless drivers a device runs, as read from the device itself.

    ``bundled`` are the ones with no package of their own in `/system package print` — on
    v6 that is how the legacy driver always arrives, inside `routeros-<arch>`.
    ``unexplained`` are menus the device answers on without any driver this module can
    name; the only safe thing to do with one is refuse, because a package that cannot be
    named cannot be uploaded.
    """

    drivers: frozenset[WirelessDriver] = frozenset()
    # The ones a radio actually depends on, as opposed to a package that merely happens to
    # be installed. Only these turn a missing counterpart into a lost link, and only these
    # make the 7.12 conversion stop necessary.
    from_radios: frozenset[WirelessDriver] = frozenset()
    bundled: frozenset[WirelessDriver] = frozenset()
    unexplained: frozenset[WirelessMenu] = frozenset()

    @property
    def any_wireless(self) -> bool:
        """The device has a radio — which is not the same as having a wireless package."""
        return bool(self.from_radios or self.unexplained)

    @property
    def wifi_drivers(self) -> frozenset[WirelessDriver]:
        return frozenset(d for d in self.drivers if d.is_wifi)

    @property
    def wifi_radios(self) -> frozenset[WirelessDriver]:
        return frozenset(d for d in self.from_radios if d.is_wifi)

    @property
    def names(self) -> str:
        return ", ".join(sorted(d.value for d in self.drivers)) or "-"


def installed_drivers(inventory: DeviceInventory,
                      rules: PackageRules | None = None) -> DriverState:
    """The drivers this device needs carried over, from two kinds of evidence.

    **Radios that exist.** A driver is needed when there are interfaces in a menu it
    serves. This, and not the package list, is what says a device has a radio: the v6
    `routeros-<arch>` bundle ships `wireless` to *every* board, so the hEX S RB760iGS —
    which has no antenna socket at all — lists the package like any SXT does (OVĚŘENO,
    tests/test_version_change.py mmips()). Reading that as "has a radio" would send every
    v6 router on a detour through 7.12 and hunt for a package it never needed.

    **Driver packages installed as files of their own.** Those have to come along whatever
    they drive, for the same reason any installed package does: a version left behind is a
    package out of step with `routeros`. A member of a v6 bundle is not one of these — it
    arrives with the bundle and needs no file.

    A menu holding interfaces that no named driver can account for is `unexplained`: the
    device has a radio and has not said what drives it, and a package that cannot be named
    cannot be uploaded.
    """
    rules = rules or default_rules()
    drivers: set[WirelessDriver] = set()
    from_radios: set[WirelessDriver] = set()
    bundled: set[WirelessDriver] = set()
    unexplained: set[WirelessMenu] = set()

    for package in inventory.packages:
        if not package.installed or package.name not in rules.driver_names:
            continue
        driver = WirelessDriver(package.name)
        if package.bundle:
            bundled.add(driver)  # inside the v6 bundle: present, but not a file to carry
        else:
            drivers.add(driver)

    for menu in {i.menu for i in inventory.radios.interfaces}:
        candidates = rules.drivers_for_menu(menu)
        serving = (drivers | bundled) & candidates
        if serving:
            drivers |= serving
            from_radios |= serving
        elif len(candidates) == 1:
            # Only one driver can serve this menu, so naming it is not a guess.
            only = next(iter(candidates))
            drivers.add(only)
            from_radios.add(only)
            bundled.add(only)
        else:
            unexplained.add(menu)
    return DriverState(frozenset(drivers), frozenset(from_radios),
                       frozenset(bundled & drivers), frozenset(unexplained))


# ---------------------------------------------------------------- naming them for a target


def offered_drivers(inventory: DeviceInventory,
                    rules: PackageRules | None = None) -> frozenset[WirelessDriver]:
    """Drivers the device itself offers for download but does not have installed.

    7.23 lists every package MikroTik publishes for the board next to the installed ones,
    with `available=true` and no version (OVĚŘENO on the arm hEX S, 2026-09-27, see
    docs/DIALECT_NOTES.md). For a board that offers exactly one of `wifi-qcom` and
    `wifi-qcom-ac` that is the board's own answer to which driver belongs on it — better
    than any model table, and the reason this is asked first.
    """
    rules = rules or default_rules()
    return frozenset(
        WirelessDriver(p.name) for p in inventory.packages
        if p.available and p.name in rules.driver_names
    )


def choose_wifi_driver(
    options: Sequence[WirelessDriver],
    inventory: DeviceInventory,
    *,
    override: WirelessDriver | None = None,
    rules: PackageRules | None = None,
) -> tuple[WirelessDriver | None, Issue | None]:
    """Which half of the 7.13 split this device needs — asked, not guessed.

    In order: what the user said, what the device offers for download, the model patterns,
    the model table. Nothing matching returns ``None`` and an error naming the choice to
    make; it is never resolved by picking the more common one.
    """
    rules = rules or default_rules()
    allowed = set(options)
    if override is not None:
        if override not in allowed:
            return None, issue("version.wifi_choice_invalid", chosen=override.value,
                               options=_names(options))
        return override, None

    if rules.split.ask_the_device_first:
        offered = offered_drivers(inventory, rules) & allowed
        if len(offered) == 1:
            return next(iter(offered)), None

    model_names = [n for n in (
        inventory.routerboard.model if inventory.routerboard else "",
        inventory.resource.board_name,
    ) if n]
    for name in model_names:
        for pattern, driver, _why in rules.split.by_model_pattern:
            if driver in allowed and pattern.search(name):
                return driver, None
    for name in model_names:
        best_len, best = 0, None
        for prefix, driver in rules.split.by_model.items():
            if driver in allowed and name.lower().startswith(prefix.lower()) \
                    and len(prefix) > best_len:
                best_len, best = len(prefix), driver
        if best is not None:
            return best, None

    return None, issue("version.wifi_choice_needed", model=model_names[0] if model_names
                       else "?", options=_names(options))


def _names(drivers: Iterable[WirelessDriver]) -> str:
    return ", ".join(sorted(d.value for d in drivers))


@dataclass(frozen=True)
class DriverPlan:
    """What the drivers mean for one hop."""

    required: frozenset[str] = frozenset()  # names that need a file in the target version
    dropped: frozenset[str] = frozenset()  # installed, with nowhere to go in the target
    issues: tuple[Issue, ...] = ()

    @property
    def blocked(self) -> bool:
        return any(i.severity == Severity.ERROR for i in self.issues)


def driver_packages(
    state: DriverState,
    target: RosVersion,
    inventory: DeviceInventory,
    *,
    wifi_override: WirelessDriver | None = None,
    rules: PackageRules | None = None,
) -> DriverPlan:
    """The driver packages this device needs in ``target``, and why it cannot have them.

    A v6 target needs no file at all: MikroTik publishes one `routeros-<arch>` bundle per
    architecture and nothing beside it, and that bundle carries the legacy driver. v6 has
    no `wifi` driver in it, though — so a *radio* running one has nowhere to go and the
    move is refused, while a `wifi-qcom` that is merely installed on a device with no
    radio is just another package that cannot come along.
    """
    rules = rules or default_rules()
    if state.unexplained:
        return DriverPlan(issues=(issue(
            "version.driver_unknown",
            menus=", ".join(sorted(m.value for m in state.unexplained))),))
    if not state.drivers:
        return DriverPlan()

    if target.major < 7:
        on_air = state.wifi_radios
        if on_air:
            # Not a feature lost in a downgrade: the radios would have nothing driving them.
            return DriverPlan(issues=(issue("version.driver_no_v6", drivers=_names(on_air),
                                            version=str(target)),))
        return DriverPlan(dropped=frozenset(d.value for d in state.wifi_drivers))

    names: set[str] = set()
    issues: list[Issue] = []
    for driver in sorted(state.drivers, key=lambda d: d.value):
        rename = next((r for r in rules.renames
                       if r.source == driver and r.applies_to(target)), None)
        if rename is None:
            names.add(driver.value)
            continue
        if len(rename.targets) == 1:
            names.add(rename.targets[0].value)
            continue
        chosen, problem = choose_wifi_driver(rename.targets, inventory,
                                             override=wifi_override, rules=rules)
        if chosen is None:
            issues.append(problem or issue("version.wifi_choice_needed", model="?",
                                           options=_names(rename.targets)))
            continue
        names.add(chosen.value)
    return DriverPlan(frozenset(names), frozenset(), tuple(issues))


def driver_switch_issue(
    state: DriverState,
    wifi_override: WirelessDriver | None,
) -> Issue | None:
    """An override that would change the driver rather than name it.

    Asking for `wifi-qcom-ac` on a device whose radios run the legacy `wireless` driver is
    not a choice this application makes on anyone's behalf: it wipes the interface
    configuration. The override only ever resolves the 7.13 split, so it is refused unless
    the device actually runs a `wifi` driver.
    """
    if wifi_override is None or not wifi_override.is_wifi:
        return None
    if state.wifi_drivers:
        return None
    return issue("version.driver_switch", chosen=wifi_override.value,
                 current=state.names or WirelessDriver.NONE.value)


# ---------------------------------------------------------------- the other extras


def plain_extras(inventory: DeviceInventory,
                 rules: PackageRules | None = None) -> list[PackageInfo]:
    """Installed packages that are neither RouterOS itself nor a wireless driver.

    container, gps, ups, iot, ... — each needs a counterpart in the target version too,
    but the user may knowingly go on without one. A driver never gets that choice.
    """
    from mqs.apply.packages import extra_packages  # local: packages imports nothing of ours

    rules = rules or default_rules()
    return [p for p in extra_packages(inventory) if p.name not in rules.driver_names]


# ---------------------------------------------------------------- the route


@dataclass(frozen=True)
class Route:
    """The versions to install, in order; the last one is the target."""

    stops: tuple[RosVersion, ...] = ()
    issues: tuple[Issue, ...] = ()

    @property
    def blocked(self) -> bool:
        return any(i.severity == Severity.ERROR for i in self.issues)


def plan_route(
    current: RosVersion,
    target: RosVersion,
    available: Sequence[RosVersion],
    rules: PackageRules | None = None,
    *,
    has_drivers: bool = True,
) -> Route:
    """The versions between ``current`` and ``target``, stops included.

    Gates are re-checked from every stop, so they compose without knowing about each
    other: an antenna on 6.4x going to 7.16 collects the 6.49.22 gate and then, from
    there, the 7.12 one — `6.4x -> 6.49.22 -> 7.12.x -> 7.16`, three reboots. A gate with
    nothing available inside its range is an error, never a jump.

    ``has_drivers`` says whether this device runs a wireless driver at all; the default is
    the careful answer, because a gate skipped by mistake is a radio lost.
    """
    rules = rules or default_rules()
    stops: list[RosVersion] = []
    position = current
    for _ in range(MAX_STOPS):
        gate = next((g for g in rules.gates
                     if g.applies(position, target, has_drivers)), None)
        if gate is None:
            break
        inside = sorted((v for v in available if gate.holds(v)), reverse=True)
        if not inside:
            return Route((), (issue(gate.code, version=str(target), current=str(position),
                                    gate=str(gate.via_at_least)),))
        stop = inside[0]
        if stop <= position:
            # The gate wants a version the device already has or has passed: it has done
            # its job, and going on is not this loop's business.
            break
        stops.append(stop)
        position = stop
    else:
        return Route((), (issue("version.route_too_long", current=str(current),
                                version=str(target), stops=MAX_STOPS),))
    return Route((*stops, target))
