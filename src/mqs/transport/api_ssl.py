"""RouterOS API over TLS (api-ssl, port 8729) — the primary transport."""

from __future__ import annotations

import contextlib
import re
import socket
import ssl
from collections.abc import Callable, Iterator
from typing import Any

import librouteros
from librouteros import exceptions as ros_exc
from librouteros import login as ros_login

from mqs.generator.ir import Command, Find
from mqs.transport.base import (
    API_SSL_PORT,
    AuthError,
    CommandError,
    ConnectionLost,
    ConnectionParams,
    ConnectTimeout,
    PeerSecurity,
    Reply,
    ServiceUnavailable,
    TransportKind,
    api_word,
    args_from_kwargs,
    split_path,
)
from mqs.transport.tls import anonymous_dh_context, peer_security_of, verified_context

_AUTH_MARKERS = ("cannot log in", "invalid user name or password", "not logged in")


# A refused command, from one !trap or from several. librouteros raises MultiTrapError,
# which is no TrapError, for more than one, and 7.23.5 answers a menu that is not there
# with two: "no such command or directory (wireless), no such command prefix" (hEX S
# without a radio, 2026-10-12). It escaped as a crash where "menu missing" was expected.
_TRAPS = (ros_exc.TrapError, ros_exc.MultiTrapError)


