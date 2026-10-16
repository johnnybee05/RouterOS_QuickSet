"""The pure core must not depend on GUI or network libraries."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "mqs"

PURE_PACKAGES = ["model", "capabilities", "generator", "profiles"]
PURE_MODULES = ["issues.py", "i18n.py", "messages.py", "messages_cs.py", "messages_en.py"]

FORBIDDEN = {
    "PySide6", "paramiko", "librouteros", "scp", "ifaddr",
    "socket", "ssl", "http", "urllib", "socketserver",
    "mqs.ui", "mqs.transport", "mqs.apply",
}


def _pure_files():
    for package in PURE_PACKAGES:
        yield from sorted((SRC / package).rglob("*.py"))
    for module in PURE_MODULES:
        yield SRC / module


def _imported_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


@pytest.mark.parametrize("path", list(_pure_files()), ids=lambda p: str(p.relative_to(SRC)))
def test_pure_core_has_no_forbidden_imports(path):
    for name in _imported_modules(path):
        for forbidden in FORBIDDEN:
            assert name != forbidden and not name.startswith(forbidden + "."), (
                f"{path.relative_to(SRC)} imports {name}"
            )


HEADLESS_PACKAGES = ["apply", "transport", "discover", "fleet"]
HEADLESS_MODULES = ["cli.py", "paths.py", "logging_setup.py", "selfupdate.py"]
GUI_ONLY = {"PySide6", "mqs.ui"}

# Detection has to combine several sources without one slow source blocking the rest, so
# the parts that reach out are threaded and awkward to test. These are the parts that
# decide things, and they are kept reachable without a network of any kind: a scan can be
# replayed from fixtures, and what it concludes is a unit test.
PURE_DISCOVER = ["candidates.py", "merge.py", "classify.py", "topology.py"]
NO_NETWORK = {"socket", "ssl", "http", "urllib", "socketserver", "paramiko", "scp",
              "librouteros", "ifaddr", "PySide6", "mqs.ui", "mqs.transport"}


@pytest.mark.parametrize("name", PURE_DISCOVER)
def test_the_deciding_half_of_detection_needs_no_network(name):
    path = SRC / "discover" / name
    for imported in _imported_modules(path):
        for forbidden in NO_NETWORK:
            assert imported != forbidden and not imported.startswith(forbidden + "."), (
                f"discover/{name} imports {imported}"
            )


def _headless_files():
    for package in HEADLESS_PACKAGES:
        yield from sorted((SRC / package).rglob("*.py"))
    for module in HEADLESS_MODULES:
        yield SRC / module


@pytest.mark.parametrize("path", list(_headless_files()), ids=lambda p: str(p.relative_to(SRC)))
def test_the_apply_flow_runs_without_a_gui(path):
    """`mqs.cli` drives the very flow the window drives, which is how it can be tested
    end to end against real hardware. That only holds while nothing the flow reaches
    needs a window."""
    for name in _imported_modules(path):
        for forbidden in GUI_ONLY:
            assert name != forbidden and not name.startswith(forbidden + "."), (
                f"{path.relative_to(SRC)} imports {name}"
            )
