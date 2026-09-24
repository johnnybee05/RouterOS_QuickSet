"""In-memory RouterOS simulation for apply-flow tests.

It models just enough state to exercise the orchestrator: files, backups (full state
snapshots), scheduler jobs with a tiny on-event interpreter, reboot/reset, service ports and
a transport session that drops when it should.
"""

from __future__ import annotations

import contextlib
import copy
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from mqs.generator.ir import Command, Find
from mqs.transport.base import (
    CommandError,
    ConnectionLost,
    ConnectionParams,
    PeerSecurity,
    Reply,
    TransportKind,
    api_word,
    split_path,
)
from mqs.transport.upload import FileUploader, UploadFailed, UploadResult, UploadStrategy


@dataclass
class RouterState:
    identity: str = "MikroTik"
    ssh_port: int = 22
    services_disabled: set[str] = field(default_factory=set)
    bridges: dict[str, str] = field(default_factory=dict)  # name -> protocol-mode
    addresses: list[tuple[str, str]] = field(default_factory=list)
    bridge_ports: list[dict[str, str]] = field(default_factory=list)
    scheduler: dict[str, dict[str, str]] = field(default_factory=dict)
    admin_password: str = ""
    dhcp_clients: list[str] = field(default_factory=list)  # interfaces
    default_gateways: list[str] = field(default_factory=list)  # static default routes


