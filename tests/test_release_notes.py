"""Vydání se generuje z CHANGELOG.md, takže parsování changelogu má testy."""

import pytest

from tools.release_notes import (
    ChangelogError,
    main,
    parse,
    previous_version,
    promote_headings,
    render,
    section_for,
)

CHANGELOG = """# Changelog

Co se v které verzi změnilo. Novější nahoře.

## [0.2.0] – 2026-10-02

Cesty, kudy se zařízení dá dosáhnout.

### Přidáno

- balíček po zapůjčené adrese

### Opraveno

- na zapůjčenou adresu se čeká

## [0.1.1] – 2026-09-28

Opravné vydání.

### Opraveno

- cizí zařízení na zapůjčené adrese se odmítne

## [0.1.0] – 2026-09-28

První vydání.

[0.2.0]: https://github.com/o/r/releases/tag/v0.2.0
[0.1.1]: https://github.com/o/r/releases/tag/v0.1.1
[0.1.0]: https://github.com/o/r/releases/tag/v0.1.0
"""


def test_parse_keeps_the_order_of_the_file():
    assert [section.version for section in parse(CHANGELOG)] == ["0.2.0", "0.1.1", "0.1.0"]


def test_a_section_carries_its_date_and_body():
    section = section_for(CHANGELOG, "0.2.0")
    assert section.date == "2026-10-02"
    assert section.body.startswith("Cesty, kudy se zařízení dá dosáhnout.")
    assert "### Opraveno" in section.body


def test_a_section_ends_at_the_next_version():
    assert "Opravné vydání" not in section_for(CHANGELOG, "0.2.0").body


def test_link_definitions_are_not_part_of_the_last_section():
    assert "releases/tag" not in section_for(CHANGELOG, "0.1.0").body


def test_an_unknown_version_names_the_ones_that_are_there():
    with pytest.raises(ChangelogError) as error:
        section_for(CHANGELOG, "0.3.0")
    assert "0.2.0" in str(error.value) and "0.3.0" in str(error.value)


def test_a_version_without_text_is_not_a_release():
    changelog = "# Changelog\n\n## [0.3.0] – 2026-10-03\n\n## [0.2.0] – 2026-10-02\n\nUž je.\n"
    with pytest.raises(ChangelogError, match="bez textu"):
        section_for(changelog, "0.3.0")


def test_previous_version_comes_from_the_changelog():
    assert previous_version(CHANGELOG, "0.2.0") == "0.1.1"
    assert previous_version(CHANGELOG, "0.1.1") == "0.1.0"


def test_the_first_version_has_nothing_to_compare_with():
    assert previous_version(CHANGELOG, "0.1.0") is None


def test_headings_inside_a_version_move_up_one_level():
    assert promote_headings("### Přidáno\n- a\n") == "## Přidáno\n- a"


def test_a_heading_inside_a_code_block_stays_as_it_is():
    body = "```\n### ne nadpis\n```\n### Přidáno"
    assert promote_headings(body) == "```\n### ne nadpis\n```\n## Přidáno"


def test_a_version_heading_inside_a_code_block_does_not_split_the_section():
    changelog = (
        "# Changelog\n\n## [0.2.0] – 2026-10-02\n\n"
        "Takhle se oddíl píše:\n\n```\n## [0.9.9] – 2026-01-01\n```\n\n"
        "## [0.1.0] – 2026-09-28\n\nPrvní.\n"
    )
    assert [section.version for section in parse(changelog)] == ["0.2.0", "0.1.0"]
    assert "0.9.9" in section_for(changelog, "0.2.0").body


def test_notes_have_the_changes_the_file_to_download_and_the_compare_link(tmp_path):
    asset = tmp_path / "MikrotikQuickSetPlus.exe"
    asset.write_bytes(b"x" * (54 * 1024 * 1024 + 900 * 1024))
    notes = render(
        section_for(CHANGELOG, "0.2.0"),
        repo="o/r",
        previous=previous_version(CHANGELOG, "0.2.0"),
        asset=asset,
    )
    assert notes.startswith("Cesty, kudy se zařízení dá dosáhnout.")
    assert "## Přidáno" in notes and "### Přidáno" not in notes
    assert "`MikrotikQuickSetPlus.exe` (54,9 MB)" in notes
    assert notes.rstrip().endswith("https://github.com/o/r/compare/v0.1.1...v0.2.0")


def test_notes_without_a_build_and_without_a_predecessor():
    notes = render(section_for(CHANGELOG, "0.1.0"), repo="o/r", previous=None)
    assert "Ke stažení" not in notes and "compare" not in notes


def _changelog_file(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG, encoding="utf-8")
    return path


def test_check_passes_for_a_version_that_is_written_down(tmp_path, capsys):
    code = main(["--version", "0.2.0", "--changelog", str(_changelog_file(tmp_path)), "--check"])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_check_fails_for_a_version_that_is_not(tmp_path, capsys):
    code = main(["--version", "0.3.0", "--changelog", str(_changelog_file(tmp_path)), "--check"])
    assert code == 1
    assert "0.3.0" in capsys.readouterr().err


def test_the_cli_writes_the_notes_to_a_file(tmp_path):
    out = tmp_path / "notes.md"
    code = main(
        [
            "--version",
            "0.1.1",
            "--changelog",
            str(_changelog_file(tmp_path)),
            "--repo",
            "o/r",
            "--output",
            str(out),
        ]
    )
    assert code == 0
    notes = out.read_text(encoding="utf-8")
    assert "cizí zařízení na zapůjčené adrese se odmítne" in notes
    assert notes.rstrip().endswith("compare/v0.1.0...v0.1.1")


def test_the_cli_says_so_when_the_build_is_missing(tmp_path, capsys):
    code = main(
        [
            "--version",
            "0.2.0",
            "--changelog",
            str(_changelog_file(tmp_path)),
            "--repo",
            "o/r",
            "--asset",
            str(tmp_path / "nic.exe"),
        ]
    )
    assert code == 1
    assert "neexistuje" in capsys.readouterr().err


def test_the_title_is_the_application_and_the_version(tmp_path, capsys):
    code = main(["--version", "0.2.0", "--changelog", str(_changelog_file(tmp_path)), "--title"])
    assert code == 0
    assert capsys.readouterr().out.strip() == "MikroTik QuickSet Plus 0.2.0"


def test_the_real_changelog_matches_the_version_in_the_package():
    from pathlib import Path

    import mqs

    changelog = Path(__file__).parent.parent / "CHANGELOG.md"
    section = section_for(changelog.read_text(encoding="utf-8"), mqs.__version__)
    assert section.date, "vydaná verze musí mít v changelogu datum"