def _to_text(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


class ApiSslTransport:
    kind = TransportKind.API_SSL

    def __init__(
        self,
        params: ConnectionParams,
        *,
        connect_func: Callable[..., Any] = librouteros.connect,
    ) -> None:
        self.params = params
        self._connect_func = connect_func
        self._api: Any = None
        self._tls_socket: ssl.SSLSocket | None = None
        self._security = PeerSecurity.UNKNOWN

    @property
    def port(self) -> int:
        return self.params.port or API_SSL_PORT

    @property
    def connected(self) -> bool:
        return self._api is not None

    @property
    def peer_security(self) -> PeerSecurity:
        return self._security

    def _attempts(self) -> list[tuple[PeerSecurity, ssl.SSLContext]]:
        verified = (PeerSecurity.VERIFIED, verified_context(self.params.ca_file))
        if self.params.ca_file or not self.params.allow_anonymous_dh:
            return [verified]
        # Default api-ssl has certificate=none -> anonymous DH is the common case.
        return [(PeerSecurity.ANONYMOUS_DH, anonymous_dh_context()), verified]

    def connect(self) -> None:
        errors: list[str] = []
        for security, ctx in self._attempts():
            for login_method in (ros_login.plain, ros_login.token):
                try:
                    self._open(security, ctx, login_method)
                    return
                except AuthError:
                    raise
                except ConnectTimeout:
                    raise
                except _LoginMethodRejected as exc:
                    errors.append(str(exc))
                    continue
                except ServiceUnavailable as exc:
                    errors.append(str(exc))
                    break
        raise ServiceUnavailable(
            f"api-ssl {self.params.host}:{self.port} unavailable: " + "; ".join(errors)
        )

    def _open(self, security: PeerSecurity, ctx: ssl.SSLContext, login_method: Any) -> None:
        captured: list[ssl.SSLSocket] = []

        def wrapper(sock: socket.socket) -> ssl.SSLSocket:
            tls = ctx.wrap_socket(sock, server_hostname=self.params.host)
            captured.append(tls)
            return tls

        try:
            api = self._connect_func(
                host=self.params.host,
                username=self.params.username,
                password=self.params.password,
                port=self.port,
                timeout=self.params.connect_timeout,
                ssl_wrapper=wrapper,
                login_method=login_method,
            )
        except _TRAPS as exc:
            message = str(exc).lower()
            if any(marker in message for marker in _AUTH_MARKERS):
                raise AuthError(
                    f"api-ssl authentication failed for {self.params.username}", self.kind
                ) from exc
            raise _LoginMethodRejected(f"{login_method.__name__} login: {exc}") from exc
        except TimeoutError as exc:
            raise ConnectTimeout(
                f"api-ssl connect to {self.params.host}:{self.port} timed out"
            ) from exc
        except ssl.SSLError as exc:
            raise ServiceUnavailable(f"{security.value} TLS handshake failed: {exc}") from exc
        except (ros_exc.LibRouterosError, OSError) as exc:
            raise ServiceUnavailable(f"{security.value}: {exc}") from exc

        self._api = api
        self._tls_socket = captured[-1] if captured else None
        if self._tls_socket is not None:
            self._security = peer_security_of(self._tls_socket)
            self._tls_socket.settimeout(self.params.command_timeout)
        else:
            self._security = security

    @contextlib.contextmanager
    def reply_timeout(self, seconds: float) -> Iterator[None]:
        self._set_timeout(seconds)
        try:
            yield
        finally:
            self._set_timeout(self.params.command_timeout)

    def _set_timeout(self, seconds: float) -> None:
        if self._tls_socket is not None:
            with contextlib.suppress(OSError):  # closed meanwhile: nothing to wait on
                self._tls_socket.settimeout(seconds)

    def close(self) -> None:
        if self._api is not None:
            try:
                self._api.close()
            finally:
                self._api = None
                self._tls_socket = None

    # ---------------------------------------------------------------- commands

    def _call(self, word: str, args: dict[str, str]) -> Reply:
        if self._api is None:
            raise ConnectionLost("api-ssl transport is not connected")
        try:
            replies = list(self._api(word, **args))
        except _TRAPS as exc:
            raise CommandError(word, str(exc)) from exc
        except TimeoutError as exc:
            raise ConnectionLost(f"api-ssl timed out during {word}") from exc
        except (ros_exc.ConnectionClosed, ros_exc.FatalError, OSError) as exc:
            self._api = None
            raise ConnectionLost(f"api-ssl connection lost during {word}: {exc}") from exc
        return [{k: _to_text(v) for k, v in reply.items()} for reply in replies]

    @staticmethod
    def _word(menu: str, verb: str) -> str:
        return api_word(menu, verb)

    def execute(self, path: str, /, **args: object) -> Reply:
        menu, verb = split_path(path)
        values = args_from_kwargs(args)
        if verb == "print":
            values.pop("detail", None)  # API print always returns all properties
        return self._call(self._word(menu, verb), values)

    def resolve_ids(self, command: Command) -> list[str]:
        """Translate a CLI selector (find / index / item name) to API .id values."""
        selector = command.selector
        items = self._call(self._word(command.path, "print"), {})
        if isinstance(selector, int):
            if selector >= len(items):
                raise CommandError(command.api_path, f"no such item {selector}")
            return [items[selector][".id"]]
        if isinstance(selector, str):
            # The console's `set ssh` means the configured item. 7.20.7 also lists every
            # open connection as a dynamic /ip service row under its service's name, and
            # a set that includes one is refused: "this is configured elsewhere". A find
            # takes the dynamic rows along in the console too, so it is left as it is.
            named = [i for i in items if i.get("name") == selector]
            static = [i for i in named if i.get("dynamic") != "yes"]
            ids = [i[".id"] for i in static or named]
        elif isinstance(selector, Find):
            if selector.raw is not None:
                raise CommandError(command.api_path, "complex find expressions need SSH")
            # A pattern is a regular expression like the console's `~`; the simple ones
            # the generator writes mean the same to Python's re.
            ids = [i[".id"] for i in items
                   if all(i.get(k) == v for k, v in selector.conditions)
                   and all(re.search(p, i.get(k, "")) for k, p in selector.patterns)]
        else:
            return []
        if not ids and not isinstance(selector, Find):
            raise CommandError(command.api_path, f"no such item {selector}")
        return ids

    def file_reference(self, name: str) -> str:
        """Value that refers to an uploaded file in a command argument.

        RouterOS 7.20.7 accepts both the file name and its `.id` here; what it refuses is
        the name without its `.rsc` extension. The `.id` is sent because that is the only
        form seen to carry a reset through to the end over the API. A name that is not
        listed is passed on unchanged, so the device names it in the error.
        """
        for item in self._call(self._word("/file", "print"), {}):
            if item.get("name") == name:
                return str(item[".id"])
        return name

    def execute_command(self, command: Command) -> Reply:
        args = dict(command.args)
        if command.selector is not None:
            ids = self.resolve_ids(command)
            if not ids:
                return []  # `[ find ... ]` matching nothing is a no-op in the CLI too
            args["numbers"] = ",".join(ids)
        return self._call(self._word(command.path, command.verb), args)


class _LoginMethodRejected(ServiceUnavailable):
    """Server rejected the login flavour (plain vs. legacy token); try the other one."""
