"""SSH transport for the RouterOS CLI (secondary transport, file transfer carrier).

RouterOS CLI over SSH is not a normal shell, so this transport:
- logs in as `<user>+cet` (no colours, no terminal detection; NUTNO OVĚŘIT per version),
- uses non-interactive `exec_command`, never a PTY shell,
- sends exactly one single-line command per exec,
- strips ANSI sequences and detects RouterOS error messages in the output.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import paramiko

from mqs.generator.ir import Command, RawLine, cmd
from mqs.generator.render import quote, render_inline
from mqs.transport.base import (
    ROOT_MENU,
    SSH_PORT,
    AuthError,
    CommandError,
    CommandTimeout,
    ConnectionLost,
    ConnectionParams,
    ConnectTimeout,
    PeerSecurity,
    Reply,
    ServiceUnavailable,
    TransportKind,
    args_from_kwargs,
    split_path,
)
from mqs.transport.cli_output import find_error, parse_key_value, parse_terse, sanitize

LOGIN_SUFFIX = "+cet"

# Menus whose `print` shows a single `key: value` block instead of an item list.
SINGLETON_MENUS = frozenset({
    "/system resource",
    "/system identity",
    "/system routerboard",
    "/system clock",
    "/system ntp client",
    "/ip dns",
    "/ip neighbor discovery-settings",
    "/ipv6 settings",
    "/interface bridge settings",
    "/snmp",
})


class SshTransport:
    kind = TransportKind.SSH

    def __init__(
        self,
        params: ConnectionParams,
        *,
        client_factory: Callable[[], Any] = paramiko.SSHClient,
    ) -> None:
        self.params = params
        self._client_factory = client_factory
        self._client: Any = None
        self.host_key_fingerprint: str | None = None

    # ---------------------------------------------------------------- lifecycle

    @property
    def port(self) -> int:
        return self.params.port or SSH_PORT

    @property
    def connected(self) -> bool:
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return bool(transport and transport.is_active())

    @property
    def peer_security(self) -> PeerSecurity:
        return PeerSecurity.SSH_HOST_KEY if self.host_key_fingerprint else PeerSecurity.UNKNOWN

    def connect(self) -> None:
        client = self._client_factory()
        # Trust on first use: freshly unboxed devices have unknown host keys. The fingerprint
        # is exposed so the UI can show it.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        timeout = self.params.connect_timeout
        try:
            client.connect(
                hostname=self.params.host,
                port=self.port,
                username=self.params.username + LOGIN_SUFFIX,
                password=self.params.password or None,
                key_filename=self.params.key_filename,
                timeout=timeout,
                auth_timeout=timeout,
                banner_timeout=timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        except paramiko.AuthenticationException as exc:
            client.close()
            raise AuthError(f"SSH authentication failed for {self.params.username}") from exc
        except TimeoutError as exc:
            client.close()
            raise ConnectTimeout(
                f"SSH connect to {self.params.host}:{self.port} timed out"
            ) from exc
        except (paramiko.SSHException, OSError) as exc:
            client.close()
            raise ServiceUnavailable(f"SSH {self.params.host}:{self.port}: {exc}") from exc

        self._client = client
        transport = client.get_transport()
        key = transport.get_remote_server_key() if transport else None
        if key is not None:
            self.host_key_fingerprint = key.get_fingerprint().hex(":")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ---------------------------------------------------------------- commands

    def run(self, line: str) -> str:
        """Execute one CLI line and return sanitised output; raises on RouterOS errors."""
        if "\n" in line or "\r" in line:
            raise ValueError("SSH commands must be a single line")
        if self._client is None:
            raise ConnectionLost("SSH transport is not connected")
        try:
            _, stdout, stderr = self._client.exec_command(
                line, timeout=self.params.command_timeout
            )
            output = stdout.read() + stderr.read()
        except TimeoutError as exc:
            raise CommandTimeout(f"SSH command timed out: {line}") from exc
        except (paramiko.SSHException, EOFError, OSError) as exc:
            raise ConnectionLost(f"SSH connection lost during: {line}") from exc
        text = sanitize(output.decode("utf-8", errors="replace"))
        error = find_error(text)
        if error is not None:
            raise CommandError(line, error)
        return text

    def execute(self, path: str, /, **args: object) -> Reply:
        menu, verb = split_path(path)
        values = args_from_kwargs(args)
        if verb == "print":
            if menu in SINGLETON_MENUS:
                return [parse_key_value(self.run(f"{menu} print"))]
            detail = " detail" if values.pop("detail", "no") == "yes" else ""
            return parse_terse(self.run(f"{menu} print{detail} terse"))
        selector = values.pop("numbers", None)
        if menu == ROOT_MENU:  # /export, /import
            line = " ".join([f"/{verb}", *(f"{k}={quote(v)}" for k, v in values.items())])
        else:
            line = render_inline(cmd(menu, verb, selector=selector, args=values))
        self.run(line)
        return []

    def execute_command(self, command: Command) -> Reply:
        self.run(render_inline(command))
        return []

    def execute_raw(self, item: Command | RawLine) -> str:
        """Line-by-line fallback: IR item rendered as a self-contained CLI line."""
        return self.run(render_inline(item))

    def open_sftp(self) -> Any:
        if self._client is None:
            raise ConnectionLost("SSH transport is not connected")
        return self._client.open_sftp()

    def paramiko_transport(self) -> Any:
        if self._client is None:
            raise ConnectionLost("SSH transport is not connected")
        return self._client.get_transport()
