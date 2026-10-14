from dataclasses import replace
from datetime import datetime
from ipaddress import IPv4Address, IPv4Interface

import pytest

from factories import FIVE_PORTS, make_inventory, router_profile
from fake_transport import FakeTransport
from mqs.apply.adopt import ADOPT_KEYS, adopt_existing, item_key
from mqs.apply.plan import (
    ApplyMode,
    ApplyOptions,
    build_upload_script,
    make_names,
    old_backups,
    result_for_mode,
)
from mqs.apply.preflight import ExistingObjects, collect_existing, preflight, prepare_import
from mqs.apply.rollback import (
    DateStyle,
    cancel_rollback,
    format_device_date,
    parse_device_clock,
    rollback_script,
    schedule_rollback,
)
from mqs.generator.base import generate
from mqs.generator.context import TargetContext
from mqs.generator.hardening import binding
from mqs.generator.hardening.access import DEFAULT_SERVICE_PORTS
from mqs.generator.ir import Find
from mqs.generator.render import render_inline
from mqs.issues import Severity, codes, has_errors
from mqs.messages_cs import MESSAGES
from mqs.model.bridge import LanAddressing
from mqs.model.inventory import ServiceInfo
from mqs.model.ports import rename_port
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.validation import validate_profile
from mqs.model.wan import PppoeWan, WanType

NOW = datetime(2026, 9, 15, 14, 5, 9)

# ---------------------------------------------------------------- names and script


def test_names_with_and_without_flash():
    names = make_names(NOW, has_flash_dir=False)
    assert names.script_file == "quickset-20260915-140509.rsc"
    assert names.remote_script == "quickset-20260915-140509.rsc"
    assert names.backup_name == "pre-quickset-20260915-140509"
    assert names.backup_file.endswith(".backup") and names.export_file.endswith(".rsc")
    flash = make_names(NOW, has_flash_dir=True)
    assert flash.remote_script == "flash/quickset-20260915-140509.rsc"
    assert flash.backup_file == "flash/pre-quickset-20260915-140509.backup"


def test_backup_names_carry_the_identity():
    names = make_names(NOW, has_flash_dir=True, identity="R1")
    assert names.backup_file == "flash/pre-quickset-R1-20260915-140509.backup"
    assert names.export_file == "flash/pre-quickset-R1-20260915-140509.rsc"
    assert names.script_file == "quickset-20260915-140509.rsc"  # the script stays as it was
    # Free text made safe for a file name: no diacritics, spaces or path separators.
    assert make_names(NOW, False, "Kancelář 2.patro/AP").device == "Kancelar_2_patro_AP"
    assert make_names(NOW, False, "  --  ").backup_name == "pre-quickset-20260915-140509"
    assert len(make_names(NOW, False, "x" * 80).device) == 32


def test_the_import_script_removes_itself_last():
    """A file still there after the import is how a stop half-way shows: over SSH,
    6.49.10 answers a failing /import with nothing (2026-09-25)."""
    names = make_names(NOW, has_flash_dir=True)
    text = build_upload_script("/system identity\nset name=R1\n",
                               ApplyOptions(ApplyMode.IMPORT), names)
    assert text == ("/system identity\nset name=R1\n# ---- CLEANUP ----\n"
                    f'/file remove [ find name="{names.remote_script}" ]\n')
    assert build_upload_script("x\n", ApplyOptions(ApplyMode.GENERATE_ONLY), names) == "x\n"


def test_reset_script_wrapping():
    names = make_names(NOW, has_flash_dir=True)
    options = ApplyOptions(ApplyMode.RESET_AND_IMPORT, reset_delay_s=20,
                           admin_password='pa ss"1')
    text = build_upload_script("/system identity\nset name=R1\n", options, names)
    lines = text.splitlines()
    assert lines[1] == ":delay 20s"
    # The password first: a failing line stops the script (7.23.7, 2026-09-24).
    assert lines[3] == '/user set [ find name=admin ] password="pa ss\\"1"'
    assert lines[4:6] == ["/system identity", "set name=R1"]
    assert lines[-1] == '/file remove [ find name="flash/quickset-20260915-140509.rsc" ]'
    masked = build_upload_script("x\n", options, names, mask_secrets=True)
    assert "pa ss" not in masked and 'password="********"' in masked
    no_password = build_upload_script("x\n", ApplyOptions(ApplyMode.RESET_AND_IMPORT), names)
    assert "/user set" not in no_password


