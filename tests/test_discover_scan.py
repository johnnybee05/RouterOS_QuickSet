"""Running the sources together.

The behaviour being pinned down: one source failing, hanging or finding nothing must not
stop the others, and the result must say which one it was. A scan that quietly returns
fewer devices because something timed out is worse than no scan at all.
"""

from __future__ import annotations

import time

from factories import make_inventory, make_radios
from mqs.discover.candidates import Confidence, Sighting, Source
from mqs.discover.portscan import ScanLimits
from mqs.discover.scan import (
    ScanPlan,
    confirm,
    log_in,
    run,
    snmp_sightings,
    with_neighbours,
)
from mqs.discover.snmp import SnmpReply
from mqs.discover.topology import build
from mqs.model.inventory import PackageInfo
from mqs.transport.mndp import DiscoveredDevice

MB = 1024 * 1024
MAC_A = "AA:BB:CC:00:00:01"
MAC_B = "AA:BB:CC:00:00:02"


class FakeScanner:
    """An MNDP scanner that answers with whatever it was given."""

    def __init__(self, devices: list[DiscoveredDevice], *, fail: str = "") -> None:
        self.devices_found = devices
        self.fail = fail
        self.solicited = 0

    def __enter__(self) -> FakeScanner:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def open(self) -> None:
        if self.fail:
            raise OSError(self.fail)

    def solicit(self) -> None:
        self.solicited += 1

    def poll(self, timeout: float) -> list[DiscoveredDevice]:
        return []

    def devices(self, max_age: float | None = None) -> list[DiscoveredDevice]:
        return self.devices_found


def announced(mac: str = MAC_A, identity: str = "sxt-01") -> DiscoveredDevice:
    return DiscoveredDevice(mac=mac, identity=identity, version="7.15.3",
                            platform="MikroTik", board="SXTsq 5 nD",
                            local_interface="eth0")


def quick(**extra) -> ScanPlan:
    return ScanPlan(mndp_seconds=0.0, **extra)


# ------------------------------------------------------------------ one source each


def test_mndp_alone_finds_what_it_hears():
    scanner = FakeScanner([announced()])
    result = run(quick(), mndp_factory=lambda: scanner)
    assert [d.identity for d in result.devices] == ["sxt-01"]
    assert result.devices[0].confidence is Confidence.PROBABLE
    assert scanner.solicited == 1  # asked once, rather than waiting out an interval
    assert not result.failures


def test_mndp_is_the_only_source_on_by_default():
    """Passive, local, and it costs nothing. Everything else needs an address range, a
    community string or credentials — which means somebody decided."""
    plan = ScanPlan()
    assert plan.mndp and not plan.scan_ports and not plan.snmp


def test_a_port_scan_contributes_addresses_and_no_claims():
    plan = quick(mndp=False, subnets=("192.0.2.0/30",), scan_ports=True,
                 scan_limits=ScanLimits(rate=0.0))
    result = run(plan, knock=lambda address, port, timeout: address == "192.0.2.1")
    assert [d.address for d in result.devices] == ["192.0.2.1"]
    assert result.devices[0].confidence is Confidence.UNKNOWN


def test_snmp_contributes_what_answers():
    plan = quick(mndp=False, subnets=("192.0.2.0/30",), snmp_community="public")

    def ask(address, community, *, timeout_s):
        return (SnmpReply(address, "1.3.6.1.4.1.14988.1", "RouterOS", "sxt-01")
                if address == "192.0.2.2" else None)

    result = run(plan, snmp_ask=ask)
    assert [d.identity for d in result.devices] == ["sxt-01"]
    assert result.devices[0].confidence is Confidence.PROBABLE


def test_snmp_needs_both_a_community_and_somewhere_to_ask():
    assert not ScanPlan(snmp_community="public").snmp
    assert not ScanPlan(subnets=("192.0.2.0/30",)).snmp
    assert ScanPlan(subnets=("192.0.2.0/30",), snmp_community="public").snmp


def test_nothing_answering_snmp_is_not_a_failure():
    found = snmp_sightings(["192.0.2.1"], "public", timeout_s=0.1,
                           ask=lambda *a, **k: None)
    assert found == []


# ------------------------------------------------------------------ together


