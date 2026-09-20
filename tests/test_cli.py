"""The device CLI, driven against the in-memory router (no hardware)."""

from __future__ import annotations

import io

import pytest

from factories import make_inventory
from fake_router import FakeRouter, RouterUploader
from mqs import cli
from mqs.apply.orchestrator import ApplyOrchestrator
from mqs.apply.session import DeviceSession
from mqs.generator.base import generate
from mqs.generator.context import TargetContext
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.profiles.store import profile_to_json
from mqs.transport.base import ConnectionParams

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


def test_passwords_are_never_command_line_arguments():
    """They would land in the shell history and in the process list."""
    for option in ("--password", "--admin-password"):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["apply", "--profile", "x", option, "secret"])
    args = cli.build_parser().parse_args(["apply", "--profile", "x"])
    assert args.password_env and args.admin_password_env