def test_old_backups_by_the_stamp_in_their_name():
    files = [
        "pre-quickset-20260921-234537.rsc", "pre-quickset-20260921-234537.backup",
        # Written right after a reset, before NTP: the file date says January, the name not.
        "pre-quickset-20260921-212313.rsc", "pre-quickset-20260921-212313.backup",
        "flash/pre-quickset-20260915-205605.backup",
        "pre-quickset-20260920-143657.rsc",
        "quickset-20260915-205605.rsc",  # an uploaded script, not a backup
        # With the identity in the name; the older ones above have none.
        "flash/pre-quickset-R1-20260922-080000.backup", "pre-quickset-R-2-20260922-090000.rsc",
        "pre-quickset-x.backup", "skins", "my.backup",
    ]
    assert old_backups(files, 4, "20260921-234537") == [
        "flash/pre-quickset-20260915-205605.backup", "pre-quickset-20260920-143657.rsc"]
    # The current backup stays even when it is not among the newest (clock set back).
    assert "pre-quickset-20260921-234537.rsc" not in old_backups(files, 1, "20260921-234537")
    assert old_backups(files, 3, "20200101-000000") == [
        "flash/pre-quickset-20260915-205605.backup",
        "pre-quickset-20260920-143657.rsc",
        "pre-quickset-20260921-212313.backup", "pre-quickset-20260921-212313.rsc"]
    assert old_backups(files, 0, "20260921-234537") == []


# ---------------------------------------------------------------- rollback


@pytest.mark.parametrize(
    ("date", "time", "expected", "style"),
    [
        ("2026-09-15", "23:58:30", datetime(2026, 9, 15, 23, 58, 30), DateStyle.ISO),
        ("sep/15/2026", "08:00:00", datetime(2026, 9, 15, 8, 0, 0), DateStyle.LEGACY),
        ("Dec/31/2026", "23:59:59", datetime(2026, 12, 31, 23, 59, 59), DateStyle.LEGACY),
    ],
)
def test_parse_device_clock(date, time, expected, style):
    assert parse_device_clock(date, time) == (expected, style)


@pytest.mark.parametrize(("date", "time"), [("15.9.2026", "10:00:00"), ("2026-09-15", "10:00"),
                                            ("abc/15/2026", "10:00:00")])
def test_parse_device_clock_invalid(date, time):
    with pytest.raises(ValueError):
        parse_device_clock(date, time)


def test_schedule_rollback_crosses_midnight_in_device_format():
    command, run_at = schedule_rollback("dec/31/2026", "23:58:00", 5,
                                        "pre-quickset-x", "mqs-rollback")
    assert run_at == datetime(2027, 1, 1, 0, 3, 0)
    assert command.arg("start-date") == "jan/01/2027"
    assert command.arg("start-time") == "00:03:00"
    assert command.arg("interval") == "0"
    assert command.arg("name") == "mqs-rollback"
    iso, _ = schedule_rollback("2026-09-15", "10:00:00", 10, "b", "j")
    assert iso.arg("start-date") == "2026-09-15"
    assert format_device_date(datetime(2026, 2, 3), DateStyle.LEGACY) == "feb/03/2026"
    with pytest.raises(ValueError):
        schedule_rollback("2026-09-15", "10:00:00", 0, "b", "j")


def test_rollback_job_removes_itself_before_loading_backup():
    script = rollback_script("mqs-rollback", "pre-quickset-x")
    remove_at = script.index("/system scheduler remove")
    load_at = script.index("/system backup load")
    assert remove_at < load_at
    assert 'name=pre-quickset-x password=""' in script
    command, _ = schedule_rollback("2026-09-15", "10:00:00", 5, "pre-quickset-x", "mqs-rollback")
    line = render_inline(command)
    assert line.startswith("/system scheduler add name=mqs-rollback")
    assert "\n" not in line
    assert render_inline(cancel_rollback("mqs-rollback")) == \
        "/system scheduler remove [ find name=mqs-rollback ]"


# ---------------------------------------------------------------- preflight


def _setup(version="7.15.3", profile=None):
    profile = profile or router_profile(FIVE_PORTS, identity="R1")
    inventory = make_inventory(version, FIVE_PORTS)
    result = generate(profile, TargetContext.from_inventory(inventory))
    return profile, inventory, result


def test_generate_only_needs_no_device():
    profile, _, result = _setup()
    issues = preflight(profile, result, validate_profile(profile),
                       ApplyOptions(ApplyMode.GENERATE_ONLY), None)
    assert not has_errors(issues)


def test_import_happy_path_notices():
    profile, inventory, result = _setup()
    issues = preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT), inventory)
    assert not has_errors(issues)
    assert {"apply.notice_ssh_port", "apply.notice_services_disabled",
            "apply.notice_api_ssl_kept", "apply.notice_rest_api",
            "apply.notice_remaining_paths"} <= codes(issues)
    ssh = next(i for i in issues if i.code == "apply.notice_ssh_port")
    assert ssh.params["port"] == 122
    disabled = next(i for i in issues if i.code == "apply.notice_services_disabled")
    assert disabled.params["services"] == "api, ftp, telnet, www, www-ssl"
    remaining = next(i for i in issues if i.code == "apply.notice_remaining_paths")
    assert remaining.params == {"count": 3, "paths": "api-ssl 8729, ssh 122, winbox 8291"}


