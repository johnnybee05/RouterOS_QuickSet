from datetime import datetime

import pytest

from factories import FIVE_PORTS, make_inventory, router_profile
from fake_transport import FakeTransport
from mqs.apply.adopt import ADOPT_KEYS, item_key
from mqs.apply.plan import (
    ApplyMode,
    ApplyOptions,
    build_upload_script,
    make_names,
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
from mqs.generator.render import render_inline
from mqs.issues import Severity, codes, has_errors
from mqs.messages_cs import MESSAGES
from mqs.model.inventory import ServiceInfo
from mqs.model.validation import validate_profile
from mqs.model.wan import PppoeWan

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


def test_import_script_is_unchanged():
    options = ApplyOptions(ApplyMode.IMPORT)
    assert build_upload_script("/system identity\nset name=R1\n", options,
                               make_names(NOW, False)) == "/system identity\nset name=R1\n"


def test_reset_script_wrapping():
    names = make_names(NOW, has_flash_dir=True)
    options = ApplyOptions(ApplyMode.RESET_AND_IMPORT, reset_delay_s=20,
                           admin_password='pa ss"1')
    text = build_upload_script("/system identity\nset name=R1\n", options, names)
    lines = text.splitlines()
    assert lines[1] == ":delay 20s"
    assert lines[2:4] == ["/system identity", "set name=R1"]
    assert lines[-2] == '/user set [ find name=admin ] password="pa ss\\"1"'
    assert lines[-1] == '/file remove [ find name=flash/quickset-20260915-140509.rsc ]'
    masked = build_upload_script("x\n", options, names, mask_secrets=True)
    assert "pa ss" not in masked and 'password="********"' in masked
    no_password = build_upload_script("x\n", ApplyOptions(ApplyMode.RESET_AND_IMPORT), names)
    assert "/user set" not in no_password


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

    pppoe = profile.model_copy(update={"wan": profile.wan.model_copy(update={
        "settings": PppoeWan(username="u")})})
    issues = preflight(pppoe, generate(pppoe, TargetContext.from_inventory(inventory)), [],
                       ApplyOptions(ApplyMode.IMPORT), inventory, session_port="ether1")
    assert "apply.lockout_wan_pppoe" in codes(issues)
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
    assert "/interface bridge bridge" in adopted.params["items"]
    assert "apply.bridge_port_moved" not in codes(issues)

    again = prepare_import(result, options, existing)
    # Nothing is added twice, except what is replaced by its comment (the firewall).
    adds = [c for c in again.script.commands() if c.verb == "add"]
    assert {c.path for c in adds} <= {"/ip firewall filter", "/ip firewall nat",
                                      "/ip firewall mangle"}, [c.path for c in adds]
    text = again.text
    assert "set [ find name=bridge ] protocol-mode=rstp" in text
    assert "set [ find interface=ether2 ] bridge=bridge" in text
    assert "set [ find address=192.168.88.1/24 ] interface=bridge" in text
    assert "set [ find name=dhcp1 ] interface=bridge" in text
    assert "add name=WAN" not in text and "add interface=ether1 list=WAN" not in text
    assert text.startswith(result.text.split("# ---- ")[0])  # the header is kept

    # Only on a running configuration, and nothing to adopt leaves the script alone.
    assert prepare_import(result, ApplyOptions(ApplyMode.RESET_AND_IMPORT), existing) is result
    assert prepare_import(result, options, ExistingObjects()) is result
    assert prepare_import(result, options, None) is result


def test_import_warns_about_moving_a_port_between_bridges():
    profile, inventory, result = _setup()
    existing = ExistingObjects({"/interface bridge port": frozenset({("ether2",)})},
                               {"ether2": "bridge1"})
    issues = preflight(profile, result, [], ApplyOptions(ApplyMode.IMPORT), inventory,
                       existing=existing)
    moved = next(i for i in issues if i.code == "apply.bridge_port_moved")
    assert moved.params == {"port": "ether2", "old": "bridge1", "new": "bridge"}


def test_collect_existing_from_transport():
    fake = FakeTransport({
        "/interface/bridge/print": [{"name": "bridge"}],
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
    assert existing.items["/interface bridge"] == {("bridge",)}
    assert existing.items["/interface list"] == {("WAN",)}
    assert existing.items["/interface list member"] == {("WAN", "ether1")}  # dynamic left out
    assert existing.items["/ip address"] == {("192.168.88.1/24",), ("bogus",)}
    assert not existing.has("/ip pool", ("dhcp_pool",))  # a menu that failed to print
    assert existing.bridge_ports == {"ether2": "bridge"}
    assert collect_existing(FakeTransport({})).bridge_ports == {}


def test_apply_messages_format():
    profile, _, result = _setup()
    text = binding.set_ssh_port(profile.services.hardening_text, 2222)
    profile = profile.model_copy(update={"services": profile.services.model_copy(
        update={"hardening_text": text})})
    existing = ExistingObjects({"/interface bridge": frozenset({("bridge",)}),
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
