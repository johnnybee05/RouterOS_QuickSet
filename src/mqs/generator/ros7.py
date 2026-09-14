"""RouterOS v7 dialect.

The editable hardening block is authored in v7 syntax, so v7 needs no translation of it.
This pass only checks version-dependent constructs inside the block.
"""

from __future__ import annotations

from mqs.generator.context import TargetContext
from mqs.generator.ir import Command, Script, SectionKey
from mqs.issues import Issue, Severity


class Dialect7:
    major = 7

    def translate(self, script: Script, ctx: TargetContext) -> list[Issue]:
        issues: list[Issue] = []
        section = script.section(SectionKey.SYSTEM)
        for item in section.items:
            if (
                isinstance(item, Command)
                and item.path == "/ipv6 settings"
                and item.has_arg("disable-ipv6")
                and not ctx.has("ipv6_disable_setting")
            ):
                issues.append(
                    Issue("v7.feature_missing", Severity.WARNING, "services.hardening_text",
                          {"version": str(ctx.version), "feature": "disable-ipv6"})
                )
        return issues