def test_not_connected_and_blocking_errors():
    profile, _, result = _setup()
    issues = preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT), None)
    assert codes(issues) == {"apply.not_connected"}
    bad = profile.model_copy(update={"identity": ""})
    issues = preflight(bad, result, validate_profile(bad), ApplyOptions(ApplyMode.IMPORT), None)
    assert "apply.blocked_by_errors" in codes(issues)


def test_dialect_mismatch_requires_confirmation():
    profile, _, result = _setup("7.15.3")
    v6_inventory = make_inventory("6.49.10", FIVE_PORTS)
    issues = preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT), v6_inventory)
    assert "apply.dialect_mismatch_unconfirmed" in codes(issues) and has_errors(issues)
    confirmed = ApplyOptions(ApplyMode.IMPORT, confirm_dialect_mismatch=True)
    issues = preflight(profile, result, [], confirmed, v6_inventory)
    assert "apply.dialect_mismatch" in codes(issues) and not has_errors(issues)
    minor = make_inventory("7.16", FIVE_PORTS)
    assert "apply.target_version_differs" in codes(
        preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT), minor))


def test_reset_checks():
    profile, inventory, result = _setup()
    inventory = inventory.model_copy(update={"identity": "MikroTik"})
    bad = ApplyOptions(ApplyMode.RESET_AND_IMPORT, confirmed_identity="mikrotik",
                       rollback_minutes=5)
    issues = codes(preflight(profile, result, [], bad, inventory))
    assert {"apply.reset_identity_mismatch", "apply.reset_password_required",
            "apply.rollback_ignored_reset"} <= issues
    good = ApplyOptions(ApplyMode.RESET_AND_IMPORT, confirmed_identity="MikroTik",
                        admin_password="x")
    assert not has_errors(preflight(profile, result, [], good, inventory))
    # Keeping the users keeps admin's password too, so a new one is optional.
    kept = ApplyOptions(ApplyMode.RESET_AND_IMPORT, confirmed_identity="MikroTik",
                        keep_users=True)
    issues = preflight(profile, result, [], kept, inventory)
    assert not has_errors(issues) and "apply.reset_keep_users" in codes(issues)


def device_with_ssh_off():
    """A device whose owner switched ssh off, as the hEX S had been on 2026-09-21."""
    services = [ServiceInfo(name=name, port=port, disabled=name in ("ssh", "www-ssl"))
                for name, port in DEFAULT_SERVICE_PORTS.items()]
    return make_inventory("7.20.7", FIVE_PORTS, services=services)


def test_a_reset_is_judged_against_factory_services():
    """A reset puts /ip service back to factory defaults before the script runs, so an
    ssh switched off beforehand comes back on: the script only moves its port. Judged
    against the device as it was, the checklist called the right result wrong."""
    profile = router_profile(FIVE_PORTS, identity="R1")
    result = generate(profile, TargetContext.from_inventory(device_with_ssh_off()))
    assert result.access.services["ssh"].disabled  # an import does leave it off

    reset = result_for_mode(result, ApplyMode.RESET_AND_IMPORT)
    ssh = reset.access.services["ssh"]
    assert not ssh.disabled and ssh.port == 122
    assert reset.access.remaining == ("api-ssl", "ssh", "winbox")
    assert "access.path_disabled" in codes(result.issues)
    assert "access.path_disabled" not in codes(reset.issues)
    assert reset.text == result.text
    assert result_for_mode(result, ApplyMode.IMPORT) is result


def test_preflight_of_a_reset_names_the_paths_the_reset_leaves():
    profile = router_profile(FIVE_PORTS, identity="R1")
    inventory = device_with_ssh_off()
    result = generate(profile, TargetContext.from_inventory(inventory))
    options = ApplyOptions(ApplyMode.RESET_AND_IMPORT, confirmed_identity=inventory.identity,
                           admin_password="x")
    remaining = next(i for i in preflight(profile, result, [], options, inventory)
                     if i.code == "apply.notice_remaining_paths")
    assert remaining.params == {"count": 3, "paths": "api-ssl 8729, ssh 122, winbox 8291"}


