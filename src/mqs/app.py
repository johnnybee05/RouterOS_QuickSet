"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from mqs import __version__
from mqs.logging_setup import SecretRegistry, setup_logging
from mqs.transport.factory import TransportChoice
from mqs.ui import strings_cs as S
from mqs.ui.main_window import MainWindow
from mqs.ui.pages.device import DevicePage
from mqs.ui.theme import apply_theme, watch_theme


def resource_path(relative: str) -> Path:
    """Path to bundled resources, both from source and from a PyInstaller onefile build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / relative


def parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    """The window's own options; anything else is left to Qt (`-style`, `-platform`, …).

    They fill the *Připojit* form, nothing more: `--connect` then presses the button, so
    the window starts where it would after the first click. The names are the ones
    `python -m mqs.cli` already uses, and a password is taken from the environment for
    the same reason it is there — it has no business on a command line.
    """
    parser = argparse.ArgumentParser(prog="MikrotikQuickSetPlus",
                                     description="MikroTik QuickSet Plus")
    parser.add_argument("--host", help="prefill the device address")
    parser.add_argument("--user", help="prefill the user name")
    parser.add_argument("--password-env", metavar="VAR",
                        help="environment variable holding the password")
    parser.add_argument("--key", help="prefill the SSH private key file")
    parser.add_argument("--key-passphrase-env", metavar="VAR",
                        help="environment variable holding the passphrase of --key")
    parser.add_argument("--ca-file", help="prefill the CA bundle for api-ssl")
    parser.add_argument("--transport", choices=[c.value for c in TransportChoice])
    parser.add_argument("--api-port", type=int)
    parser.add_argument("--ssh-port", type=int)
    parser.add_argument("--connect", action="store_true",
                        help="connect as soon as the window is up (needs --host)")
    return parser.parse_known_args(list(argv))


def prefill(page: DevicePage, args: argparse.Namespace,
            env: dict[str, str] | None = None) -> bool:
    """Put the command line into the connect form. True when it also says to connect.

    Only what was passed is touched; the rest of the form keeps the defaults the page
    itself chose. Connecting without an address is nothing to do, so it is not asked for.

    A password variable that exists is an answer even when it is empty — that is how a
    key-only run says "no password", as in `mqs.cli`. A variable that does not exist
    leaves the field alone: unlike the command line the window can still be typed into.
    """
    environ = os.environ if env is None else env
    for value, field in ((args.host, page.host), (args.user, page.user),
                         (args.key, page.key_file), (args.ca_file, page.ca_file)):
        if value:
            field.setText(value)
    if args.password_env and args.password_env in environ:
        page.password.setText(environ[args.password_env])
    if args.key_passphrase_env and args.key_passphrase_env in environ:
        page.key_passphrase.setText(environ[args.key_passphrase_env])
    for value, box in ((args.api_port, page.api_port), (args.ssh_port, page.ssh_port)):
        if value:
            box.setValue(value)
    if args.transport:
        index = page.transport_choice.findData(TransportChoice(args.transport))
        if index >= 0:
            page.transport_choice.setCurrentIndex(index)
    return bool(args.connect and args.host)


def main(argv: Sequence[str] | None = None) -> int:
    given = list(argv) if argv is not None else sys.argv
    args, rest = parse_args(given[1:])
    app = QApplication([given[0] if given else "", *rest])
    app.setApplicationName(S.APP_TITLE)
    app.setApplicationVersion(__version__)
    icon = resource_path("assets/icon.ico")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    registry = SecretRegistry()
    log_file = setup_logging(registry)
    logging.getLogger("mqs").info("MikroTik QuickSet Plus %s started, log %s", __version__,
                                  log_file)

    window = MainWindow(registry=registry, dark=apply_theme(app))
    watch_theme(app, window.set_dark)
    connect_now = prefill(window.device_page, args)
    window.show()
    if connect_now:
        # After the window is on screen, so its progress is visible from the first step.
        QTimer.singleShot(0, window.device_page.connect_device)
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
