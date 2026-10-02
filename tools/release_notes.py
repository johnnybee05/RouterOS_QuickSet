"""Poznámky k vydání z CHANGELOG.md.

Changelog je zdroj: text vydání na GitHubu se z něj generuje, nepíše se podruhé.
Modul sám nic nepublikuje a nic nespouští po síti -- jen přečte soubor a složí
text, který `release.ps1` předá `gh release create`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HEADING = re.compile(r"^##\s+\[(?P<version>[^\]]+)\]\s*(?:[–\-]\s*(?P<date>\S.*?))?\s*$")
LINK_DEFINITION = re.compile(r"^\[[^\]]+\]:\s")
FENCE = re.compile(r"^\s*(?:```|~~~)")

DOWNLOAD_HEADING = "## Ke stažení"
DOWNLOAD_NOTE = "jediný přenositelný soubor, Python není potřeba, nic se neinstaluje."
CHANGES_LABEL = "**Úplný seznam změn**"


class ChangelogError(Exception):
    """Changelog neobsahuje to, co vydání potřebuje."""


@dataclass(frozen=True)
class Section:
    """Jedna verze v changelogu."""

    version: str
    date: str | None
    body: str


def parse(text: str) -> list[Section]:
    """Rozdělí changelog na verze v tom pořadí, v jakém jsou v souboru."""
    sections: list[Section] = []
    version: str | None = None
    date: str | None = None
    lines: list[str] = []
    in_fence = False

    def flush() -> None:
        if version is not None:
            sections.append(Section(version, date, "\n".join(lines).strip("\n")))

    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else HEADING.match(line)
        if match:
            flush()
            version, date = match.group("version"), match.group("date")
            lines = []
            continue
        if version is None:
            continue
        if not in_fence and LINK_DEFINITION.match(line):
            continue
        lines.append(line)

    flush()
    return sections


def section_for(text: str, version: str) -> Section:
    """Verze z changelogu, nebo `ChangelogError` s tím, co tam je."""
    sections = parse(text)
    for section in sections:
        if section.version == version:
            if not section.body.strip():
                raise ChangelogError(f"Verze {version} je v changelogu bez textu")
            return section
    known = ", ".join(section.version for section in sections) or "žádné"
    raise ChangelogError(f"Changelog nemá oddíl '## [{version}]' (má: {known})")


def previous_version(text: str, version: str) -> str | None:
    """Verze zapsaná pod danou verzí, tedy ta předchozí. U první vrátí None."""
    versions = [section.version for section in parse(text)]
    try:
        index = versions.index(version)
    except ValueError as error:
        raise ChangelogError(f"Changelog nemá oddíl '## [{version}]'") from error
    rest = versions[index + 1 :]
    return rest[0] if rest else None


def promote_headings(body: str) -> str:
    """Oddíly uvnitř verze zvedne o úroveň -- v changelogu `###`, ve vydání `##`."""
    out: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        if not in_fence and line.startswith("### "):
            line = line[1:]
        out.append(line)
    return "\n".join(out)


def download_block(asset: Path) -> str:
    """Odstavec o souboru ke stažení, s jeho skutečnou velikostí."""
    megabytes = asset.stat().st_size / (1024 * 1024)
    size = f"{megabytes:.1f}".replace(".", ",")
    return f"{DOWNLOAD_HEADING}\n\n`{asset.name}` ({size} MB) — {DOWNLOAD_NOTE}"


def render(
    section: Section,
    *,
    repo: str,
    previous: str | None = None,
    asset: Path | None = None,
) -> str:
    """Složí text vydání: changelog, soubor ke stažení a odkaz na porovnání."""
    parts = [promote_headings(section.body).strip("\n")]
    if asset is not None:
        parts.append(download_block(asset))
    if previous:
        compare = f"https://github.com/{repo}/compare/v{previous}...v{section.version}"
        parts.append(f"{CHANGES_LABEL}: {compare}")
    return "\n\n".join(parts) + "\n"


def repo_from_git(root: Path) -> str:
    """owner/name z remote origin, aby odkazy nemusely být nikde napsané."""
    url = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    if not match:
        raise ChangelogError(f"Z remote origin nejde přečíst owner/name: {url}")
    return match.group(1)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="verze bez 'v', např. 0.2.0")
    parser.add_argument("--changelog", type=Path, default=root / "CHANGELOG.md")
    parser.add_argument(
        "--previous",
        default="auto",
        help="předchozí verze pro odkaz na porovnání: 'auto' (z changelogu) nebo 'none'",
    )
    parser.add_argument("--asset", type=Path, help="soubor ke stažení (jeho velikost se vypíše)")
    parser.add_argument("--repo", help="owner/name; bez něj se vezme z remote origin")
    parser.add_argument("--output", type=Path, help="kam text zapsat; bez něj na výstup")
    parser.add_argument(
        "--check",
        action="store_true",
        help="jen ověří, že changelog verzi má (nic nevypíše)",
    )
    parser.add_argument("--title", action="store_true", help="vypíše název vydání")
    args = parser.parse_args(argv)

    try:
        text = args.changelog.read_text(encoding="utf-8")
        section = section_for(text, args.version)
        if args.check:
            return 0
        if args.title:
            print(f"MikroTik QuickSet Plus {section.version}")
            return 0
        if args.previous == "auto":
            previous = previous_version(text, args.version)
        elif args.previous == "none":
            previous = None
        else:
            previous = args.previous.lstrip("v")
        if args.asset is not None and not args.asset.exists():
            raise ChangelogError(f"Soubor ke stažení neexistuje: {args.asset}")
        notes = render(
            section,
            repo=args.repo or repo_from_git(root),
            previous=previous,
            asset=args.asset,
        )
    except (ChangelogError, OSError, subprocess.CalledProcessError) as error:
        print(f"chyba: {error}", file=sys.stderr)
        return 1

    if args.output:
        args.output.write_text(notes, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
