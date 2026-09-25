"""The device CLI, driven against the in-memory router (no hardware)."""

from __future__ import annotations

import io
from ipaddress import IPv4Address, IPv4Network

import pytest

from factories import make_inventory, make_npk
from fake_router import FakeRouter, RouterUploader
from fake_transport import FakeTransport
from mqs import cli
from mqs.apply.orchestrator import ApplyOrchestrator
from mqs.apply.plan import ApplyMode
from mqs.apply.session import DeviceSession
from mqs.generator.base import generate
from mqs.generator.context import TargetContext
from mqs.model.bridge import ProtocolMode
from mqs.model.inventory import PackageInfo
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.wan import WanType
from mqs.profiles.store import profile_to_json
from mqs.transport.base import AuthError, ConnectionParams, TransportKind
from mqs.transport.factory import ConnectAttempt, ConnectFailed
from mqs.transport.mac_connect import MacRoute
from mqs.transport.mndp import DiscoveredDevice
from mqs.transport.netifs import LocalInterface

PORTS = ["ether1", "ether2", "ether3", "ether4", "ether5"]


@pytest.fixture
def profile():
    return apply_preset(DeviceProfile(identity="PV-R1"), Mode.ROUTER, PORTS)


@pytest.fixture
def profile_file(profile, tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(profile_to_json(profile), encoding="utf-8")
    return path


@pytest.fixture
def router(profile):
    """A router that ends up in the state the generated script asks for."""
    inventory = make_inventory("7.15.3", PORTS)
    result = generate(profile, TargetContext.from_inventory(inventory))
    router = FakeRouter()
    router.pending_import_effects = {
        "identity": profile.identity,
        "ssh_port": result.access.ssh_port,
        "services_disabled": {n for n, s in result.access.services.items() if s.disabled},
        "bridges": {"bridge1": "none"},
        "addresses": [("192.168.88.1/24", "bridge1")],
        "ports": profile.bridge.ports,
        "dhcp_clients": ["ether1"],  # the router preset takes its WAN address by DHCP
        "admin_password": "",
    }
    return router


def run(argv, router=None, **extra):
    """Run the CLI with the device side faked out; returns (exit code, output)."""
    out = io.StringIO()
    deps = dict(extra)
    if router is not None:
        session = DeviceSession(
            ConnectionParams(host="10.0.0.1", username="admin"),
            router.session(), make_inventory("7.15.3", PORTS), {}, "ether9",
        )
        deps.setdefault("connect", lambda *a, **k: session)
        deps.setdefault("collect_existing_fn", lambda transport: None)

        def orchestrator_factory(ctx):
            ctx.uploader_factory = lambda transport: RouterUploader(router)
            return ApplyOrchestrator(ctx)

        deps.setdefault("orchestrator_factory", orchestrator_factory)
    return cli.main(argv, out=out, **deps), out.getvalue()


def test_generate_without_a_device_writes_the_script(profile_file, tmp_path):
    script = tmp_path / "out.rsc"
    code, out = run(["apply", "--profile-file", str(profile_file), "--mode", "generate",
                     "--out", str(script)])
    assert code == cli.OK, out
    assert "/interface bridge" in script.read_text(encoding="utf-8")


def test_generate_for_a_device_writes_the_script_and_reports_the_generator(
        router, profile, tmp_path):
    """--out and the generator's findings used to be offline only (2026-09-25)."""
    fasttrack = profile.firewall.fasttrack.model_copy(
        update={"enabled": True, "hw_offload": True, "no_shaping_confirmed": True})
    profile = profile.model_copy(update={"firewall": profile.firewall.model_copy(
        update={"fasttrack": fasttrack})})
    source = tmp_path / "profile.json"
    source.write_text(profile_to_json(profile), encoding="utf-8")
    script = tmp_path / "out.rsc"
    code, out = run(["apply", "--host", "10.0.0.1", "--profile-file", str(source),
                     "--mode", "generate", "--out", str(script)], router)
    assert code == cli.OK, out
    assert "/interface bridge" in script.read_text(encoding="utf-8")
    assert f"written to {script}" in out
    assert "fasttrack.hw_offload_skipped" in out  # no probe said yes


def test_an_apply_blocked_by_the_generator_says_why(router, profile, tmp_path):
    """Before, an import of MSTP without vlan-filtering onto RouterOS 6 printed only
    "apply.blocked_by_errors", without the error itself."""
    mstp = profile.model_copy(update={"bridge": profile.bridge.model_copy(
        update={"protocol_mode": ProtocolMode.MSTP})})
    source = tmp_path / "mstp.json"
    source.write_text(profile_to_json(mstp), encoding="utf-8")
    session = DeviceSession(ConnectionParams(host="10.0.0.1", username="admin"),
                            router.session(), make_inventory("6.49.10", PORTS), {}, "ether9")
    code, out = run(["apply", "--host", "10.0.0.1", "--profile-file", str(source),
                     "--mode", "import", "-y"], router, connect=lambda *a, **k: session)
    assert code == cli.BLOCKED, out
    assert "error   bridge.mstp_needs_vlan_filtering" in out
    assert "/import" not in router.log


def test_the_pppoe_password_comes_from_the_environment(profile, tmp_path, monkeypatch):
    """A saved profile leaves it out, so a PPPoE profile was blocked by
    "wan.pppoe.password_empty" in every CLI run."""
    wan = profile.wan.with_type(WanType.PPPOE)
    wan = wan.model_copy(update={"settings": wan.settings.model_copy(
        update={"username": "test@isp"})})
    source = tmp_path / "pppoe.json"
    source.write_text(profile_to_json(profile.model_copy(update={"wan": wan})),
                      encoding="utf-8")
    argv = ["apply", "--profile-file", str(source), "--mode", "generate", "--target", "6.49.10"]
    monkeypatch.delenv("MQS_PPPOE_PASSWORD", raising=False)
    code, out = run(argv)
    assert code == cli.BLOCKED and "wan.pppoe.password_empty" in out
    monkeypatch.setenv("MQS_PPPOE_PASSWORD", "s3cr3t")
    code, out = run(argv)
    assert code == cli.OK, out
    assert 'add name=pppoe-out1 interface=ether1 user="test@isp" password=s3cr3t' in out


def test_connect_describes_the_device(router):
    session = DeviceSession(ConnectionParams(host="10.0.0.1", username="admin"),
                            router.session(), make_inventory("7.15.3", PORTS), {}, "ether9")
    code, out = run(["connect", "--host", "10.0.0.1"], connect=lambda *a, **k: session)
    assert code == cli.OK
    assert "identity    : MikroTik" in out
    assert "version     : 7.15.3" in out


def test_import_runs_the_whole_flow(router, profile_file):
    code, out = run(["apply", "--host", "10.0.0.1", "--profile-file", str(profile_file),
                     "--mode", "import", "--yes"], router=router)
    assert code == cli.OK, out
    assert router.state.identity == "PV-R1"
    assert "preflight:" in out and "checklist:" in out
    assert "/import" in router.log


def test_a_change_is_refused_without_yes(router, profile_file, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    code, _out = run(["apply", "--host", "10.0.0.1", "--profile-file", str(profile_file),
                     "--mode", "import"], router=router)
    assert code == cli.REFUSED
    assert "/import" not in router.log, "the device was touched without confirmation"


def test_no_answer_is_not_a_yes(router, profile_file, monkeypatch):
    """A terminal whose input has run out must not be read as consent."""
    monkeypatch.setenv("MQS_PASSWORD", "")  # so the terminal is only used for the question
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError))
    code, _out = run(["apply", "--host", "10.0.0.1", "--profile-file", str(profile_file),
                      "--mode", "import"], router=router)
    assert code == cli.REFUSED
    assert "/import" not in router.log