def test_lockout_warnings_for_a_tagged_wan():
    """A VLAN takes the port's address away just like PPPoE does, and says so."""
    profile, inventory, _ = _setup()  # WAN = ether1, bridge = ether2..5
    tagged = profile.model_copy(update={"wan": profile.wan.model_copy(update={"vlan_id": 848})})
    tagged_result = generate(tagged, TargetContext.from_inventory(inventory))
    issue = next(i for i in preflight(tagged, tagged_result, [], ApplyOptions(ApplyMode.IMPORT),
                                      inventory, session_port="ether1")
                 if i.code.startswith("apply.lockout_wan"))
    assert issue.code == "apply.lockout_wan_vlan" and issue.severity == Severity.WARNING
    assert issue.params["vlan"] == 848 and issue.params["interface"] == "vlan848"

    by_mac = preflight(tagged, tagged_result, [], ApplyOptions(ApplyMode.IMPORT), inventory,
                       session_port="ether1", by_mac=True)
    assert "apply.session_by_mac_on_wan_vlan" in codes(by_mac)

    # PPPoE in a VLAN keeps the PPPoE wording: both reasons hold, one message is enough.
    pppoe = tagged.model_copy(update={"wan": tagged.wan.model_copy(update={
        "settings": PppoeWan(username="u")})})
    assert "apply.lockout_wan_pppoe" in codes(preflight(
        pppoe, generate(pppoe, TargetContext.from_inventory(inventory)), [],
        ApplyOptions(ApplyMode.IMPORT), inventory, session_port="ether1"))


def test_rollback_minimum():
    profile, inventory, result = _setup()
    issues = preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT, rollback_minutes=1),
                       inventory)
    assert "apply.rollback_too_short" in codes(issues)


def test_lockout_warnings():
    profile, inventory, result = _setup()  # WAN = ether1, bridge = ether2..5
    on_wan = next(i for i in preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT),
                                       inventory, session_port="ether1")
                  if i.code.startswith(("apply.lockout_wan", "apply.session_on_wan")))
    # Management from WAN is on by default: the session comes back, nothing to warn about.
    assert on_wan.code == "apply.session_on_wan" and on_wan.severity == Severity.INFO
    assert on_wan.params["paths"] == "api-ssl 8729, ssh 122, winbox 8291"

    closed = profile.model_copy(update={"firewall": profile.firewall.model_copy(
        update={"wan_management": False})})
    closed_result = generate(closed, TargetContext.from_inventory(inventory))
    issues = preflight(closed, closed_result, [], ApplyOptions(ApplyMode.IMPORT), inventory,
                       session_port="ether1")
    assert "apply.lockout_wan" in codes(issues)

    no_filter = closed.model_copy(update={"firewall": closed.firewall.model_copy(
        update={"basic_filter": False})})
    issues = preflight(no_filter, generate(no_filter, TargetContext.from_inventory(inventory)),
                       [], ApplyOptions(ApplyMode.IMPORT), inventory, session_port="ether1")
    assert "apply.session_on_wan" in codes(issues)

    # IPv6 on: its input chain closes WAN over IPv6 even without the basic filter.
    ipv6 = no_filter.model_copy(update={"firewall": no_filter.firewall.model_copy(
        update={"ipv6": True})})
    issues = preflight(ipv6, generate(ipv6, TargetContext.from_inventory(inventory)),
                       [], ApplyOptions(ApplyMode.IMPORT), inventory, session_port="ether1")
    assert "apply.lockout_wan_ipv6" in codes(issues)
    assert "apply.session_on_wan" not in codes(issues)
    managed = ipv6.model_copy(update={"firewall": ipv6.firewall.model_copy(
        update={"wan_management": True})})
    issues = preflight(managed, generate(managed, TargetContext.from_inventory(inventory)),
                       [], ApplyOptions(ApplyMode.IMPORT), inventory, session_port="ether1")
    assert "apply.session_on_wan" in codes(issues)

    pppoe = profile.model_copy(update={"wan": profile.wan.model_copy(update={
        "settings": PppoeWan(username="u")})})
    pppoe_result = generate(pppoe, TargetContext.from_inventory(inventory))
    issues = preflight(pppoe, pppoe_result, [], ApplyOptions(ApplyMode.IMPORT), inventory,
                       session_port="ether1")
    assert "apply.lockout_wan_pppoe" in codes(issues)
    # By MAC it comes back over an address lent to the port, if the filter lets it in
    # (the v6 hEX S did after a reset to PPPoE, 2026-09-25).
    by_mac = preflight(pppoe, pppoe_result, [], ApplyOptions(ApplyMode.IMPORT), inventory,
                       session_port="ether1", by_mac=True)
    assert "apply.lockout_wan_pppoe" not in codes(by_mac)
    assert "apply.session_by_mac_on_wan_pppoe" in codes(by_mac)
    shut = pppoe.model_copy(update={"firewall": pppoe.firewall.model_copy(
        update={"wan_management": False})})
    assert "apply.lockout_wan_pppoe" in codes(preflight(
        shut, generate(shut, TargetContext.from_inventory(inventory)), [],
        ApplyOptions(ApplyMode.IMPORT), inventory, session_port="ether1", by_mac=True))
    reset = ApplyOptions(ApplyMode.RESET_AND_IMPORT, confirmed_identity="MikroTik",
                         admin_password="x")
    other = profile.model_copy(update={"bridge": profile.bridge.model_copy(
        update={"ports": ["ether2"]})})
    result = generate(other, TargetContext.from_inventory(inventory))
    issues = preflight(other, result, [], reset, inventory, session_port="ether4")
    assert "apply.lockout_unconfigured_port" in codes(issues)
    assert "apply.lockout_unconfigured_port" not in codes(
        preflight(other, result, [], reset, inventory, session_port="ether2"))


