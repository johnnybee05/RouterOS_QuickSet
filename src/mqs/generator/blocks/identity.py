"""Section 1: /system identity."""

from __future__ import annotations

from mqs.generator.context import TargetContext
from mqs.generator.ir import Script, SectionKey, cmd
from mqs.issues import Issue
from mqs.model.profile import DeviceProfile


def build(profile: DeviceProfile, ctx: TargetContext, script: Script) -> list[Issue]:
    script.section(SectionKey.IDENTITY).add(cmd("/system identity", "set", name=profile.identity))
    return []