def test_reset_without_the_identity_is_blocked(router, profile_file):
    code, out = run(["apply", "--host", "10.0.0.1", "--profile-file", str(profile_file),
                     "--mode", "reset", "--yes"], router=router)
    assert code == cli.BLOCKED
    assert "apply.reset_identity" in out
    assert "/system/reset-configuration" not in router.log


def test_reset_needs_the_right_identity(router, profile_file):
    code, _out = run(["apply", "--host", "10.0.0.1", "--profile-file", str(profile_file),
                     "--mode", "reset", "--confirm-identity", "not-this-one", "--yes"],
                    router=router)
    assert code == cli.BLOCKED
    assert "/system/reset-configuration" not in router.log


def test_reset_keeping_users_asks_for_no_admin_password(router, profile_file, monkeypatch):
    monkeypatch.delenv("MQS_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: pytest.fail(prompt))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    args = cli.build_parser().parse_args(["apply", "--profile", "x", "--mode", "reset",
                                          "--keep-users"])
    options = cli.build_options(args, ApplyMode.RESET_AND_IMPORT)
    assert options.keep_users and options.admin_password == ""
    # --keep-users means nothing outside a reset.
    args = cli.build_parser().parse_args(["apply", "--profile", "x", "--keep-users"])
    assert not cli.build_options(args, ApplyMode.IMPORT).keep_users


