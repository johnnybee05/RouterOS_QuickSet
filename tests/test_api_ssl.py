"""API-SSL transport against a fake librouteros connection (no device needed)."""

from __future__ import annotations

import ssl

import pytest
from librouteros import exceptions as ros_exc
from librouteros import login as ros_login

from mqs.generator.ir import Find, cmd
from mqs.generator.render import render_inline
from mqs.transport.api_ssl import ApiSslTransport
from mqs.transport.base import (
    AuthError,
    CommandError,
    ConnectionLost,
    ConnectionParams,
    ConnectTimeout,
    PeerSecurity,
    ServiceUnavailable,
    TransportKind,
)


class FakeApi:
    def __init__(self, data=None, traps=None, lost=None) -> None:
        self.data = data or {}
        self.traps = traps or {}
        self.lost = lost or set()
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def __call__(self, word: str, **kwargs):
        self.calls.append((word, kwargs))
        if word in self.traps:
            raise ros_exc.TrapError(self.traps[word])
        if word in self.lost:
            raise ros_exc.ConnectionClosed("socket closed")
        return iter(self.data.get(word, []))

    def close(self) -> None:
        self.closed = True


class FakeConnect:
    """Plays a scripted sequence of outcomes, one per connect attempt."""

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.attempts: list[dict] = []

    def __call__(self, **kwargs):
        self.attempts.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


PARAMS = ConnectionParams(host="10.0.0.1", username="admin", password="pw")


def test_anonymous_dh_first_by_default():
    api = FakeApi()
    fake = FakeConnect([api])
    transport = ApiSslTransport(PARAMS, connect_func=fake)
    transport.connect()
    assert transport.connected
    assert transport.peer_security == PeerSecurity.ANONYMOUS_DH
    attempt = fake.attempts[0]
    assert attempt["port"] == 8729 and attempt["timeout"] == 10
    assert attempt["login_method"] is ros_login.plain
    assert callable(attempt["ssl_wrapper"])
    transport.close()
    assert api.closed and not transport.connected


def test_falls_back_to_verified_when_handshake_fails():
    fake = FakeConnect([ssl.SSLError("no shared cipher"), FakeApi()])
    transport = ApiSslTransport(PARAMS, connect_func=fake)
    transport.connect()
    assert transport.peer_security == PeerSecurity.VERIFIED
    assert len(fake.attempts) == 2


def test_ca_file_means_verified_only(tmp_path):
    params = ConnectionParams(host="h", username="u", ca_file=None, allow_anonymous_dh=False)
    fake = FakeConnect([ssl.SSLError("verify failed")])
    with pytest.raises(ServiceUnavailable) as err:
        ApiSslTransport(params, connect_func=fake).connect()
    assert "verified" in str(err.value)
    assert len(fake.attempts) == 1


def test_legacy_token_login_fallback():
    fake = FakeConnect([ros_exc.TrapError("unknown command"), FakeApi()])
    transport = ApiSslTransport(PARAMS, connect_func=fake)
    transport.connect()
    assert fake.attempts[1]["login_method"] is ros_login.token


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (ros_exc.TrapError("invalid user name or password (6)"), AuthError),
        (TimeoutError("slow"), ConnectTimeout),
    ],
)
def test_connect_fatal_errors(outcome, expected):
    with pytest.raises(expected) as err:
        ApiSslTransport(PARAMS, connect_func=FakeConnect([outcome])).connect()
    if expected is AuthError:  # the CLI's hint tells an api-ssl refusal from an SSH one
        assert err.value.kind == TransportKind.API_SSL


def test_unavailable_after_all_attempts():
    fake = FakeConnect([ConnectionRefusedError("refused"), ConnectionRefusedError("refused")])
    with pytest.raises(ServiceUnavailable) as err:
        ApiSslTransport(PARAMS, connect_func=fake).connect()
    assert "anonymous-dh" in str(err.value) and "verified" in str(err.value)


def connected(api: FakeApi) -> ApiSslTransport:
    transport = ApiSslTransport(PARAMS, connect_func=FakeConnect([api]))
    transport.connect()
    return transport


def test_execute_converts_values_and_names():
    api = FakeApi(data={"/system/resource/print": [{"version": "7.15.3", "free-memory": 1024,
                                                    "bad-blocks": False}]})
    transport = connected(api)
    assert transport.execute("/system/resource/print") == [
        {"version": "7.15.3", "free-memory": "1024", "bad-blocks": "no"}]
    transport.execute("/ip dhcp-server add", address_pool="p", disabled=False)
    assert api.calls[-1] == ("/ip/dhcp-server/add", {"address-pool": "p", "disabled": "no"})
    transport.execute("/console/inspect", request="child", path="ip,firewall,filter,add")
    assert api.calls[-1] == ("/console/inspect",
                             {"request": "child", "path": "ip,firewall,filter,add"})
    transport.execute("/interface/bridge/port/print", detail=True)
    assert api.calls[-1] == ("/interface/bridge/port/print", {})
    transport.execute("/import", file_name="flash/q.rsc")
    assert api.calls[-1] == ("/import", {"file-name": "flash/q.rsc"})