def _same_as_script(result) -> ExistingObjects:
    """The device after an earlier apply of the same script: every add already there."""
    items: dict[str, set[tuple[str, ...]]] = {}
    ports = {}
    for command in result.script.commands():
        if command.verb == "add" and command.path in ADOPT_KEYS:
            args = dict(command.args)
            items.setdefault(command.path, set()).add(item_key(command.path, args))
            if command.path == "/interface bridge port":
                ports[args["interface"]] = args["bridge"]
    return ExistingObjects({p: frozenset(v) for p, v in items.items()}, ports)


def test_import_adopts_what_an_earlier_apply_left():
    profile, inventory, result = _setup()
    existing = _same_as_script(result)
    options = ApplyOptions(ApplyMode.IMPORT)
    issues = preflight(profile, result, [], options, inventory, existing=existing)
    assert not has_errors(issues)
    adopted = next(i for i in issues if i.code == "apply.adopted")
    assert "/interface bridge bridge1" in adopted.params["items"]
    assert "apply.bridge_port_moved" not in codes(issues)

    again = prepare_import(result, options, existing)
    # Nothing is added twice, except what is replaced by its comment (the firewall).
    adds = [c for c in again.script.commands() if c.verb == "add"]
    assert {c.path for c in adds} <= {"/ip firewall filter", "/ip firewall nat",
                                      "/ip firewall mangle"}, [c.path for c in adds]
    text = again.text
    assert "set [ find name=bridge1 ] protocol-mode=none" in text
    assert "set [ find interface=ether2 ] bridge=bridge1" in text
    # Quoted: a bare address in a find matched no /ip address on 7.20.7.
    assert 'set [ find address="192.168.88.1/24" ] interface=bridge1' in text
    assert "set [ find name=dhcp1 ] interface=bridge1" in text
    assert "add name=WAN" not in text and "add interface=ether1 list=WAN" not in text
    assert text.startswith(result.text.split("# ---- ")[0])  # the header is kept

    # Only on a running configuration, and nothing to adopt leaves the script alone.
    assert prepare_import(result, ApplyOptions(ApplyMode.RESET_AND_IMPORT), existing) is result
    assert prepare_import(result, options, ExistingObjects()) is result
    assert prepare_import(result, options, None) is result


def test_import_warns_about_moving_a_port_between_bridges():
    profile, inventory, result = _setup()
    existing = ExistingObjects({"/interface bridge port": frozenset({("ether2",)})},
                               {"ether2": "bridge"})
    issues = preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT), inventory,
                       existing=existing)
    moved = next(i for i in issues if i.code == "apply.bridge_port_moved")
    assert moved.params == {"port": "ether2", "old": "bridge", "new": "bridge1"}


def test_import_switches_off_vlan_filtering_the_profile_does_not_have():
    """v6 hEX S, 2026-09-25: importing a profile without vlan-filtering onto a bridge an
    earlier one had set it on left it on, and the MT7621 then offloaded no port."""
    profile, inventory, result = _setup()
    existing = _same_as_script(result)
    options = ApplyOptions(ApplyMode.IMPORT)
    assert "vlan-filtering" not in prepare_import(result, options, existing).text

    filtering = replace(existing, vlan_filtering=frozenset({"bridge1"}))
    issues = preflight(profile, result, [], options, inventory, existing=filtering)
    assert "apply.vlan_filtering_off" in codes(issues)
    text = prepare_import(result, options, filtering).text
    assert "set [ find name=bridge1 ] protocol-mode=none vlan-filtering=no" in text

    # A profile that filters writes its own value; another bridge is not touched.
    other = replace(existing, vlan_filtering=frozenset({"bridge7"}))
    assert "vlan-filtering" not in prepare_import(result, options, other).text


