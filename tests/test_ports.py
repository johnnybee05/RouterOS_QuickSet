"""Port names: default-name vs. the name a device or a profile gives a port."""

from factories import make_inventory, router_profile
from mqs.model.inventory import InterfaceInfo
from mqs.model.ports import (
    adopt_device_names,
    final_port_names,
    natural_key,
    port_renames,
    rename_port,
    renames_to_emit,
)
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode

HEX_S = ["ether1", "ether2", "ether3", "ether4", "ether5", "sfp1"]
# The hEX S of 2026-09-23 after the ports were renamed in Winbox.
HEX_S_NAMED = {"ether1-uplink": "ether1", "ether2-zakaznik1": "ether2",
               "ether3-zakaznik2": "ether3", "ether4-zakaznik3": "ether4",
               "ether5-zakaznik4": "ether5", "sfp1-optika": "sfp1"}


def named_hex_s():
    return make_inventory("7.20.7", list(HEX_S_NAMED), default_names=HEX_S_NAMED)


def test_a_profile_made_with_factory_names_fits_a_renamed_device():
    # The bridge profile of the hEX S, loaded after its ports were renamed on the device.
    profile = apply_preset(DeviceProfile(identity="AP"), Mode.BRIDGE_AP, HEX_S)
    adopted = adopt_device_names(profile, named_hex_s())
    assert adopted.bridge.ports == list(HEX_S_NAMED)
    # The names are the profile's now, so a reset (factory names back) keeps them.
    assert adopted.interface_names == {d: n for n, d in HEX_S_NAMED.items()}
    assert adopt_device_names(adopted, named_hex_s()) is adopted


def test_the_wan_port_is_followed_to_its_device_name():
    profile = router_profile(["ether1", "ether2"])  # WAN ether1, bridge ether2
    inventory = make_inventory("7.20.7", ["wan", "ether2"], default_names={"wan": "ether1"})
    adopted = adopt_device_names(profile, inventory)
    assert adopted.wan.interface == "wan" and adopted.bridge.ports == ["ether2"]
    assert adopted.interface_names == {"ether1": "wan"}


def test_nothing_to_adopt_on_factory_names():
    profile = router_profile()
    assert adopt_device_names(profile, make_inventory()) is profile


def test_the_profile_name_wins_over_the_device_name():
    profile = router_profile(["ether1", "ether2"]).model_copy(update={
        "interface_names": {"ether1": "internet"}})
    profile = profile.model_copy(update={"wan": profile.wan.model_copy(
        update={"interface": "ether1-uplink"})})  # picked before the profile renamed it
    inventory = make_inventory("7.20.7", ["ether1-uplink", "ether2"],
                               default_names={"ether1-uplink": "ether1"})
    adopted = adopt_device_names(profile, inventory)
    assert adopted.interface_names == {"ether1": "internet"}
    assert adopted.wan.interface == "internet"
    assert port_renames(adopted.interface_names, inventory) == {"ether1-uplink": "internet"}
    assert final_port_names(adopted, inventory) == ["internet", "ether2"]


def test_a_renamed_wireless_port_is_found_but_not_renamed():
    profile = apply_preset(DeviceProfile(), Mode.BRIDGE_AP, ["ether1", "wlan1"])
    inventory = make_inventory("7.20.7", ["ether1", "wlan-2g"],
                               port_types={"wlan-2g": "wlan"},
                               default_names={"wlan-2g": "wlan1"})
    adopted = adopt_device_names(profile, inventory)
    assert adopted.bridge.ports == ["ether1", "wlan-2g"]
    assert adopted.interface_names == {}


def test_rename_port_carries_the_references():
    profile = router_profile(["ether1", "ether2", "ether3"])
    renamed = rename_port(profile, "ether1", "internet")
    assert renamed.wan.interface == "internet"
    renamed = rename_port(renamed, "ether2", "lan2")
    assert renamed.bridge.ports == ["lan2", "ether3"]
    assert renamed.interface_names == {"ether1": "internet", "ether2": "lan2"}
    back = rename_port(renamed, "ether2", "ether2")
    assert back.bridge.ports == ["ether2", "ether3"]


def test_renames_to_emit():
    names = {"ether10": "uplink", "ether2": "lan2", "ether1": "ether1"}
    # Offline the device is assumed to have its factory names.
    assert renames_to_emit(names, None) == [("ether2", "lan2"), ("ether10", "uplink")]
    inventory = make_inventory("7.20.7", ["ether1-old", "ether2", "ether3"],
                               default_names={"ether1-old": "ether1"})
    # Online a factory name is set back; a port the device lacks is left out.
    assert renames_to_emit(names, inventory) == [("ether1", "ether1"), ("ether2", "lan2")]
    assert renames_to_emit({}, inventory) == []


def test_natural_key():
    assert sorted(["ether10", "ether2", "sfp1", "ether1"], key=natural_key) == [
        "ether1", "ether2", "ether10", "sfp1"]


def test_inventory_renamed():
    inventory = make_inventory("7.20.7", ["ether1", "ether2"])
    inventory = inventory.model_copy(update={"interfaces": [
        *inventory.interfaces, InterfaceInfo(name="bridge", type="bridge")]})
    renamed = inventory.renamed({"ether1": "wan"})
    assert [i.name for i in renamed.interfaces] == ["wan", "ether2", "bridge"]
    assert renamed.interface("wan").default_name == "ether1"
    assert inventory.renamed({}) is inventory
