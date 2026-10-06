import pytest
from pydantic import BaseModel, ValidationError

from mqs.model.version import RosVersion


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7.15.3 (stable)", (7, 15, 3, "", "stable")),
        ("6.49.10 (long-term)", (6, 49, 10, "", "long-term")),
        ("6.40", (6, 40, 0, "", "")),
        ("7.16beta2 (testing)", (7, 16, 0, "beta2", "testing")),
        ("7.1rc5", (7, 1, 0, "rc5", "")),
    ],
)
def test_parse(text, expected):
    v = RosVersion.parse(text)
    assert (v.major, v.minor, v.patch, v.prerelease, v.channel) == expected


def test_parse_invalid():
    with pytest.raises(ValueError):
        RosVersion.parse("seven")


def test_ordering():
    assert RosVersion.parse("6.40") < RosVersion.parse("6.41") < RosVersion.parse("7.1")
    assert RosVersion.parse("7.16beta2") < RosVersion.parse("7.16")
    assert RosVersion.parse("7.15 (stable)") == RosVersion.parse("7.15.0")
    assert RosVersion.parse("7.15") != "7.15"
    assert len({RosVersion.parse("7.15"), RosVersion.parse("7.15.0 (stable)")}) == 1


def test_at_least():
    v = RosVersion.parse("6.41.2")
    assert v.at_least(6, 41)
    assert v.at_least(6, 41, 2)
    assert not v.at_least(6, 42)
    assert not v.at_least(7)


def test_str():
    assert str(RosVersion.parse("7.15.3 (stable)")) == "7.15.3"
    assert str(RosVersion.parse("6.40")) == "6.40"
    assert str(RosVersion.parse("7.16beta2")) == "7.16beta2"


class _Holder(BaseModel):
    version: RosVersion


def test_pydantic_field_roundtrip():
    holder = _Holder(version="6.49.10")
    assert holder.version.minor == 49
    dumped = holder.model_dump_json()
    assert dumped == '{"version":"6.49.10"}'
    assert _Holder.model_validate_json(dumped) == holder


def test_pydantic_field_invalid():
    with pytest.raises(ValidationError):
        _Holder(version="x.y")


# A version quoted inside a longer line, which is how every passive source gives one.


def test_the_version_is_found_inside_an_mndp_announcement():
    """7.23.6 announces `7.23.6 (long-term) 2026-09-14 10:14:57` — OVĚŘENO on an RB4011
    and a hAP ac3 on the lab network, 2026-10-06. Strict parsing yields nothing there,
    and two scans then never notice a device that changed version."""
    assert RosVersion.find("7.23.6 (long-term) 2026-09-14 10:14:57") == \
        RosVersion.parse("7.23.6")


def test_the_version_is_found_inside_a_sentence():
    assert RosVersion.find("RouterOS 7.16.1 on RB760iGS") == RosVersion.parse("7.16.1")


def test_a_date_is_not_mistaken_for_a_version():
    assert RosVersion.find("2026-09-14 10:14:57") is None


def test_a_line_with_no_version_has_none():
    assert RosVersion.find("no version here") is None
    assert RosVersion.find("") is None


def test_a_prerelease_inside_a_line_is_found_whole():
    assert RosVersion.find("7.16rc3 (testing) 2026-01-01") == RosVersion.parse("7.16rc3")


def test_a_clean_version_is_found_as_itself():
    for text in ("6.49.10", "7.23.6 (long-term)", "7.16"):
        assert RosVersion.find(text) == RosVersion.parse(text)