def refuse(error):
    def connect(*args, **kwargs):
        raise error
    return connect


KEY_ONLY = ["--user", "bee", "--key", "id_rsa"]


@pytest.mark.parametrize(("error", "extra", "hinted"), [
    # 2026-09-24: a key and no password; api-ssl refused, SSH not on its default port.
    (AuthError("api-ssl authentication failed for bee", TransportKind.API_SSL), KEY_ONLY, True),
    (AuthError("SSH authentication failed for bee", TransportKind.SSH), KEY_ONLY, False),
    (AuthError("api-ssl authentication failed for bee", TransportKind.API_SSL), [], False),
])
def test_a_refused_login_is_one_line_not_a_traceback(profile_file, capsys, monkeypatch,
                                                     error, extra, hinted):
    monkeypatch.setenv("MQS_PASSWORD", "")
    code, _out = run(["apply", "--host", "192.168.55.68", *extra,
                      "--profile-file", str(profile_file), "--mode", "generate"],
                     connect=refuse(error))
    assert code == cli.FAILED
    err = capsys.readouterr().err
    assert err.splitlines() == [err.rstrip("\n")], err
    assert err.startswith(f"AuthError: {error}")
    assert ("--transport ssh --ssh-port" in err) == hinted


def test_an_unreachable_device_is_one_line_not_a_traceback(capsys, monkeypatch):
    monkeypatch.setenv("MQS_PASSWORD", "pw")
    error = ConnectFailed([ConnectAttempt(TransportKind.API_SSL, 8729, "refused"),
                           ConnectAttempt(TransportKind.SSH, 22, "timed out")])
    code, _out = run(["connect", "--host", "192.168.55.68"], connect=refuse(error))
    assert code == cli.FAILED
    assert capsys.readouterr().err == f"ConnectFailed: {error}\n"


def test_host_is_required_for_a_real_mode(profile_file, capsys):
    code = cli.main(["apply", "--profile-file", str(profile_file), "--mode", "import"],
                    out=io.StringIO())
    assert code == cli.BLOCKED
    assert "--host is required" in capsys.readouterr().err


MAC = "D0:EA:11:5D:65:E2"
MAC_DEVICE = DiscoveredDevice(mac=MAC, identity="MikroTik", interface_name="ether1",
                              local_address=IPv4Address("192.168.55.55"))
LAN = LocalInterface("Ethernet", IPv4Address("192.168.55.55"), IPv4Network("192.168.55.0/24"))


def mac_deps(calls, *, found=MAC_DEVICE):
    transport = FakeTransport()
    transport.connect()

    def connect_mac(device, params, address, interface, choice, **kwargs):
        calls.append({"device": device, "address": address, "interface": interface,
                      "params": params, **kwargs})
        return DeviceSession(
            ConnectionParams(host=str(address), username="admin"), transport,
            make_inventory("7.20.7", PORTS), {}, "ether1",
            temp_address=f"{address}/24", mac_method="mac-telnet",
            mac_route=MacRoute(device, interface, address),
        )

    return transport, {"find_device": lambda mac, seconds: found,
                       "interfaces": lambda: [LAN], "connect_mac": connect_mac}


def test_connect_by_mac_lends_an_address_and_gives_it_back(monkeypatch):
    monkeypatch.setenv("MQS_PASSWORD", "pw")
    calls = []
    transport, deps = mac_deps(calls)
    code, out = run(["connect", "--mac", "d0-ea-11-5d-65-e2",
                     "--temp-address", "192.168.55.254", "--ssh-port", "122"], **deps)
    assert code == cli.OK, out
    assert calls[0]["device"] is MAC_DEVICE and calls[0]["interface"] is LAN
    assert str(calls[0]["address"]) == "192.168.55.254"
    assert calls[0]["params"].password == "pw"
    assert calls[0]["ssh_port"] == 122, "the ports asked for hold by MAC as well"
    assert f"by MAC      : {MAC} over mac-telnet" in out
    removed = [c for c in transport.commands if c.verb == "remove"]
    assert removed and removed[0].api_path == "/ip/address"


