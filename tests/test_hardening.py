import pytest

from mqs.generator.hardening import binding
from mqs.generator.hardening.access import analyze_access_text
from mqs.generator.hardening.parser import parse, tokenize, unquote
from mqs.generator.ir import Command, Comment, Find, RawLine, Script, SectionKey
from mqs.generator.render import render_script
from mqs.issues import Severity, codes, has_errors
from mqs.model.inventory import ServiceInfo
from mqs.model.services import DEFAULT_HARDENING_V7

# ---------------------------------------------------------------- parser


def test_default_block_parses_cleanly():
    block = parse(DEFAULT_HARDENING_V7)
    assert block.problems == []
    commands = block.commands
    assert len(commands) == 19
    first = commands[0]
    assert first.path == "/snmp community"
    assert first.selector == Find(conditions=(("default", "yes"),))
    logging = [c for c in commands if c.path == "/system logging"]
    assert [c.selector for c in logging] == [0, 1, 2, 3]
    services = [c for c in commands if c.path == "/ip service"]
    assert [c.selector for c in services] == ["ftp", "telnet", "www", "ssh", "api"]
    ntp = [c.arg("address") for c in commands if c.path == "/system ntp client servers"]
    assert ntp == ["195.113.144.201", "195.113.144.238"]


def test_default_block_round_trips_through_renderer():
    script = Script()
    script.section(SectionKey.SYSTEM).add(*parse(DEFAULT_HARDENING_V7).items)
    rendered = render_script(script).split("\n", 1)[1]  # drop section title
    assert rendered == DEFAULT_HARDENING_V7


def test_inline_command_and_comment():
    block = parse("# note\n/ip dns set servers=1.1.1.1\nset allow-remote-requests=no\n")
    assert isinstance(block.lines[0].item, Comment)
    inline = block.lines[1]
    assert inline.inline and inline.menu == "/ip dns"
    assert block.lines[2].item == Command("/ip dns", "set", (("allow-remote-requests", "no"),))
    assert not block.lines[2].inline


def test_continuation_and_quotes():
    text = '/system note\nset note="line \\"one\\"" \\\n    show-at-login=no\n'
    block = parse(text)
    assert block.problems == []
    command = block.commands[0]
    assert command.arg("note") == 'line "one"'
    assert command.arg("show-at-login") == "no"
    assert block.lines[1].line_count == 2


def test_raw_find_selector():
    block = parse('/system logging\nset [ find where topics~"info" ] action=disk\n')
    assert block.commands[0].selector == Find(raw='where topics~"info"')
    block = parse("/x\nset [ find name=\"a b\" ] disabled=yes\n")
    assert block.commands[0].selector == Find(conditions=(("name", "a b"),))
    block = parse("/x\nremove [ find ]\n")
    assert block.commands[0].selector == Find()


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("/ip dns\nprint\n", "unknown_verb"),
        (":delay 5s\n", "scripting"),
        ("set servers=1.1.1.1\n", "no_menu"),
        ('/ip dns\nset servers="1.1.1.1\n', "tokenize"),
        ("/ip dns\nset [ find name=x disabled=yes\n", "tokenize"),
        ("/ip dns\nset ] x=1\n", "tokenize"),
        ("/ip service\nset ssh port\n", "bad_argument"),
        ("/ip service\nadd =x\n", "bad_argument"),
        ("/ip service\nset [ foo ] disabled=yes\n", "bad_argument"),
    ],
)
def test_parse_problems_keep_raw_lines(text, reason):
    block = parse(text)
    assert [p.reason for p in block.problems] == [reason]
    raw = [i for i in block.items if isinstance(i, RawLine)]
    assert len(raw) == 1
    issue = block.problems[0].to_issue()
    assert issue.code == f"hardening.parse.{reason}"
    assert issue.severity == Severity.WARNING
    assert str(block.problems[0].line_no + 1) in issue.message