def test_the_same_device_from_two_sources_is_one_device():
    plan = quick(subnets=("192.0.2.0/30",), snmp_community="public")
    scanner = FakeScanner([announced()])

    def ask(address, community, *, timeout_s):
        return SnmpReply(address, "1.3.6.1.4.1.14988.1", "RouterOS", "sxt-01") \
            if address == "192.0.2.1" else None

    result = run(plan, mndp_factory=lambda: scanner, snmp_ask=ask)
    # Different sources, no shared MAC: SNMP only ever has an address, so these stay two
    # rows until a login gives them a serial. Honest, and the merge says why.
    assert len(result.devices) == 2
    assert {s for d in result.devices for s in d.sources} == {Source.MNDP, Source.SNMP}


def test_a_source_that_fails_does_not_take_the_others_with_it():
    plan = quick(subnets=("192.0.2.0/30",), scan_ports=True,
                 scan_limits=ScanLimits(rate=0.0))
    result = run(plan, mndp_factory=lambda: FakeScanner([], fail="no such interface"),
                 knock=lambda a, p, t: a == "192.0.2.1")
    assert [d.address for d in result.devices] == ["192.0.2.1"]
    failed = result.of(Source.MNDP)
    assert failed is not None and not failed.ok and "no such interface" in failed.error


def test_a_source_that_hangs_costs_its_own_timeout_and_no_one_elses():
    """The slowest source must not decide how long a scan takes."""

    class Slow(FakeScanner):
        def poll(self, timeout: float) -> list:
            time.sleep(1.5)
            return []

        def devices(self, max_age: float | None = None) -> list:
            return []

    plan = ScanPlan(mndp_seconds=1.0, subnets=("192.0.2.0/30",), scan_ports=True,
                    scan_limits=ScanLimits(rate=0.0), source_timeout_s=0.3)
    started = time.monotonic()
    result = run(plan, mndp_factory=lambda: Slow([]),
                 knock=lambda a, p, t: a == "192.0.2.1")
    assert time.monotonic() - started < 1.0  # not the 1.5 s the slow one takes
    timed_out = result.of(Source.MNDP)
    assert timed_out is not None and "no answer" in timed_out.error


def test_every_source_is_reported_running_and_then_done():
    seen: list[tuple[Source, str]] = []
    run(quick(), mndp_factory=lambda: FakeScanner([announced()]),
        on_source=lambda source, state: seen.append((source, state)))
    assert (Source.MNDP, "running") in seen and (Source.MNDP, "ok") in seen


def test_a_scan_with_no_sources_enabled_finds_nothing_and_says_nothing():
    result = run(ScanPlan(mndp=False))
    assert result.devices == () and result.results == ()


def test_a_subnet_too_large_fails_its_source_rather_than_the_scan():
    plan = quick(mndp=True, subnets=("10.0.0.0/8",), scan_ports=True)
    result = run(plan, mndp_factory=lambda: FakeScanner([announced()]))
    assert [d.identity for d in result.devices] == ["sxt-01"]
    broken = result.of(Source.SCAN)
    assert broken is not None and "more than the limit" in broken.error


def test_devices_are_stamped_with_when_they_were_seen():
    result = run(quick(), mndp_factory=lambda: FakeScanner([announced()]),
                 now=lambda: 1234.0)
    assert result.devices[0].last_seen == 1234.0


def test_two_devices_on_one_address_come_back_flagged():
    plan = quick(mndp=False, subnets=("192.0.2.0/30",), snmp_community="public")
    # Not reachable through one source in practice; the merge is what is being checked.
    result = run(plan, snmp_ask=lambda address, community, *, timeout_s: SnmpReply(
        address, "1.3.6.1.4.1.14988.1", "RouterOS", f"dev-{address[-1]}"))
    assert len(result.devices) == 2 and all(not d.conflicts for d in result.devices)


# ------------------------------------------------------------------ logging in


def device_inventory(identity: str = "sxt-01", serial: str = "SN1"):
    inventory = make_inventory("7.15.3", architecture="arm", model="RBSXTsq5nD",
                               free_hdd_space=256 * MB,
                               radios=make_radios(("wlan1", "wireless", "station-bridge")),
                               packages=[PackageInfo(name="routeros", version="7.15.3"),
                                         PackageInfo(name="wireless", version="7.15.3")])
    board = inventory.routerboard.model_copy(update={"serial_number": serial})
    return inventory.model_copy(update={"identity": identity, "routerboard": board})


def test_logging_in_is_what_makes_a_device_updatable():
    result = run(quick(), mndp_factory=lambda: FakeScanner([announced()]))
    done = confirm(result.devices, lambda device: device_inventory())
    assert done[0].confidence is Confidence.CONFIRMED
    assert done[0].confidence.updatable and done[0].serial_number == "SN1"
    assert done[0].radios.collected