@pytest.mark.parametrize(("argv", "message"), [
    (["connect", "--mac", MAC], "--temp-address"),
    (["connect"], "--host or --mac"),
])
def test_connect_by_mac_needs_what_it_cannot_guess(argv, message, capsys):
    assert cli.main(argv, out=io.StringIO()) == cli.BLOCKED
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(("temp", "found", "message"), [
    ("192.168.99.254", MAC_DEVICE, "not a free address"),
    ("192.168.55.55", MAC_DEVICE, "not a free address"),  # this PC's own address
    ("not-an-ip", MAC_DEVICE, "--temp-address"),
    ("192.168.55.254", None, "did not announce itself"),
])
def test_connect_by_mac_refuses_without_touching_the_device(temp, found, message, capsys,
                                                           monkeypatch):
    monkeypatch.setenv("MQS_PASSWORD", "pw")
    calls = []
    _transport, deps = mac_deps(calls, found=found)
    code = cli.main(["connect", "--mac", MAC, "--temp-address", temp], out=io.StringIO(),
                    **deps)
    assert code == cli.FAILED
    assert message in capsys.readouterr().err
    assert calls == []


def test_passwords_are_never_command_line_arguments():
    """They would land in the shell history and in the process list."""
    for option in ("--password", "--admin-password"):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["apply", "--profile", "x", option, "secret"])
    args = cli.build_parser().parse_args(["apply", "--profile", "x"])
    assert args.password_env and args.admin_password_env


@pytest.mark.parametrize(("mode", "extra", "code", "given_back"), [
    ("import", [], cli.OK, True),
    ("reset", ["--confirm-identity", "MikroTik"], cli.OK, False),  # the reset took it
    ("reset", [], cli.BLOCKED, True),  # preflight said no: nothing changed, give it back
])
def test_apply_by_mac_gives_the_lent_address_back(router, profile_file, monkeypatch,
                                                  mode, extra, code, given_back):
    monkeypatch.setenv("MQS_PASSWORD", "pw")
    monkeypatch.setenv("MQS_ADMIN_PASSWORD", "N3w")

    def connect_mac(device, params, address, interface, choice, **kwargs):
        return DeviceSession(ConnectionParams(host=str(address), username="admin"),
                             router.session(), make_inventory("7.15.3", PORTS), {}, "ether9",
                             temp_address=f"{address}/24", mac_method="mac-telnet")

    def orchestrator_factory(ctx):
        ctx.uploader_factory = lambda transport: RouterUploader(router)
        ctx.reconnect = lambda target: router.session(username=target.params.username,
                                                      password=target.params.password)
        ctx.sleep = lambda seconds: None
        return ApplyOrchestrator(ctx)

    _transport, deps = mac_deps([])
    deps["connect_mac"] = connect_mac
    result, out = run(["apply", "--mac", MAC, "--temp-address", "192.168.55.254",
                       "--profile-file", str(profile_file), "--mode", mode, "--yes", *extra],
                      router=router, orchestrator_factory=orchestrator_factory, **deps)
    assert result == code, out
    assert ("/ip/address/remove" in router.log) == given_back
    assert ("temporary address 192.168.55.254/24 removed" in out) == given_back
    if given_back:
        assert router.log[-1] == "/ip/address/remove"  # after everything else


def test_apply_takes_over_the_port_names_of_the_device(router, profile_file):
    # The profile names ether1 the WAN port; on this device it is called ether1-uplink.
    inventory = make_inventory("7.15.3", ["ether1-uplink", *PORTS[1:]],
                               default_names={"ether1-uplink": "ether1"})
    session = DeviceSession(ConnectionParams(host="10.0.0.1", username="admin"),
                            router.session(), inventory, {}, "ether9")
    code, out = run(["apply", "--host", "10.0.0.1", "--profile-file", str(profile_file),
                     "--mode", "generate", "--show-script"], connect=lambda *a, **k: session)
    assert code == cli.OK, out
    assert "ports       : ether1-uplink (ether1), ether2," in out
    assert "port names  : taken over from the device: ether1=ether1-uplink" in out
    assert "set [ find default-name=ether1 ] name=ether1-uplink" in out
    assert "add interface=ether1-uplink list=WAN" in out
    assert "wan.interface_unknown" not in out