def test_tokenize_and_unquote_edges():
    assert tokenize('set a="x y" [ find b=1 ]') == ["set", 'a="x y"', "[ find b=1 ]"]
    assert unquote('"a\\x"') == "a\\x"
    assert unquote('"tab\\tend"') == "tab\tend"
    assert unquote("bare") == "bare"
    assert unquote('"') == '"'


def test_complex_find_falls_back_to_raw():
    block = parse('/x\nset [ find "odd ] disabled=yes\n')
    assert block.problems  # unterminated quote inside brackets
    block = parse("/x\nset [ find default ] disabled=yes\n")
    assert block.commands[0].selector == Find(raw="default")


# ---------------------------------------------------------------- binding


def test_read_fields_default():
    fields = binding.read_fields(DEFAULT_HARDENING_V7)
    assert fields == binding.HardeningFields(
        dns_servers=("192.168.200.200", "8.8.8.8"),
        ssh_port=122,
        snmp_community="PVIP",
        ntp_servers=("195.113.144.201", "195.113.144.238"),
        time_zone="Europe/Prague",
    )


def test_read_fields_empty():
    assert binding.read_fields("") == binding.HardeningFields()


def _changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    return [(a, b) for a, b in zip(before.split("\n"), after.split("\n"), strict=False) if a != b]


def test_set_ssh_port_changes_single_line():
    after = binding.set_ssh_port(DEFAULT_HARDENING_V7, 2222)
    assert binding.read_fields(after).ssh_port == 2222
    assert _changed_lines(DEFAULT_HARDENING_V7, after) == [
        ("set ssh port=122", "set ssh port=2222")
    ]


def test_set_dns_servers():
    after = binding.set_dns_servers(DEFAULT_HARDENING_V7, ["1.1.1.1", "9.9.9.9"])
    assert binding.read_fields(after).dns_servers == ("1.1.1.1", "9.9.9.9")
    assert len(_changed_lines(DEFAULT_HARDENING_V7, after)) == 1


def test_set_time_zone_appends_missing_menu():
    after = binding.set_time_zone("/ip dns\nset servers=1.1.1.1\n", "UTC")
    assert after == "/ip dns\nset servers=1.1.1.1\n/system clock\nset time-zone-name=UTC\n"
    assert binding.set_time_zone("", "UTC") == "/system clock\nset time-zone-name=UTC\n"


def test_set_ssh_port_inserts_into_existing_menu():
    text = "/ip service\nset telnet disabled=yes\n/ip dns\nset servers=1.1.1.1\n"
    after = binding.set_ssh_port(text, 122)
    assert after == (
        "/ip service\nset telnet disabled=yes\nset ssh port=122\n/ip dns\nset servers=1.1.1.1\n"
    )


def test_set_arg_extends_existing_set_line_without_key():
    after = binding.set_ssh_port("/ip service\nset ssh disabled=no\n", 122)
    assert after == "/ip service\nset ssh disabled=no port=122\n"


def test_inline_form_is_preserved():
    after = binding.set_dns_servers("/ip dns set servers=1.1.1.1\n", ["8.8.4.4"])
    assert after == "/ip dns set servers=8.8.4.4\n"


def test_selector_by_find_name():
    after = binding.set_ssh_port("/ip service\nset [ find name=ssh ] port=22\n", 122)
    assert after == "/ip service\nset [ find name=ssh ] port=122\n"


def test_set_snmp_community():
    after = binding.set_snmp_community(DEFAULT_HARDENING_V7, "NEWCOMM")
    assert binding.read_fields(after).snmp_community == "NEWCOMM"
    assert "add addresses=::/0 name=NEWCOMM" in after
    assert "set enabled=yes trap-community=NEWCOMM trap-version=2" in after

    fresh = binding.set_snmp_community("", "C1")
    assert fresh == "/snmp community\nadd addresses=::/0 name=C1\n/snmp\nset trap-community=C1\n"
    inline = binding.set_snmp_community("/snmp community add name=A\n", "B")
    assert inline.startswith("/snmp community add name=B\n")


