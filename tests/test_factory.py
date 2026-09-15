import pytest

from mqs.transport.base import (
    AuthError,
    Cancelled,
    ConnectionParams,
    ConnectTimeout,
    ServiceUnavailable,
    TransportKind,
)
from mqs.transport.factory import ConnectFailed, TransportChoice, connect_transport


class StubTransport:
    def __init__(self, kind, default_port, error, params, log):
        self.kind = kind
        self.params = params
        self.error = error
        self.default_port = default_port
        log.append((kind, params.port))

    @property
    def port(self):
        return self.params.port or self.default_port

    def connect(self):
        if self.error is not None:
            raise self.error


def factories(api_error=None, ssh_error=None):
    log: list = []

    def api(params):
        return StubTransport(TransportKind.API_SSL, 8729, api_error, params, log)

    def ssh(params):
        return StubTransport(TransportKind.SSH, 22, ssh_error, params, log)

    return api, ssh, log


PARAMS = ConnectionParams(host="192.168.88.1", username="admin", password="x")


def test_auto_prefers_api_ssl():
    api, ssh, log = factories()
    outcome = connect_transport(PARAMS, api_factory=api, ssh_factory=ssh)
    assert outcome.transport.kind == TransportKind.API_SSL
    assert not outcome.fell_back
    assert log == [(TransportKind.API_SSL, None)]


@pytest.mark.parametrize("error", [ServiceUnavailable("refused"), ConnectTimeout("slow")])
def test_auto_falls_back_to_ssh(error):
    api, ssh, log = factories(api_error=error)
    outcome = connect_transport(PARAMS, api_factory=api, ssh_factory=ssh, ssh_port=122)
    assert outcome.transport.kind == TransportKind.SSH
    assert outcome.fell_back
    assert outcome.attempts[0].error and outcome.attempts[1].error is None
    assert log == [(TransportKind.API_SSL, None), (TransportKind.SSH, 122)]


def test_auth_error_is_not_retried():
    api, ssh, log = factories(api_error=AuthError("bad password"))
    with pytest.raises(AuthError):
        connect_transport(PARAMS, api_factory=api, ssh_factory=ssh)
    assert len(log) == 1


def test_explicit_choices_and_total_failure():
    api, ssh, log = factories(api_error=ServiceUnavailable("closed"))
    with pytest.raises(ConnectFailed) as err:
        connect_transport(PARAMS, TransportChoice.API_SSL, api_factory=api, ssh_factory=ssh)
    assert [a.port for a in err.value.attempts] == [8729]
    assert "api-ssl:8729" in str(err.value)

    api, ssh, log = factories(api_error=ServiceUnavailable("closed"))
    outcome = connect_transport(PARAMS, TransportChoice.SSH, api_factory=api, ssh_factory=ssh)
    assert outcome.transport.kind == TransportKind.SSH and not outcome.fell_back
    assert log == [(TransportKind.SSH, None)]

    api, ssh, _ = factories(api_error=ServiceUnavailable("a"), ssh_error=ServiceUnavailable("b"))
    with pytest.raises(ConnectFailed) as err:
        connect_transport(PARAMS, api_factory=api, ssh_factory=ssh)
    assert len(err.value.attempts) == 2


def test_cancelled():
    api, ssh, _ = factories()
    with pytest.raises(Cancelled):
        connect_transport(PARAMS, api_factory=api, ssh_factory=ssh, cancelled=lambda: True)