class FakeRouter:
    def __init__(self, clock: datetime = datetime(2026, 9, 15, 10, 0, 0)) -> None:
        self.state = RouterState()
        self.files: dict[str, str | bytes] = {}
        self.backups: dict[str, RouterState] = {}
        self.clock = clock
        self.reboots = 0
        self.log: list[str] = []
        self.fail: dict[str, str] = {}  # word -> error message
        self.drop_on: set[str] = set()  # words that drop the session
        self.pending_import_effects: dict[str, object] = {}
        self.run_after_reset: str | None = None
        self.reset_args: dict[str, str] = {}
        self.reset_refusals = 0  # how many times to answer "cannot use file" first
        self.offload_running = True
        self.version = "7.15.3 (stable)"
        # Package file name -> the version the reboot installs from it. What a real
        # device does with a package on the reset's reboot is not known; see
        # `installs_packages` for a device that ignores it.
        self.installable: dict[str, str] = {}
        self.installs_packages = True
        self.refused_parameter: str | None = None  # "bad parameter" to a script line
        self.device_log: list[dict[str, str]] = []

    # ------------------------------------------------------------ simulation helpers

    def advance(self, minutes: int) -> None:
        """Let device time pass and fire due scheduler jobs (one-shot, interval=0)."""
        self.clock += timedelta(minutes=minutes)
        for name, job in list(self.state.scheduler.items()):
            due = datetime.fromisoformat(f"{job['start-date']}T{job['start-time']}")
            if due <= self.clock and name in self.state.scheduler:
                self._run_script(job["on-event"])

    def _run_script(self, script: str) -> None:
        for statement in (s.strip() for s in script.split(";")):
            if match := re.match(r'/system scheduler remove \[find name="?([\w-]+)"?\]', statement):
                self.state.scheduler.pop(match[1], None)
                self.log.append(f"scheduler remove {match[1]}")
            elif match := re.match(r'/system backup load name="?([\w/.-]+)"?', statement):
                backup = self.backups.get(match[1])
                if backup is None:
                    self.log.append(f"backup load failed {match[1]}")
                    return
                self.state = copy.deepcopy(backup)
                self.reboots += 1
                self.log.append(f"backup load {match[1]} + reboot")

    def session(self, port: int = 8729, kind: TransportKind = TransportKind.API_SSL,
                username: str = "admin", password: str = "") -> RouterSession:
        return RouterSession(self, kind, port, username, password)

    # ------------------------------------------------------------ command handling

    def handle(self, session: RouterSession, word: str, args: dict[str, str]) -> Reply:
        self.log.append(word)
        if word in self.fail:
            raise CommandError(word, self.fail[word])
        s = self.state
        if word == "/system/backup/save":
            self.backups[args["name"]] = copy.deepcopy(s)
            self.files[args["name"] + ".backup"] = "<binary>"
            return []
        if word == "/export":
            self.files[args["file"] + ".rsc"] = "# export"
            return []
        if word == "/system/clock/print":
            return [{"date": self.clock.strftime("%Y-%m-%d"),
                     "time": self.clock.strftime("%H:%M:%S")}]
        if word == "/system/scheduler/add":
            s.scheduler[args["name"]] = dict(args)
            return []
        if word == "/system/scheduler/remove":
            for name in args.get("names", []):
                s.scheduler.pop(name, None)
            return []
        if word == "/file/print":
            return [{".id": f"*{i}", "name": name} for i, name in enumerate(self.files)]
        if word == "/file/remove":
            for name in args.get("names", []):
                self.files.pop(name, None)
            return []
        if word == "/import":
            if args["file-name"] not in self.files:
                raise CommandError(word, "no such file")
            self._apply_import()
            if session.kind == TransportKind.SSH and session.port != s.ssh_port:
                session.alive = False
                raise ConnectionLost("ssh session closed by port change")
            return []
        if word == "/system/reset-configuration":
            # A device refuses a just-written script for a moment; the flow waits it out.
            if self.reset_refusals > 0:
                self.reset_refusals -= 1
                raise CommandError(word, "cannot use file")
            self.run_after_reset = args["run-after-reset"]
            self.reset_args = dict(args)
            session.alive = False
            self.reboots += 1
            self._install_packages()
            kept_password = self.state.admin_password
            self.state = RouterState()
            if args.get("keep-users") == "yes":
                self.state.admin_password = kept_password
            self._run_after_reset(self.run_after_reset)
            raise ConnectionLost("rebooting")
        if word == "/system/identity/print":
            return [{"name": s.identity}]
        if word == "/system/resource/print":
            return [{"version": self.version, "architecture-name": "arm"}]
        if word == "/log/print":
            return [dict(row) for row in self.device_log]
        if word == "/ip/service/print":
            ports = {"telnet": 23, "ftp": 21, "www": 80, "ssh": s.ssh_port, "www-ssl": 443,
                     "api": 8728, "winbox": 8291, "api-ssl": 8729}
            return [{"name": n, "port": str(p), "disabled": "yes" if n in s.services_disabled
                     else "no"} for n, p in ports.items()]
        if word == "/interface/bridge/print":
            return [{"name": n, "protocol-mode": m} for n, m in s.bridges.items()]
        if word == "/ip/address/print":
            return [{"address": a, "interface": i} for a, i in s.addresses]
        if word == "/ip/address/remove":
            s.addresses = [(a, i) for a, i in s.addresses if a != args.get("address")]
            return []
        if word == "/interface/bridge/port/print":
            return [dict(p) for p in s.bridge_ports]
        if word == "/ip/dhcp-client/print":
            return [{"interface": i, "disabled": "no", "status": "bound"} for i in s.dhcp_clients]
        if word == "/ip/route/print":
            return [{"dst-address": "0.0.0.0/0", "gateway": g, "dynamic": "no"}
                    for g in s.default_gateways]
        raise CommandError(word, "bad command name")

    def _install_packages(self) -> None:
        if not self.installs_packages:
            return
        for name in [n for n in self.files if n in self.installable]:
            self.version = f"{self.installable[name]} (long-term)"
            self.files.pop(name)
            self.log.append(f"installed {name}")

    def _run_after_reset(self, name: str | None) -> None:
        """Runs the uploaded script's lines in order; a line with a parameter this device
        refuses stops it there, as 7.23.7 did (2026-09-24): logged, the file left behind."""
        script = self.files.get(name or "")
        if not isinstance(script, str):
            return
        lines = script.splitlines()
        stop = next((number for number, line in enumerate(lines, 1)
                     if self.refused_parameter and f" {self.refused_parameter}=" in line), None)
        ran = lines if stop is None else lines[:stop - 1]
        # The script sets admin's password only when it was given one.
        if any(line.startswith("/user set [ find name=admin ] password=") for line in ran):
            self.state.admin_password = str(self.pending_import_effects["admin_password"])
        if stop is not None:
            self.device_log.append({
                "topics": "system,error,critical",
                "message": "error while running run-after-reset script: bad parameter "
                           f"{self.refused_parameter} (line {stop} column 1)"})
            return
        self._apply_import()
        self.files.pop(name or "", None)

    def _apply_import(self) -> None:
        effects = self.pending_import_effects
        s = self.state
        s.identity = str(effects.get("identity", s.identity))
        s.ssh_port = int(effects.get("ssh_port", s.ssh_port))
        s.services_disabled = set(effects.get("services_disabled", s.services_disabled))
        s.bridges = dict(effects.get("bridges", s.bridges))
        s.addresses = list(effects.get("addresses", s.addresses))
        s.dhcp_clients = list(effects.get("dhcp_clients", s.dhcp_clients))
        s.default_gateways = list(effects.get("default_gateways", s.default_gateways))
        s.bridge_ports = [
            {"interface": p, "bridge": "bridge1", "hw": "yes",
             "hw-offload": "yes" if self.offload_running else "no"}
            for p in effects.get("ports", [])
        ]