def test_set_ntp_servers():
    one = binding.set_ntp_servers(DEFAULT_HARDENING_V7, ["10.0.0.1"])
    assert binding.read_fields(one).ntp_servers == ("10.0.0.1",)
    assert one.endswith("/system ntp client servers\nadd address=10.0.0.1\n")

    three = binding.set_ntp_servers(one, ["a.example", "b.example", "c.example"])
    assert binding.read_fields(three).ntp_servers == ("a.example", "b.example", "c.example")

    fresh = binding.set_ntp_servers("/ip dns\nset servers=1.1.1.1\n", ["1.2.3.4"])
    assert fresh.endswith("/system ntp client servers\nadd address=1.2.3.4\n")
    assert binding.set_ntp_servers("/ip dns\n", []) == "/ip dns\n"


# ---------------------------------------------------------------- access


def test_default_block_access():
    report = analyze_access_text(DEFAULT_HARDENING_V7)
    assert report.remaining == ("api-ssl", "ssh", "winbox")
    assert report.ssh_port == 122
    assert report.api_ssl_port == 8729
    assert not report.blocked
    assert not has_errors(report.issues)
    assert "access.path_disabled" not in codes(report.issues)
    assert report.services["api"].disabled and report.services["telnet"].disabled


def test_disabling_one_path_warns():
    report = analyze_access_text(DEFAULT_HARDENING_V7 + "/ip service\nset winbox disabled=yes\n")
    assert report.disabled_paths == ("winbox",)
    issue = next(i for i in report.issues if i.code == "access.path_disabled")
    assert issue.params == {"service": "winbox"}
    assert not has_errors(report.issues)


def test_no_paths_blocks():
    text = "/ip service\nset [ find ] disabled=yes\n"
    report = analyze_access_text(text)
    assert report.blocked
    assert {"access.no_paths", "access.api_ssl_disabled"} <= codes(report.issues)
    assert has_errors(report.issues)


def test_disable_and_enable_verbs_and_find_name():
    text = (
        "/ip service\ndisable ssh\ndisable winbox\nenable winbox\n"
        "set [ find name=api-ssl ] disabled=yes\n"
    )
    report = analyze_access_text(text)
    assert report.remaining == ("winbox",)


def test_device_state_is_base():
    device = [
        ServiceInfo(name="api-ssl", port=8729, disabled=True),
        ServiceInfo(name="ssh", port=22),
        ServiceInfo(name="winbox", port=8291),
    ]
    report = analyze_access_text("/ip service\nset ssh port=122\n", device)
    assert "access.api_ssl_disabled" in codes(report.issues)
    assert report.ssh_port == 122


def test_port_collision_and_invalid():
    report = analyze_access_text("/ip service\nset ssh port=8291\n")
    collision = next(i for i in report.issues if i.code == "service.port_collision")
    assert collision.params == {"services": "ssh, winbox", "port": 8291}

    for bad in ("70000", "abc", "0"):
        report = analyze_access_text(f"/ip service\nset ssh port={bad}\n")
        assert "service.port_invalid" in codes(report.issues)
        assert report.ssh_port == 22


def test_uncertain_unknown_and_address():
    report = analyze_access_text('/ip service\nset [ find where port=22 ] disabled=yes\n')
    assert report.uncertain and "access.uncertain" in codes(report.issues)
    report = analyze_access_text("/ip service\nset [ find name=ssh port=22 ] disabled=yes\n")
    assert report.uncertain
    report = analyze_access_text("/ip service\nset 0 disabled=yes\n")
    assert report.uncertain
    report = analyze_access_text("/ip service\nset gopher disabled=yes\n")
    assert "service.unknown" in codes(report.issues)
    report = analyze_access_text("/ip service\nset ssh address=10.0.0.0/24\n")
    restricted = next(i for i in report.issues if i.code == "access.address_restricted")
    assert restricted.severity == Severity.INFO