def test_collect_existing_from_transport():
    fake = FakeTransport({
        "/interface/bridge/print": [{"name": "bridge"},
                                    {"name": "br-vlan", "vlan-filtering": "true"}],
        "/interface/list/print": [{"name": "WAN"}, {"comment": "no name"}],
        "/interface/list/member/print": [
            {"list": "WAN", "interface": "ether1"},
            {"list": "LAN", "interface": "bridge", "dynamic": "true"}],
        "/ip/address/print": [{"address": "192.168.88.1/24"}, {"address": "bogus"},
                              {"address": "192.168.55.132/24", "_flags": "D"}],
        "/interface/bridge/port/print": [{"interface": "ether2", "bridge": "bridge"}],
        "/ip/dhcp-client/print": [{"interface": "ether1"}],
    })
    existing = collect_existing(fake)
    assert existing.items["/interface bridge"] == {("bridge",), ("br-vlan",)}
    assert existing.vlan_filtering == {"br-vlan"}
    assert existing.items["/interface list"] == {("WAN",)}
    assert existing.items["/interface list member"] == {("WAN", "ether1")}  # dynamic left out
    assert existing.items["/ip address"] == {("192.168.88.1/24",), ("bogus",)}
    assert not existing.has("/ip pool", ("dhcp_pool",))  # a menu that failed to print
    assert existing.bridge_ports == {"ether2": "bridge"}
    assert collect_existing(FakeTransport({})).bridge_ports == {}


def test_collect_existing_counts_our_nat_and_mangle_rules():
    fake = FakeTransport({
        "/ip/firewall/nat/print": [
            {"comment": "quickset: masquerade to WAN", "chain": "srcnat"},
            {"comment": "port forward to NAS", "chain": "dstnat"}],
        "/ip/firewall/mangle/print": [
            {"comment": "special dummy rule to show fasttrack counters", "dynamic": "true"},
            {"comment": "quickset: clamp MSS to path MTU", "chain": "forward"}],
    })
    assert collect_existing(fake).own_rules == {"/ip firewall nat": 1,
                                                "/ip firewall mangle": 1}
    assert collect_existing(FakeTransport({})).own_rules == {}


def _static_wan_result(gateway: str):
    profile = router_profile(FIVE_PORTS, identity="R1")
    wan = profile.wan.with_type(WanType.STATIC)
    wan = wan.model_copy(update={"settings": wan.settings.model_copy(update={
        "address": IPv4Interface("192.0.2.2/24"), "gateway": IPv4Address(gateway)})})
    return _setup(profile=profile.model_copy(update={"wan": wan}))[2]


def test_import_moves_a_route_to_the_new_gateway():
    # On the hEX S the add left the old default route too: two of them, both active.
    result = _static_wan_result("192.0.2.9")
    options = ApplyOptions(ApplyMode.IMPORT)
    existing = ExistingObjects({"/ip route": frozenset({("0.0.0.0/0", "192.0.2.1")})})
    text = prepare_import(result, options, existing).text
    assert ('/ip route\nset [ find dst-address="0.0.0.0/0" gateway="192.0.2.1" dynamic=no ] '
            'gateway=192.0.2.9') in text
    assert "add dst-address=0.0.0.0/0" not in text
    adopted = next(i for i in adopt_existing(result, existing)[1] if i.code == "apply.adopted")
    assert "/ip route 0.0.0.0/0 192.0.2.1 → 192.0.2.9" in adopted.params["items"]

    # The same gateway: nothing to do. Several routes (failover): not ours to pick one.
    same = ExistingObjects({"/ip route": frozenset({("0.0.0.0/0", "192.0.2.9")})})
    assert "/ip route" not in prepare_import(result, options, same).text.split("---- FIREWALL")[0]
    pair = ExistingObjects({"/ip route": frozenset({("0.0.0.0/0", "192.0.2.1"),
                                                   ("0.0.0.0/0", "192.0.2.5")})})
    assert "add dst-address=0.0.0.0/0 gateway=192.0.2.9" in prepare_import(
        result, options, pair).text
    warning = next(i for i in adopt_existing(result, pair)[1]
                   if i.code == "apply.route_ambiguous")
    assert warning.severity == Severity.WARNING
    assert warning.params == {"dst": "0.0.0.0/0", "gateway": "192.0.2.9",
                              "gateways": "192.0.2.1, 192.0.2.5"}
    assert "{" not in warning.message


def _bridge_profile(addressing=LanAddressing.STATIC):
    profile = apply_preset(DeviceProfile(identity="AP1"), Mode.BRIDGE_AP, FIVE_PORTS)
    if addressing == LanAddressing.STATIC:
        lan = profile.lan.with_addressing(LanAddressing.STATIC).model_copy(
            update={"address": IPv4Interface("192.168.55.68/24")})
        profile = profile.model_copy(update={"lan": lan})
    return profile