class RouterSession:
    def __init__(self, router: FakeRouter, kind: TransportKind, port: int, username: str,
                 password: str) -> None:
        self.router = router
        self.kind = kind
        self.port = port
        self.params = ConnectionParams(host="192.168.88.1", username=username, password=password)
        self.alive = True
        self.commands: list[Command] = []

    @property
    def connected(self) -> bool:
        return self.alive

    @property
    def peer_security(self) -> PeerSecurity:
        return PeerSecurity.ANONYMOUS_DH

    def connect(self) -> None:
        self.alive = True

    def close(self) -> None:
        self.alive = False

    def _word(self, path: str) -> str:
        return api_word(*split_path(path))

    def execute(self, path: str, /, **args: object) -> Reply:
        if not self.alive or path.replace(" ", "/") in self.router.drop_on:
            self.alive = False
            raise ConnectionLost("session gone")
        values = {k.replace("_", "-"): ("yes" if v is True else "no" if v is False else str(v))
                  for k, v in args.items()}
        return self.router.handle(self, self._word(path), values)

    def file_reference(self, name: str) -> str:
        return name

    @contextlib.contextmanager
    def reply_timeout(self, seconds: float) -> Iterator[None]:
        yield

    def execute_command(self, command: Command) -> Reply:
        if not self.alive:
            raise ConnectionLost("session gone")
        self.commands.append(command)
        args: dict[str, object] = dict(command.args)
        if isinstance(command.selector, Find):
            args["names"] = [command.selector.get("name")]
            args |= dict(command.selector.conditions)
        return self.router.handle(self, command.api_path + "/" + command.verb, args)  # type: ignore[arg-type]


class RouterUploader(FileUploader):
    def __init__(self, router: FakeRouter, *, fail: bool = False, line_by_line: bool = False,
                 download_fail: bool = False, fail_partway: set[str] | None = None) -> None:
        super().__init__([], allow_line_by_line=line_by_line)
        self.router, self.fail, self.download_fail = router, fail, download_fail
        # Files whose transfer breaks off after part of them is written.
        self.fail_partway = fail_partway or set()
        self.uploaded: list[str] = []

    def upload(self, data: bytes, remote_path: str) -> UploadResult:
        if self.fail:
            return super().upload(data, remote_path)
        if remote_path in self.fail_partway:
            self.router.files[remote_path] = data[:10]
            raise UploadFailed([(UploadStrategy.SFTP, "connection reset")])
        # Scripts are text the fake runs; a package is bytes it only installs.
        self.router.files[remote_path] = (data.decode("utf-8") if remote_path.endswith(".rsc")
                                          else data)
        self.uploaded.append(remote_path)
        return UploadResult(UploadStrategy.SFTP, remote_path)

    def download(self, remote_path, local_path):
        if self.download_fail or remote_path not in self.router.files:
            raise UploadFailed([(UploadStrategy.SFTP, "no such file")])
        local_path.write_text(self.router.files[remote_path], encoding="utf-8")
        return UploadStrategy.SFTP
