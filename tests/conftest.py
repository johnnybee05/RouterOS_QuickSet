from __future__ import annotations

import os
from pathlib import Path

# GUI tests run headless.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

GOLDEN_DIR = Path(__file__).parent / "golden"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite golden .rsc files instead of comparing against them.",
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


@pytest.fixture
def golden(update_golden: bool):
    """Compare text with tests/golden/<name>, or rewrite it with --update-golden."""

    def check(name: str, actual: str) -> None:
        path = GOLDEN_DIR / name
        if update_golden or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(actual, encoding="utf-8", newline="\n")
            if not update_golden:
                pytest.fail(f"golden file {name} did not exist and was created; review it")
            return
        expected = path.read_text(encoding="utf-8")
        assert actual == expected, f"output differs from golden file {name}"

    return check


@pytest.fixture(autouse=True)
def czech_texts(monkeypatch: pytest.MonkeyPatch):
    """Tests compare Czech texts; the language must not follow the machine they run on, nor
    leak from a test that switches it (`mqs.cli` sets it on every run)."""
    from mqs import i18n

    monkeypatch.setenv(i18n.ENV, "cs")
    i18n.set_language("cs")
    yield
    i18n.set_language("cs")
