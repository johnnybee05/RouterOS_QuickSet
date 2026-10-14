"""One device of a batch: connect, check the plan still holds, change the version.

Shared by `mqs fleet` and the window's *Hromadný update* page, so the part that reboots hardware is
written once. Raising is how a device reports failure to the batch, and nothing here
swallows an error: a device that did not come back must stop everything behind it.

The plan a batch is shown was worked out from the inventory, which is whatever the last
login read — possibly days ago. So before anything is uploaded the device is planned again
from what it says *now*, and a route that differs stops the batch rather than running a
plan made for a device that is no longer there.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from mqs.apply.orchestrator import ApplyEvent
from mqs.apply.session import (
    DeviceSession,
    build_version_context,
    session_after_version_change,
)
from mqs.apply.version_change import VersionChangeAborted, VersionChanger, VersionPlan
from mqs.fleet.batch import DeviceStep
from mqs.issues import Issue, Severity
from mqs.model.inventory import DeviceInventory


def route(plan: VersionPlan | None) -> tuple[str, ...]:
    """The versions a plan walks through, which is what has to match."""
    return tuple(str(hop.version) for hop in plan.hops) if plan is not None else ()


def update_device(
    step: DeviceStep,
    open_session: Callable[[str], DeviceSession],
    *,
    on_event: Callable[[ApplyEvent], None],
    close: Callable[[DeviceSession], None],
    replan: Callable[[DeviceInventory], VersionPlan] | None = None,
    read_again: Callable[[DeviceSession], DeviceInventory] | None = None,
    changer_factory: Callable[..., Any] = VersionChanger,
    **context: Any,
) -> DeviceInventory | None:
    """Change one device's version; return what it looks like afterwards, if asked.

    ``context`` goes to `build_version_context` as it is (``download_dir``,
    ``keep_backups``, ``upgrade_routerboot`` ...). ``read_again`` reads the device once it
    is back; a failure there is not a failed update — the version change is verified by
    then — so it returns None instead of raising.
    """
    address = step.device.address
    if not address:
        raise RuntimeError(_text("fleet.no_address", name=step.name))
    session = open_session(address)
    try:
        plan = step.plan
        if plan is None:  # plan_batch never runs a device it could not plan
            raise RuntimeError(f"{step.name}: no plan")
        if replan is not None:
            fresh = replan(session.inventory)
            if route(fresh) != route(plan) or fresh.current != plan.current:
                raise RuntimeError(_text(
                    "fleet.stale", name=step.name, planned=plan.current,
                    route=" → ".join(route(plan)) or "-", current=fresh.current,
                    fresh=" → ".join(route(fresh)) or "-"))
        ctx = build_version_context(plan, session, on_event=on_event, **context)
        report = changer_factory(ctx).run()
        session_after_version_change(session, report)
        if not report.succeeded:
            failed = [c.message for c in report.checks if not c.ok]
            raise RuntimeError(_text("fleet.check_failed",
                                     detail="; ".join(failed) or "?"))
        if read_again is None:
            return None
        try:
            return read_again(session)
        except Exception:  # the update is done and verified; only the re-read failed
            return None
    except VersionChangeAborted as exc:
        session_after_version_change(session, exc.report)
        raise RuntimeError(exc.issue.message) from exc
    finally:
        # Closing a session is not part of updating a device: a failure there must not
        # turn a device that came back into a device that failed.
        with contextlib.suppress(Exception):
            close(session)


def _text(code: str, **params: Any) -> str:
    return Issue(code, Severity.ERROR, "", params).message
