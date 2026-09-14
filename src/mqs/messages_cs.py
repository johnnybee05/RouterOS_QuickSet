"""Czech message catalog for issues. Codes are stable identifiers used in code and tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MESSAGES: dict[str, str] = {
    # identity
    "identity.empty": "Identity zařízení nesmí být prázdná.",
    "identity.too_long": "Identity může mít nejvýše {max} znaků.",
    "identity.invalid_chars": "Identity smí obsahovat jen písmena bez diakritiky, číslice, tečku, "
    "pomlčku a podtržítko.",
    # WAN
    "wan.no_interface": "Vyberte rozhraní pro WAN.",
    "wan.interface_in_bridge": "Rozhraní {interface} je zvolené jako WAN, nemůže být zároveň "
    "členem bridge.",
    "wan.interface_unknown": "Rozhraní {interface} na zařízení neexistuje.",
    "wan.static.address_not_host": "Adresa {address} je adresa sítě nebo broadcast.",
    "wan.static.gateway_is_address": "Brána nesmí být stejná jako adresa WAN.",
    "wan.static.gateway_outside_subnet": "Brána {gateway} neleží v síti {network}, "
    "není dosažitelná.",
    "wan.pppoe.username_empty": "Zadejte uživatelské jméno PPPoE.",
    "wan.pppoe.password_empty": "Zadejte heslo PPPoE.",
    "wan.pppoe.interface_name_invalid": "Název PPPoE rozhraní obsahuje nepovolené znaky.",
    # bridge
    "bridge.name_invalid": "Název bridge obsahuje nepovolené znaky.",
    "bridge.port_duplicate": "Port {interface} je v bridge uvedený vícekrát.",
    "bridge.port_unknown": "Port {interface} na zařízení neexistuje.",
    "bridge.no_ports": "Bridge nemá žádné členské porty.",
    # LAN / DHCP
    "lan.address_missing": "Zadejte IP adresu bridge.",
    "lan.address_not_host": "Adresa {address} je adresa sítě nebo broadcast.",
    "lan.overlaps_wan": "Síť LAN {lan} se překrývá se sítí WAN {wan}.",
    "dhcp.requires_static": "DHCP server vyžaduje statickou IP adresu na bridge.",
    "dhcp.pool_outside_subnet": "Rozsah {pool} neleží celý v síti {network}.",
    "dhcp.pool_contains_address": "Rozsah {pool} obsahuje adresu bridge {address}.",
    "dhcp.pool_contains_reserved": "Rozsah {pool} obsahuje adresu sítě nebo broadcast.",
    "dhcp.gateway_outside_subnet": "Brána DHCP {gateway} neleží v síti {network}.",
    # firewall
    "firewall.fasttrack_shaping_unconfirmed": "FastTrack obchází simple queues a HTB. "
    "Potvrďte, že tvarování provozu nepoužíváte.",
    "firewall.fasttrack_pppoe_no_hw": "FastTrack s PPPoE WAN funguje, ale bez hardwarové "
    "akcelerace.",
    # mode
    "mode.router_without_wan": "Režim Router je zvolený, ale WAN je vypnutá.",
}


def format_message(code: str, params: Mapping[str, Any] | None = None) -> str:
    template = MESSAGES.get(code)
    if template is None:
        return code
    try:
        return template.format(**(params or {}))
    except (KeyError, IndexError):
        return template