def test_import_cannot_turn_a_bridge_into_a_router():
    # The WAN address would sit on a bridge port: nothing takes the port out of the bridge.
    profile, inventory, result = _setup()
    existing = ExistingObjects(bridge_ports={"ether1": "br-ap", "ether2": "br-ap"})
    issues = preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT), inventory,
                       existing=existing)
    blocked = next(i for i in issues if i.code == "apply.import_wan_port_bridged")
    assert blocked.severity == Severity.ERROR
    assert blocked.params == {"port": "ether1", "bridge": "br-ap"}
    assert "{" not in blocked.message
    # A reset starts from nothing and is not concerned.
    reset = ApplyOptions(ApplyMode.RESET_AND_IMPORT, confirmed_identity=inventory.identity,
                         keep_users=True)
    assert "apply.import_wan_port_bridged" not in codes(
        preflight(profile, result, [], reset, inventory, existing=existing))


def test_import_warns_when_a_router_becomes_a_bridge():
    profile = _bridge_profile()
    _, inventory, result = _setup(profile=profile)
    existing = ExistingObjects({"/interface list member": frozenset({
        ("WAN", "ether1"), ("LAN", "bridge")})})
    issues = preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT), inventory,
                       existing=existing)
    warning = next(i for i in issues if i.code == "apply.import_router_to_bridge")
    assert warning.severity == Severity.WARNING and warning.params == {"ports": "ether1"}
    assert not has_errors(issues)
    # Unknown until the device has been read: the window's live preflight says nothing.
    assert "apply.import_router_to_bridge" not in codes(
        preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT), inventory))


def test_import_warns_when_the_session_port_joins_the_bridge():
    # B0 on the hEX S: ether1 carried the session and its address, and joined the bridge.
    profile = _bridge_profile()
    _, inventory, result = _setup(profile=profile)
    options = ApplyOptions(ApplyMode.IMPORT)
    issues = preflight(profile, result, [], options, inventory,
                       existing=ExistingObjects(), session_port="ether1")
    moved = next(i for i in issues if i.code == "apply.session_port_to_bridge")
    assert moved.params == {"port": "ether1", "bridge": "bridge1", "address": "192.168.55.68"}
    assert "{" not in moved.message

    dhcp = _bridge_profile(LanAddressing.DHCP_CLIENT)
    _, _, dhcp_result = _setup(profile=dhcp)
    assert "apply.session_port_to_bridge_no_address" in codes(preflight(
        dhcp, dhcp_result, [], options, inventory, existing=ExistingObjects(),
        session_port="ether1"))
    # Already a bridge port, or a port that stays out of the bridge: nothing changes for it.
    for existing, port in ((ExistingObjects(bridge_ports={"ether1": "bridge1"}), "ether1"),
                           (ExistingObjects(), "ether9")):
        assert not {"apply.session_port_to_bridge", "apply.session_port_to_bridge_no_address"} \
            & codes(preflight(profile, result, [], options, inventory, existing=existing,
                              session_port=port))


def test_import_removes_our_rules_the_profile_no_longer_adds():
    # Seen on the hEX S: MSS clamping switched off, and the rule stayed after the import.
    _, _, result = _setup()
    assert result.script.find("/ip firewall nat", "add")  # the router profile masquerades
    assert not result.script.find("/ip firewall mangle", "add")  # and does not clamp
    existing = ExistingObjects(own_rules={"/ip firewall nat": 1, "/ip firewall mangle": 1})
    options = ApplyOptions(ApplyMode.IMPORT)
    changed = prepare_import(result, options, existing)
    mangle = [c for c in changed.script.commands() if c.path == "/ip firewall mangle"]
    assert [(c.verb, c.selector) for c in mangle] == [
        ("remove", Find.matching("comment", "^quickset: "))]
    assert '/ip firewall mangle\nremove [ find where comment~"^quickset: " ]' in changed.text
    # NAT is still added, and the script replaces its rule by itself: not removed twice.
    nat_removals = [c for c in changed.script.commands()
                    if c.path == "/ip firewall nat" and c.verb == "remove"]
    assert len(nat_removals) == 1
    issues = adopt_existing(result, existing)[1]
    assert [i.params for i in issues if i.code == "apply.own_rules_removed"] == [
        {"table": "/ip firewall mangle", "count": 1}]
    assert "{" not in issues[0].message

    # A reset starts from nothing, and a table without our rules needs nothing.
    assert prepare_import(result, ApplyOptions(ApplyMode.RESET_AND_IMPORT), existing) is result
    assert prepare_import(result, options, ExistingObjects(
        own_rules={"/ip firewall nat": 1})) is result


