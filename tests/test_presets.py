from factories import FIVE_PORTS, make_inventory
from mqs.issues import has_errors
from mqs.model.bridge import LanAddressing
from mqs.model.presets import DEFAULT_LAN_ADDRESS, apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.services import ServicesConfig
from mqs.model.validation import validate_profile


def test_router_preset():
    profile = apply_preset(DeviceProfile(identity="R1"), Mode.ROUTER, FIVE_PORTS)
    assert profile.mode == Mode.ROUTER
    assert profile.wan.enabled and profile.wan.interface == "ether1"
    assert profile.wan.nat_masquerade and profile.wan.add_to_wan_list
    assert profile.bridge.ports == ["ether2", "ether3", "ether4", "ether5"]
    assert profile.lan.address == DEFAULT_LAN_ADDRESS
    assert profile.lan.dhcp_server.enabled
    assert profile.firewall.basic_filter


def test_bridge_ap_preset():
    profile = apply_preset(DeviceProfile(identity="AP1"), Mode.BRIDGE_AP, FIVE_PORTS)
    assert not profile.wan.enabled and profile.wan.interface is None
    assert not profile.wan.nat_masquerade
    assert profile.bridge.ports == FIVE_PORTS
    assert profile.lan.addressing == LanAddressing.DHCP_CLIENT
    assert not profile.lan.dhcp_server.enabled
    assert not profile.firewall.basic_filter
    assert not has_errors(validate_profile(profile, make_inventory()))


def test_preset_without_basic_filter_turns_remote_dns_off():
    profile = DeviceProfile(identity="AP1")
    profile = profile.model_copy(update={
        "services": profile.services.model_copy(update={"dns_remote_requests": True})})
    assert apply_preset(profile, Mode.ROUTER, FIVE_PORTS).router_is_dns
    bridge = apply_preset(profile, Mode.BRIDGE_AP, FIVE_PORTS)
    assert not bridge.services.dns_remote_requests


def test_custom_preset_is_empty():
    profile = apply_preset(DeviceProfile(identity="X"), Mode.CUSTOM, FIVE_PORTS)
    assert not profile.wan.enabled
    assert profile.bridge.ports == []
    assert profile.lan.addressing == LanAddressing.NONE
    assert not profile.lan.dhcp_server.enabled
    assert not has_errors(validate_profile(profile))


def test_preset_keeps_identity_name_and_hardening():
    base = DeviceProfile(
        name="pobocka",
        identity="R7",
        services=ServicesConfig(hardening_text="/ip dns\nset servers=1.1.1.1\n"),
    )
    profile = apply_preset(base, Mode.ROUTER, FIVE_PORTS)
    assert profile.name == "pobocka"
    assert profile.identity == "R7"
    assert profile.services.hardening_text == "/ip dns\nset servers=1.1.1.1\n"
