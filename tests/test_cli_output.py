import pytest

from mqs.generator.ir import Find, cmd
from mqs.transport.base import api_word, args_from_kwargs, command_selector_args, split_path
from mqs.transport.cli_output import find_error, parse_key_value, parse_terse, sanitize


def test_sanitize_strips_ansi_and_carriage_returns():
    raw = "\x1b[9999B\x1b[m\x1b[32mname\x1b[0m: ether1\r\n  progress 10%\rprogress 100%\r\n\x07"
    assert sanitize(raw) == "name: ether1\nprogress 100%\n"
    assert sanitize("\x1b]0;title\x07ok  ") == "ok"


def test_a_carriage_return_at_the_end_of_a_line_rewrites_nothing():
    # RouterOS 6.49.10 over MAC-Telnet ends print lines with CR CR LF (2026-09-25).
    raw = "\r\n\r  name: MikroTik\r\r\n\r\r\r\r[admin@MikroTik] >    \r[admin@MikroTik] > "
    assert sanitize(raw) == "\n  name: MikroTik\n[admin@MikroTik] >"


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


def test_parse_terse_lower_case_flags():
    """Captured from `/ip service print terse` on a hEX S, 7.20.7: an open connection is
    flagged "D c", and the lower-case flag used to throw index and flags away."""
    text = (
        " 7     name=ssh port=122 proto=tcp address= vrf=main max-sessions=20\n"
        " 8 D c name=ssh port=122 proto=tcp local=192.168.55.68 remote=192.168.55.55:61425\n"
        "11  X  name=www-ssl port=443 proto=tcp address= certificate=none tls-version=any\n"
    )
    items = parse_terse(text)
    assert [(i["_index"], i["_flags"], i["name"]) for i in items] == [
        ("7", "", "ssh"), ("8", "Dc", "ssh"), ("11", "X", "www-ssl")]
    assert items[1]["remote"] == "192.168.55.55:61425"


def test_parse_terse_adjacent_flags():
    """Captured from `/interface bridge port print terse` on a hEX S, 7.20.7: an inactive
    port with hw offload is "IH", which used to lose index and flags, so the checklist
    saw no offload on every port without a cable."""
    text = (
        "0 IH interface=ether2 bridge=bridge priority=0x80 hw=yes pvid=1\n"
        "1  H interface=ether3 bridge=bridge priority=0x80 hw=yes pvid=1\n"
        "4 I  interface=sfp1 bridge=bridge priority=0x80 hw=no pvid=1\n"
    )
    items = parse_terse(text)
    assert [(i["_index"], i["_flags"], i["interface"]) for i in items] == [
        ("0", "IH", "ether2"), ("1", "H", "ether3"), ("4", "I", "sfp1")]


def test_parse_terse_empty_values_of_routeros_6():
    """Captured from 6.49.10 (v6 hEX S, 2026-09-25): an empty value is written "", a value
    with spaces is not quoted at all."""
    text = (
        " 3   name=ssh port=22 address=\"\" \r\n"
        " 0 * comment=contains all interfaces name=all dynamic=no include=\"\" exclude=\"\" \r\n"
    )
    ssh, all_list = parse_terse(text)
    assert ssh["address"] == ""
    assert all_list["comment"] == "contains all interfaces"
    assert (all_list["include"], all_list["exclude"]) == ("", "")


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
    assert split_path("/export") == ("/", "export")
    with pytest.raises(ValueError):
        split_path("/")
    assert api_word("/", "import") == "/import"
    assert api_word("/ip service", "set") == "/ip/service/set"
    assert args_from_kwargs({"address_pool": "p", "disabled": False, "port": 122}) == {
        "address-pool": "p", "disabled": "no", "port": "122"}


def test_command_selector_args():
    assert command_selector_args(cmd("/ip service", "set", selector="ssh")) == {"numbers": "ssh"}
    assert command_selector_args(cmd("/system logging", "set", selector=0)) == {"numbers": "0"}
    assert command_selector_args(cmd("/ip dns", "set")) == {}
    with pytest.raises(ValueError):
        command_selector_args(cmd("/x", "set", selector=Find.by(name="a")))