def test_import_removes_our_ipv6_rules_only_with_ipv6_switched_off():
    options = ApplyOptions(ApplyMode.IMPORT)
    existing = ExistingObjects(own_rules={"/ipv6 firewall filter": 16})

    # IPv6 off: the hardening block switches it off, then the old rules go.
    _, _, result = _setup()
    changed = prepare_import(result, options, existing)
    text = changed.text
    removal = '/ipv6 firewall filter\nremove [ find where comment~"^quickset: " ]'
    assert removal in text
    assert text.index("set disable-ipv6=yes") < text.index(removal)
    assert {"table": "/ipv6 firewall filter", "count": 16} in [
        i.params for i in adopt_existing(result, existing)[1]
        if i.code == "apply.own_rules_removed"]

    # IPv6 left on (the block no longer switches it off): the rules stay.
    profile = router_profile(FIVE_PORTS, identity="R1")
    block = profile.services.hardening_text.replace("/ipv6 settings\nset disable-ipv6=yes\n", "")
    kept_on = profile.model_copy(update={"services": profile.services.model_copy(
        update={"hardening_text": block})})
    _, _, result = _setup(profile=kept_on)
    assert prepare_import(result, options, existing) is result

    # IPv6 on: the firewall block replaces its rules itself.
    on = profile.model_copy(update={"firewall": profile.firewall.model_copy(
        update={"ipv6": True})})
    _, _, result = _setup(profile=on)
    changed = prepare_import(result, options, existing)
    assert len([c for c in changed.script.commands()
                if c.path == "/ipv6 firewall filter" and c.verb == "remove"]) == 1

    # v6 disables the package only at the next reboot, which an import does not do.
    _, _, result = _setup("6.49.10")
    assert "/ipv6 firewall filter\nremove" not in prepare_import(result, options, existing).text


def test_apply_messages_format():
    profile, _, result = _setup()
    text = binding.set_ssh_port(profile.services.hardening_text, 2222)
    profile = profile.model_copy(update={"services": profile.services.model_copy(
        update={"hardening_text": text})})
    existing = ExistingObjects({"/interface bridge": frozenset({("bridge1",)}),
                                "/interface bridge port": frozenset({("ether2",)})},
                               {"ether2": "old"})
    options = ApplyOptions(ApplyMode.RESET_AND_IMPORT, rollback_minutes=1)
    issues = preflight(profile, result, [], options, make_inventory("6.49", FIVE_PORTS),
                       session_port="ether1", existing=existing)
    issues += preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT, rollback_minutes=1),
                        make_inventory("6.49", FIVE_PORTS), session_port="ether1",
                        existing=existing)
    assert {"apply.adopted", "apply.bridge_port_moved"} <= codes(issues)
    for issue in issues:
        assert issue.code in MESSAGES and "{" not in issue.message, issue.code


def test_what_the_device_has_is_matched_under_the_names_the_script_gives():
    # ether1 becomes "internet" and ether2 "lan2"; the device still calls them ether1/2.
    profile = rename_port(rename_port(router_profile(FIVE_PORTS, identity="R1"),
                                      "ether1", "internet"), "ether2", "lan2")
    inventory = make_inventory("7.15.3", FIVE_PORTS)
    result = generate(profile, TargetContext.from_inventory(inventory))
    assert result.renames == {"ether1": "internet", "ether2": "lan2"}
    existing = ExistingObjects({"/interface bridge port": frozenset({("ether2",)}),
                                "/interface list member": frozenset({("WAN", "ether1")}),
                                "/ip dhcp-client": frozenset({("ether1",)})},
                               {"ether2": "bridge1"})
    options = ApplyOptions(ApplyMode.IMPORT)
    text = prepare_import(result, options, existing).text
    # The renames run first, so the finds after them use the new names.
    assert text.index("name=internet") < text.index("set [ find interface=lan2 ] bridge=bridge1")
    assert "add bridge=bridge1 interface=lan2" not in text
    assert "add interface=internet list=WAN" not in text
    assert "set [ find interface=internet ] use-peer-dns=yes" in text

    # The session comes in on ether1, which is the WAN port once renamed.
    issues = preflight(profile, result, [], options, inventory, session_port="ether1")
    assert "apply.session_on_wan" in codes(issues)
    # A bridge port the device has under the old name blocks it as the WAN port.
    bridged = ExistingObjects(bridge_ports={"ether1": "br-ap"})
    blocked = next(i for i in preflight(profile, result, [], options, inventory,
                                        existing=bridged)
                   if i.code == "apply.import_wan_port_bridged")
    assert blocked.params == {"port": "internet", "bridge": "br-ap"}