def test_execute_command_resolves_selectors():
    api = FakeApi(data={
        "/ip/service/print": [{".id": "*1", "name": "ftp"}, {".id": "*4", "name": "ssh"}],
        "/system/logging/print": [{".id": "*0"}, {".id": "*1"}],
        "/interface/bridge/port/print": [{".id": "*A", "interface": "ether2"},
                                         {".id": "*B", "interface": "ether3"}],
    })
    transport = connected(api)
    transport.execute_command(cmd("/ip service", "set", selector="ssh", port=122))
    assert api.calls[-1] == ("/ip/service/set", {"port": "122", "numbers": "*4"})
    transport.execute_command(cmd("/system logging", "set", selector=1, action="disk"))
    assert api.calls[-1] == ("/system/logging/set", {"action": "disk", "numbers": "*1"})
    transport.execute_command(cmd("/interface bridge port", "set",
                                  selector=Find.by(interface="ether3"), hw=True))
    assert api.calls[-1] == ("/interface/bridge/port/set", {"hw": "yes", "numbers": "*B"})

    calls = len(api.calls)
    transport.execute_command(cmd("/interface bridge port", "set",
                                  selector=Find.by(interface="ether9"), hw=True))
    assert len(api.calls) == calls + 1  # only the print, no set
    transport.execute_command(cmd("/system identity", "set", name="R1"))
    assert api.calls[-1] == ("/system/identity/set", {"name": "R1"})

    with pytest.raises(CommandError):
        transport.execute_command(cmd("/ip service", "set", selector="gopher", port=1))
    with pytest.raises(CommandError):
        transport.execute_command(cmd("/system logging", "set", selector=5, action="disk"))
    with pytest.raises(CommandError):
        transport.execute_command(cmd("/ip service", "set", selector=Find(raw="where port=22")))


def test_find_where_pattern_over_the_api():
    api = FakeApi(data={"/ip/firewall/filter/print": [
        {".id": "*1", "comment": "quickset: drop invalid"},
        {".id": "*2", "comment": "defconf: drop invalid"},
        {".id": "*3"},
        {".id": "*4", "comment": "quickset: accept ICMP"},
    ]})
    transport = connected(api)
    command = cmd("/ip firewall filter", "remove",
                  selector=Find.matching("comment", "^quickset: "))
    assert render_inline(command) == \
        '/ip firewall filter remove [ find where comment~"^quickset: " ]'
    transport.execute_command(command)
    assert api.calls[-1] == ("/ip/firewall/filter/remove", {"numbers": "*1,*4"})


def test_a_name_means_the_configured_item_not_its_connections():
    """Captured on a hEX S, 7.20.7: our own connection is listed as a dynamic api-ssl row,
    and `/ip/service/set =numbers=*9,*14` was refused with "this is configured elsewhere"
    while `=numbers=*9` went through. The console's `set api-ssl` means *9 alone."""
    api = FakeApi(data={
        "/ip/service/print": [
            {".id": "*9", "name": "api-ssl", "port": 8729, "dynamic": False},
            {".id": "*14", "name": "api-ssl", "port": 8729, "dynamic": True,
             "connection": True, "remote": "192.168.55.55:62253"},
        ],
        "/interface/print": [{".id": "*20", "name": "pppoe-out1", "dynamic": True}],
    })
    transport = connected(api)
    transport.execute_command(cmd("/ip service", "set", selector="api-ssl", port=8729))
    assert api.calls[-1] == ("/ip/service/set", {"port": "8729", "numbers": "*9"})
    # A find takes dynamic rows along, in the console too.
    transport.execute_command(cmd("/ip service", "set", selector=Find.by(name="api-ssl"),
                                  port=8729))
    assert api.calls[-1] == ("/ip/service/set", {"port": "8729", "numbers": "*9,*14"})
    # With nothing configured under the name, the device gets to say what it thinks.
    transport.execute_command(cmd("/interface", "set", selector="pppoe-out1", mtu=1480))
    assert api.calls[-1] == ("/interface/set", {"mtu": "1480", "numbers": "*20"})


def test_traps_and_connection_loss():
    api = FakeApi(traps={"/ip/dns/set": "invalid value for argument servers"},
                  lost={"/system/reboot"})
    transport = connected(api)
    with pytest.raises(CommandError) as err:
        transport.execute("/ip/dns/set", servers="x")
    assert "invalid value" in err.value.message
    with pytest.raises(ConnectionLost):
        transport.execute("/system/reboot")
    assert not transport.connected
    with pytest.raises(ConnectionLost):
        transport.execute("/system/identity/print")


def test_file_reference_resolves_the_file_id():
    """File arguments are matched by `.id` over the API, not by name.

    Verified on RouterOS 7.20.7: `/system/reset-configuration run-after-reset=<name>` is
    refused with "input does not match any value of run-after-reset" even though the file
    is listed, while the same call carrying the `.id` runs.
    """
    transport = ApiSslTransport(PARAMS, connect_func=lambda *a, **k: None)
    transport._call = lambda word, args: [
        {".id": "*1", "name": "other.rsc"},
        {".id": "*2", "name": "quickset-20260916-174633.rsc"},
    ]
    assert transport.file_reference("quickset-20260916-174633.rsc") == "*2"
    assert transport.file_reference("not-uploaded.rsc") == "not-uploaded.rsc"
