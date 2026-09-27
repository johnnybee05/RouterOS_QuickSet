"""The RouterBOOT upgrade, shared by the apply and the version change.

RouterBOOT comes with RouterOS: installing a package puts a new firmware image in
`upgrade-firmware`, and `/system routerboard upgrade` writes it — only a reboot then
starts it. Both things that install a package (the update inside an apply, and the
version change) therefore leave the same job behind, and it is done here so they do it
the same way and report it with the same message codes.

Nothing is upgraded when `upgrade-firmware` is not newer than `current-firmware`, which
is what keeps this out of a downgrade's way: after a RouterOS downgrade the available
firmware reads *older* than the running one (6.49.12 next to 6.49.21, OVĚŘENO
2026-09-27), and after a downgrade across the major version the two are equal.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mqs.apply.events import ApplyEvent, StepStatus
from mqs.apply.update import firmware_newer
from mqs.apply.verify import CheckResult
from mqs.issues import Issue, Severity
from mqs.model.inventory import DeviceInventory
from mqs.transport.base import (
    CommandError,
    CommandTimeout,
    ConnectionLost,
    Transport,
    TransportError,
)
from mqs.transport.discovery import to_bool

REBOOT_REPLY_S = 10.0
REBOOT_WAIT_S = 20.0


@dataclass(frozen=True)
class RouterbootState:
    """What `/system routerboard print` says about the firmware."""

    is_routerboard: bool = False
    current: str = ""
    available: str = ""

    @property
    def upgradable(self) -> bool:
        return self.is_routerboard and firmware_newer(self.available, self.current)

    @property
    def older(self) -> bool:
        """The firmware on offer is *behind* the running one, as after a RouterOS
        downgrade. Writing it is possible — `/system routerboard upgrade` writes whatever
        the installed RouterOS carries, OVĚŘENO 2026-09-27 — but never implied."""
        return self.is_routerboard and firmware_newer(self.current, self.available)

    def writable(self, *, allow_older: bool = False) -> bool:
        return self.upgradable or (allow_older and self.older)


def read_state(transport: Transport) -> RouterbootState:
    """CHR and x86 have no RouterBOOT and answer with an error; that is a state too."""
    try:
        rows = transport.execute("/system/routerboard/print")
    except CommandError:
        return RouterbootState()
    board = rows[0] if rows else {}
    return RouterbootState(to_bool(board, "routerboard"),
                           board.get("current-firmware", ""),
                           board.get("upgrade-firmware", ""))


def state_from_inventory(inventory: DeviceInventory) -> RouterbootState:
    """The same state out of what discovery already read, for planning without asking."""
    board = inventory.routerboard
    if board is None:
        return RouterbootState()
    return RouterbootState(board.routerboard, board.current_firmware, board.upgrade_firmware)


def pending_issue(state: RouterbootState, code: str, *, allow_older: bool = False,
                  lower_code: str = "") -> Issue | None:
    """`{code}` (INFO) when a firmware is waiting to be written, else nothing.

    With ``allow_older`` an older firmware counts as waiting too, and is reported under
    ``lower_code`` so the message can say which way it is going.
    """
    if not state.upgradable:
        if allow_older and state.older:
            return Issue(lower_code or code, Severity.INFO, "",
                         {"current": state.current or "?", "version": state.available})
        return None
    return Issue(code, Severity.INFO, "",
                 {"current": state.current or "?", "version": state.available})


@dataclass
class RouterbootUpgrade:
    """`/system routerboard upgrade`, the reboot that starts it, and the check after.

    The caller lends its own machinery: `event` and `warn` put this in the same log as
    every other step, `reconnect` is the caller's own way back to the device (an apply
    keeps a lent address, a version change has a whole ladder), and `checks` is where the
    result is recorded. `transport` is the connection before the reboot; `run` returns the
    one after it, which the caller keeps using.
    """

    transport: Transport
    event: Callable[..., None]
    warn: Callable[..., None]
    reconnect: Callable[[], Transport]
    checks: list[CheckResult]
    sleep: Callable[[float], None]
    wait_s: float = REBOOT_WAIT_S
    # "Snížit RouterBOOT": write the firmware even when it is older than the running one.
    # Never implied by anything — a RouterOS downgrade does not take the bootloader with it.
    allow_older: bool = False

    def run(self) -> Transport:
        state = read_state(self.transport)
        if not state.writable(allow_older=self.allow_older):
            self.event("apply.step.routerboot_current", StepStatus.SKIPPED,
                       current=state.current or "-")
            return self.transport
        step = "apply.step.routerboot_downgrade" if state.older else "apply.step.routerboot"
        params = {"current": state.current, "version": state.available}
        self.event(step, StepStatus.RUNNING, **params)
        try:
            self.transport.execute("/system/routerboard/upgrade")
            with self.transport.reply_timeout(REBOOT_REPLY_S):
                self.transport.execute("/system/reboot")
        except (ConnectionLost, CommandTimeout):
            pass  # the reboot takes the session along
        except TransportError as exc:
            # Nothing was written; the old RouterBOOT keeps running and so does everything
            # else on the device.
            self.warn("apply.warning.routerboot", error=str(exc))
            self.checks.append(CheckResult("verify.routerboot", False,
                                           {"expected": state.available,
                                            "actual": state.current}))
            return self.transport
        self.transport.close()
        self.event(step, StepStatus.OK, **params)
        self.sleep(self.wait_s)
        self.transport = self.reconnect()
        # A device that came back but will not say what it is running is the caller's
        # failure to report: the `TransportError` goes up as it did before this was shared.
        actual = read_state(self.transport).current
        self.checks.append(CheckResult("verify.routerboot", actual == state.available,
                                       {"expected": state.available, "actual": actual or "-"}))
        return self.transport


__all__ = ["ApplyEvent", "RouterbootState", "RouterbootUpgrade", "pending_issue",
           "read_state", "state_from_inventory"]
