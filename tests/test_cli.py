"""The device CLI, driven against the in-memory router (no hardware)."""

from __future__ import annotations

import io
from ipaddress import IPv4Address, IPv4Network

import pytest

from factories import make_inventory
from fake_router import FakeRouter, RouterUploader
from fake_transport import FakeTransport
from mqs import cli
from mqs.apply.orchestrator import ApplyOrchestrator
from mqs.apply.session import DeviceSession
from mqs.generator.base import generate
from mqs.generator.context import TargetContext
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.profiles.store import profile_to_json
from mqs.transport.base import ConnectionParams
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
        "bridges": {"bridge": "rstp"},
        "addresses": [("192.168.88.1/24", "bridge")],
        "ports": profile.bridge.ports,
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
