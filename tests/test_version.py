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