def test_a_refusal_is_recorded_and_not_tried_again():
    """A device that locks an account out after three goes is worse off for the retry."""
    attempts = {"n": 0}

    def open_device(device):
        attempts["n"] += 1
        raise PermissionError("invalid user name or password")

    result = run(quick(), mndp_factory=lambda: FakeScanner([announced()]))
    once = confirm(result.devices, open_device)
    assert once[0].login_error == "invalid user name or password"
    assert once[0].confidence is Confidence.PROBABLE

    twice = confirm(once, open_device)
    assert attempts["n"] == 1  # not asked a second time
    assert twice[0].login_error


def test_a_device_already_read_is_not_read_again():
    asked = {"n": 0}

    def open_device(device):
        asked["n"] += 1
        return device_inventory()

    result = run(quick(), mndp_factory=lambda: FakeScanner([announced()]))
    once = confirm(result.devices, open_device)
    confirm(once, open_device)
    assert asked["n"] == 1


def test_a_login_that_answers_with_nothing_counts_as_a_refusal():
    result = run(quick(), mndp_factory=lambda: FakeScanner([announced()]))
    done = confirm(result.devices, lambda device: None)
    assert done[0].login_error == "no answer"


def test_logging_in_by_hand_reads_again_what_was_read_before():
    """Picked by hand is read by hand: that is how a device's state gets refreshed."""
    asked = {"n": 0}

    def open_device(device):
        asked["n"] += 1
        return device_inventory()

    result = run(quick(), mndp_factory=lambda: FakeScanner([announced()]))
    once = log_in(result.devices, open_device, now=lambda: 50.0)
    twice = log_in(once, open_device, now=lambda: 60.0)
    assert asked["n"] == 2
    assert twice[0].confidence is Confidence.CONFIRMED and twice[0].last_seen == 60.0


def test_logging_in_by_hand_retries_a_refusal_and_clears_it():
    def refuse(device):
        raise PermissionError("invalid user name or password")

    result = run(quick(), mndp_factory=lambda: FakeScanner([announced()]))
    refused_once = log_in(result.devices, refuse)
    assert refused_once[0].login_error
    fixed = log_in(refused_once, lambda device: device_inventory())
    assert fixed[0].login_error == "" and fixed[0].confidence is Confidence.CONFIRMED


def test_a_device_that_refuses_now_loses_what_an_earlier_login_read():
    """An update is planned only from what can be read today."""
    result = run(quick(), mndp_factory=lambda: FakeScanner([announced()]))
    read = log_in(result.devices, lambda device: device_inventory())

    def refuse(device):
        raise PermissionError("invalid user name or password")

    now = log_in(read, refuse)
    assert now[0].confidence is Confidence.PROBABLE
    assert now[0].login_error == "invalid user name or password"


def test_a_cancelled_login_leaves_the_rest_as_they_were():
    result = run(quick(), mndp_factory=lambda: FakeScanner(
        [announced(), announced(MAC_B, "lhg-02")]))
    calls: list[str] = []

    def open_device(device):
        calls.append(device.identity)
        return device_inventory(device.identity, serial=device.identity)

    done = log_in(result.devices, open_device, cancelled=lambda: bool(calls))
    assert len(calls) == 1 and len(done) == 2
    assert done[1] == result.devices[1]


def test_what_a_device_sees_joins_the_list_and_draws_the_tree():
    ap = log_in(run(quick(), mndp_factory=lambda: FakeScanner([announced()])).devices,
                lambda device: device_inventory("ap-1", serial="AP1"))
    station = Sighting(source=Source.REGISTRATION, found_at="ap-1", macs=[MAC_B],
                       identity="cpe-1", via_interface="wlan1")
    devices = with_neighbours(ap, [station])
    assert [d.identity for d in devices] == ["ap-1", "cpe-1"]
    assert devices[1].confidence is Confidence.PROBABLE
    assert build(devices).links[0].child == devices[1].key


def test_two_rows_become_one_once_a_serial_is_known():
    """The payoff of logging in: the SNMP row and the MNDP row are the same box."""
    plan = quick(subnets=("192.0.2.0/30",), snmp_community="public")
    scanner = FakeScanner([announced()])
    result = run(plan, mndp_factory=lambda: scanner,
                 snmp_ask=lambda address, community, *, timeout_s: SnmpReply(
                     address, "1.3.6.1.4.1.14988.1", "RouterOS", "sxt-01")
                 if address == "192.0.2.1" else None)
    assert len(result.devices) == 2
    done = confirm(result.devices, lambda device: device_inventory())
    assert len(done) == 1 and done[0].serial_number == "SN1"