def test_update_needs_a_reset(profile_file, capsys):
    code = cli.main(["apply", "--host", "10.0.0.1", "--profile-file", str(profile_file),
                     "--mode", "import", "--update"])
    assert code == cli.BLOCKED
    assert "--update needs --mode reset" in capsys.readouterr().err


def reset_offline(router):
    """The orchestrator of a reset that neither sleeps nor reconnects over the network."""

    def factory(ctx):
        ctx.uploader_factory = lambda transport: RouterUploader(router)
        ctx.sleep = lambda seconds: None
        ctx.reconnect = lambda target: router.session(
            port=target.api_port, username=target.params.username,
            password=target.params.password)
        return ApplyOrchestrator(ctx)

    return factory


def test_reset_with_update_installs_the_package_for_the_device(router, profile_file, tmp_path,
                                                               monkeypatch):
    monkeypatch.setenv("MQS_ADMIN_PASSWORD", "N3w")
    folder = tmp_path / "Update"
    folder.mkdir()
    (folder / "routeros-7.23.7-arm.npk").write_bytes(make_npk())
    (folder / "routeros-7.23.7-mmips.npk").write_bytes(make_npk(arch="mmips"))
    router.installable = {"routeros-7.23.7-arm.npk": "7.23.7"}
    inventory = make_inventory("7.15.3", PORTS, architecture="arm",
                               packages=[PackageInfo(name="routeros", version="7.15.3")])
    session = DeviceSession(ConnectionParams(host="10.0.0.1", username="admin"),
                            router.session(), inventory, {}, "ether9")
    code, out = run(["apply", "--host", "10.0.0.1", "--profile-file", str(profile_file),
                     "--mode", "reset", "--confirm-identity", "MikroTik", "--yes",
                     "--update", "--update-dir", str(folder)],
                    router=router, connect=lambda *a, **k: session,
                    orchestrator_factory=reset_offline(router))
    assert code == cli.OK, out
    assert "update.planned" in out and "routeros-7.23.7-arm.npk" in out
    assert "mmips" not in out.split("preflight:")[1]
    assert router.version.startswith("7.23.7")
    assert "OK   RouterOS 7.23.7" in out


def test_without_update_nothing_is_installed(router, profile_file, monkeypatch):
    monkeypatch.setenv("MQS_ADMIN_PASSWORD", "N3w")
    router.files["routeros-7.23.7-arm.npk"] = b"left there by hand"
    router.installable = {"routeros-7.23.7-arm.npk": "7.23.7"}
    router.installs_packages = False  # a stray package is not the CLI's business
    code, out = run(["apply", "--host", "10.0.0.1", "--profile-file", str(profile_file),
                     "--mode", "reset", "--confirm-identity", "MikroTik", "--yes"],
                    router=router, orchestrator_factory=reset_offline(router))
    assert code == cli.OK, out
    assert "update." not in out and "RouterOS 7.23.7" not in out


def test_routerboot_has_its_own_switch():
    parse = cli.build_parser().parse_args
    reset = ApplyMode.RESET_AND_IMPORT
    both = cli.build_options(parse(["apply", "--profile", "x", "--mode", "reset", "--update",
                                    "--routerboot", "--keep-users"]), reset)
    assert both.upgrades_routerboot
    update = cli.build_options(parse(["apply", "--profile", "x", "--mode", "reset",
                                      "--update", "--keep-users"]), reset)
    assert not update.upgrade_routerboot
    alone = cli.build_options(parse(["apply", "--profile", "x", "--mode", "reset",
                                     "--routerboot", "--keep-users"]), reset)
    assert alone.upgrades_routerboot


def test_routerboot_needs_a_reset(profile_file, capsys):
    code = cli.main(["apply", "--host", "10.0.0.1", "--profile-file", str(profile_file),
                     "--mode", "import", "--routerboot"])
    assert code == cli.BLOCKED
    assert "--routerboot needs --mode reset" in capsys.readouterr().err
