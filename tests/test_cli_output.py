import pytest

from mqs.generator.ir import Find, cmd
from mqs.transport.base import args_from_kwargs, command_selector_args, split_path
from mqs.transport.cli_output import find_error, parse_key_value, parse_terse, sanitize


def test_sanitize_strips_ansi_and_carriage_returns():
    raw = "\x1b[9999B\x1b[m\x1b[32mname\x1b[0m: ether1\r\n  progress 10%\rprogress 100%\r\n\x07"
    assert sanitize(raw) == "name: ether1\nprogress 100%\n"
    assert sanitize("\x1b]0;title\x07ok  ") == "ok"


def test_find_error():
    assert find_error("bad command name printt (line 1 column 12)") == \
        "bad command name printt (line 1 column 12)"
    assert find_error("ok\nfailure: already have interface with such name\n").startswith("failure:")
    assert find_error("expected end of command (line 1 column 5)")
    assert find_error("   name: ether1\n") is None


def test_parse_terse():
    text = (
        "Flags: D - dynamic, X - disabled, R - running\n"
        " 0  R name=ether1 default-name=ether1 type=ether mtu=1500 comment=to ISP uplink\n"
        " 1 X  name=ether2 default-name=ether2 type=ether mtu=1500\n"
        "12 D R name=bridge type=bridge\n"
        "\n"
    )
    items = parse_terse(text)
    assert items[0] == {"_index": "0", "_flags": "R", "name": "ether1",
                        "default-name": "ether1", "type": "ether", "mtu": "1500",
                        "comment": "to ISP uplink"}
    assert items[1]["_flags"] == "X"
    assert items[2]["_flags"] == "DR" and items[2]["_index"] == "12"
    assert len(items) == 3


def test_parse_terse_without_index():
    assert parse_terse("name=switch1 type=RTL8367\n") == [{"name": "switch1", "type": "RTL8367"}]
    assert parse_terse("garbage line\n") == []


def test_parse_key_value():
    text = (
        "                   uptime: 1d2h3m\n"
        "                  version: 7.15.3 (stable)\n"
        "               board-name: RB4011iGS+\n"
        "                      cpu: ARM\n"
        "                           continued\n"
    )
    assert parse_key_value(text) == {"uptime": "1d2h3m", "version": "7.15.3 (stable)",
                                     "board-name": "RB4011iGS+", "cpu": "ARM continued"}
    assert parse_key_value("  continuation without key\n") == {}


def test_split_path_and_args():
    assert split_path("/system/resource/print") == ("/system resource", "print")
    assert split_path("/interface bridge port print") == ("/interface bridge port", "print")
    with pytest.raises(ValueError):
        split_path("/print")
    assert args_from_kwargs({"address_pool": "p", "disabled": False, "port": 122}) == {
        "address-pool": "p", "disabled": "no", "port": "122"}


def test_command_selector_args():
    assert command_selector_args(cmd("/ip service", "set", selector="ssh")) == {"numbers": "ssh"}
    assert command_selector_args(cmd("/system logging", "set", selector=0)) == {"numbers": "0"}
    assert command_selector_args(cmd("/ip dns", "set")) == {}
    with pytest.raises(ValueError):
        command_selector_args(cmd("/x", "set", selector=Find.by(name="a")))
