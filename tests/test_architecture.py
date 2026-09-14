"""The pure core must not depend on GUI or network libraries."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "mqs"

PURE_PACKAGES = ["model", "capabilities", "generator", "profiles"]
PURE_MODULES = ["issues.py", "messages_cs.py"]

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
