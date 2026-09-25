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
    # port names
    "ports.unknown": "Port {default} na zařízení není (nebo není ethernetový), název {name} se "
    "mu nenastaví.",
    "ports.name_empty": "Port {default} nemá název.",
    "ports.name_unusual": "Název {name} (port {default}) obsahuje i jiné znaky než písmena bez "
    "diakritiky, číslice, tečku, pomlčku a podtržítko – RouterOS ho nemusí přijmout.",
    "ports.name_duplicate": "Název {name} mají porty {default} i {other}.",
    "ports.name_in_use": "Port {default} nelze přejmenovat na {name}: ten název má v tu chvíli "
    "ještě port {other} (po resetu je to jeho tovární název). Zvolte jiný název, nebo "
    "přejmenujte ve dvou krocích.",
    "ports.name_taken": "Název {name} (port {default}) už má jiné rozhraní – bridge, PPPoE nebo "
    "rozhraní na zařízení.",
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
    "lan.gateway_is_address": "Brána nesmí být stejná jako adresa bridge.",
    "lan.gateway_outside_subnet": "Brána {gateway} neleží v síti {network}, není dosažitelná.",
    "lan.gateway_requires_static": "Brána se použije jen se statickou adresou bridge – "
    "s jinou adresací se výchozí trasa nevygeneruje.",
    "lan.gateway_conflicts_wan": "Výchozí trasu už přidává WAN. Dvě výchozí trasy se budou "
    "překrývat – nechte jen jednu.",
    "dhcp.requires_static": "DHCP server vyžaduje statickou IP adresu na bridge.",
    "dhcp.pool_outside_subnet": "Rozsah {pool} neleží celý v síti {network}.",
    "dhcp.pool_contains_address": "Rozsah {pool} obsahuje adresu bridge {address}.",
    "dhcp.pool_contains_reserved": "Rozsah {pool} obsahuje adresu sítě nebo broadcast.",
    "dhcp.gateway_outside_subnet": "Brána DHCP {gateway} neleží v síti {network}.",
    "dhcp.dns_router_not_answering": "DHCP přiděluje jako DNS adresu routeru {address}, ale "
    "vzdálené DNS dotazy jsou vypnuté, takže router klientům neodpoví. Povolte je ve Službách "
    "a hardeningu, nebo pole DNS nechte prázdné.",
    "dhcp.no_dns_servers": "DHCP server nemá jaké DNS přidělit: vzdálené DNS dotazy jsou "
    "vypnuté a hardening blok ani statická WAN nemají DNS server s IPv4 adresou. Doplňte DNS "
    "ve Službách a hardeningu, zadejte ho u DHCP serveru, nebo povolte vzdálené DNS dotazy.",
    # DNS
    "dns.remote_requests_need_filter": "Vzdálené DNS dotazy (allow-remote-requests) jdou "
    "zapnout jen se základním filtrem, bez něj by router odpovídal na DNS i z WAN. Zůstanou "
    "vypnuté a DHCP server přidělí DNS servery ze Služeb a hardeningu.",
    # firewall
    "firewall.fasttrack_shaping_unconfirmed": "FastTrack obchází simple queues a HTB. "
    "Potvrďte, že tvarování provozu nepoužíváte.",
    "firewall.fasttrack_pppoe_no_hw": "FastTrack s PPPoE WAN funguje, ale bez hardwarové "
    "akcelerace.",
    # mode
    "mode.router_without_wan": "Režim Router je zvolený, ale WAN je vypnutá.",
    # hardening block parsing
    "hardening.parse.tokenize": "Řádek {line}: nelze rozebrat (neuzavřená uvozovka nebo "
    "závorka), předá se beze změny: {text}",
    "hardening.parse.unknown_verb": "Řádek {line}: neznámý příkaz, předá se beze změny: {text}",
    "hardening.parse.bad_argument": "Řádek {line}: nerozpoznaný parametr, předá se beze změny: "
    "{text}",
    "hardening.parse.no_menu": "Řádek {line}: příkaz bez menu (chybí řádek začínající /): {text}",
    "hardening.parse.scripting": "Řádek {line}: skriptovací příkaz se předá beze změny: {text}",
    # access paths / services
    "access.path_disabled": "Přístupová cesta {service} bude po aplikaci vypnutá.",
    "access.api_ssl_disabled": "api-ssl bude vypnuté – aplikace po aplikaci ztratí svůj hlavní "
    "transport.",
    "access.no_paths": "Po aplikaci nezůstane žádná přístupová cesta (api-ssl, SSH, Winbox). "
    "Aplikace konfigurace je zablokovaná.",
    "access.uncertain": "Některé změny v /ip service nelze jednoznačně vyhodnotit. "
    "Zkontrolujte přístupové cesty ručně.",
    "access.address_restricted": "Služba {service} bude dostupná jen z adres {address}.",
    "service.port_invalid": "Neplatný port {port} u služby {service} (povoleno 1–65535).",
    "service.port_collision": "Služby {services} používají stejný port {port}.",
    "service.unknown": "Neznámá služba {service} v /ip service.",
    "service.find_takes_connections": "Výběr přes [find …] v /ip service zahrne i otevřená "
    "spojení ke službě a RouterOS takovou změnu odmítne („this is configured elsewhere“). Při "
    "importu to u api-ssl nastane vždy, u ostatních služeb, když k nim je někdo připojený. "
    "Použijte zápis podle jména, např. „set {service} …“, nebo "
    "„[find name={service} dynamic=no]“.",
    # bridge HW offload
    "offload.version_too_old": "HW offload bridge vyžaduje RouterOS 6.41 nebo novější "
    "(zařízení má {version}), volba se nenabízí.",
    "offload.disabled_by_user": "Vypnuto uživatelem.",
    "offload.port_sfp": "SFP/SFP+ port se obvykle neoffloaduje.",
    "offload.port_wireless": "Bezdrátové rozhraní se neoffloaduje.",
    "offload.port_bonding": "Bonding se obvykle neoffloaduje.",
    "offload.port_other": "Rozhraní typu {type} se neoffloaduje.",
    "offload.port_no_switch": "Port není připojený ke switch čipu.",
    "offload.chip_unsupported": "MikroTik u čipu {chip_type} HW offload bridge nedoporučuje "
    "(podpora se teprve dokončuje). Ponechte softwarový bridge s RSTP.",
    "offload.use_ip_firewall": "use-ip-firewall=yes na bridgi vypíná HW offload.",
    "offload.mstp_unsupported": "protocol-mode=mstp na čipu {chip_type} offload vypíná.",
    "offload.mstp_conflict": "protocol-mode=mstp vypíná HW offload na čipu {chip_type} ({chip}). "
    "Přepněte na rstp, nebo HW offload vypněte.",
    "offload.vlan_filtering_unsupported": "vlan-filtering=yes vypíná HW offload na čipu "
    "{chip_type}.",
    "offload.stp_unsupported": "Čip {chip_type} s touto verzí RouterOS neoffloaduje bridge se "
    "STP/RSTP/MSTP.",
    "offload.stp_no_offload": "Čip {chip_type} ({chip}) s RouterOS {version} neoffloaduje bridge "
    "s protocol-mode={mode}, porty poběží přes CPU. Pro HW offload zvolte protocol-mode=none.",
    "offload.vlan_filtering_uncertain": "Offload s vlan-filtering na čipu {chip_type} není jistý, "
    "ověřte po aplikaci.",
    "offload.ok": "Offload na čipu {chip_type} ({chip}).",
    "offload.ok_unknown_chip": "Offload na čipu {chip_type} ({chip}); čip není v lokální "
    "databázi, platí obecná pravidla.",
    "offload.forced": "Port {port}: offload vynucen uživatelem (jinak: {original}).",
    "offload.software_bridge": "Bridge spojuje porty na více switch čipech – {chips}. Provoz mezi "
    "skupinami poběží softwarově přes CPU.",
    "offload.mixed_software": "Porty {ports} poběží softwarově; provoz mezi nimi a offloadovanými "
    "porty jde přes CPU.",
    "offload.no_device_data": "Bez připojeného zařízení nejsou známé switch čipy – hw= se "
    "negeneruje a RouterOS ponechá své výchozí chování.",
    # generator
    "target.unsupported_version": "RouterOS {version} není podporovaný (podporováno 6.40+ a 7.x).",
    "bridge.mstp_unsupported_version": "protocol-mode=mstp není na RouterOS {version} dostupný. "
    "Použijte rstp.",
    "bridge.mstp_needs_vlan_filtering": "RouterOS {version} přijme protocol-mode=mstp jen se "
    "zapnutým vlan-filtering. Zapněte vlan-filtering, nebo použijte rstp.",
    "fasttrack.hw_offload_skipped": "hw-offload na FastTrack pravidle se nevygeneruje – verze "
    "nebo hardware ho nepodporuje, případně to nebylo ověřeno na zařízení.",
    "dns.remote_requests_in_hardening": "Hardening blok nastavuje allow-remote-requests="
    "{value}. Běží až po volbě „Povolit vzdálené DNS dotazy“, takže ji přepíše – řádek "
    "z bloku odstraňte a použijte volbu.",
    "wan.static.dns_overridden": "DNS servery ze statické WAN přepíše nastavení /ip dns "
    "v hardening bloku.",
    "feature.unverified": "Použité neověřené rozdíly verzí (NUTNO OVĚŘIT): {features}",
    "logging.robust_unknown_index": "Logovací pravidlo s indexem {index} nemá známé topics, "
    "ponechává se index.",
    # v6 dialect translation
    "v6.ntp_too_many": "RouterOS v6 umí jen dva NTP servery (primary/secondary); vynechají se: "
    "{dropped}",
    "v6.ntp_hostname_unsupported": "NTP server {server} je hostname, který tato verze v6 neumí; "
    "vynechá se.",
    "v6.ntp_dropped_line": "Řádek z /system ntp client servers nelze přeložit do v6, vynechá se: "
    "{text}",
    "v6.ipv6_package_disable": "IPv6 se ve v6 vypne příkazem /system package disable ipv6 – "
    "projeví se až po restartu.",
    "v6.ipv6_omitted_missing": "Balíček ipv6 není nainstalovaný nebo povolený, IPv6 blok se "
    "vynechá.",
    "v6.ipv6_unknown_package": "Stav balíčku ipv6 není známý (offline), IPv6 blok se vynechá – "
    "ověřte na zařízení.",
    "v6.ipv6_dropped": "IPv6 není na zařízení aktivní, příkaz se vynechá: {text}",
    "v6.snmp_ipv6_address": "IPv6 adresa {address} v SNMP community se ve v6 vynechá.",
    "v6.feature_missing": "RouterOS {version} nemá parametr {feature}, příkaz se vynechá: {text}",
    "v6.raw_line_untranslated": "Řádek nelze přeložit do v6 a předá se beze změny, ověřte ho: "
    "{text}",
    "v7.feature_missing": "RouterOS {version} nemá parametr {feature}.",
    # apply preflight
    "apply.blocked_by_errors": "Konfigurace obsahuje chyby, aplikace na zařízení je zablokovaná.",
    "apply.not_connected": "Aplikace není připojená k zařízení.",
    "apply.dialect_mismatch_unconfirmed": "Skript je vygenerovaný pro RouterOS {target}, ale "
    "zařízení má {device}. Aplikaci je nutné výslovně potvrdit.",
    "apply.dialect_mismatch": "Skript pro RouterOS {target} se aplikuje na zařízení s {device} "
    "(potvrzeno uživatelem).",
    "apply.target_version_differs": "Skript je vygenerovaný pro {target}, zařízení má {device}.",
    "apply.reset_identity_mismatch": "Pro reset opište přesně identity zařízení: {identity}",
    "apply.reset_password_required": "Po resetu by admin neměl heslo – zadejte nové heslo.",
    "apply.reset_keep_users": "Reset zachová uživatele a jejich hesla (keep-users), aplikace "
    "se po něm připojí pod stejným účtem. Heslo admina se změní, jen pokud ho zadáte.",
    "apply.rollback_ignored_reset": "Rollback pojistka se u resetu nepoužije (reset smaže "
    "i naplánovanou úlohu).",
    "apply.rollback_too_short": "Rollback pojistka musí mít alespoň {minimum} minuty.",
    "apply.lockout_wan": "Aplikace je k zařízení připojená přes port {port}, který se stane WAN. "
    "Základní filtr bez správy z WAN tam zahodí nová spojení, takže se po aplikaci přes "
    "tento port znovu nepřipojí. Zapněte „Povolit správu z WAN“ nebo se připojte z LAN.",
    "apply.lockout_wan_pppoe": "Aplikace je k zařízení připojená přes port {port}, na kterém "
    "poběží PPPoE. Port sám nebude mít IP adresu, takže se po aplikaci přes něj znovu "
    "nepřipojí. Připojte se z LAN.",
    "apply.session_on_wan": "Aplikace je k zařízení připojená přes port {port}, který se stane "
    "WAN. Správa z WAN zůstane dostupná ({paths}).",
    "apply.lockout_unconfigured_port": "Aplikace je připojená přes port {port}, který po resetu "
    "nebude mít IP adresu ani nebude v bridge.",
    "apply.adopted": "Na zařízení už je {count} položek, které skript přidává ({items}). "
    "Skript je místo přidání upraví (set), položky bez dalších parametrů vynechá.",
    "apply.import_wan_port_bridged": "Port {port}, který má být WAN, je na zařízení v bridge "
    "{bridge}. Import ho z bridge nevyjme, takže WAN adresa na něm nebude platit a spojení "
    "přes něj se ztratí. Změnu režimu bridge → router proveďte resetem (Reset + aplikovat).",
    "apply.import_router_to_bridge": "Na zařízení je konfigurace routeru (WAN: {ports}), "
    "profil WAN nemá. Import ji neodstraní: zůstane seznam WAN a původní bridge s adresou "
    "a DHCP serverem. Změnu režimu router → bridge proveďte raději resetem.",
    "apply.session_port_to_bridge": "Aplikace je připojená přes port {port}, který import "
    "přidá do bridge {bridge}. Adresa na portu tím přestane platit, na bridge ji skript "
    "přesune ({address}) až o krok později, takže se spojení na chvíli přeruší.",
    "apply.session_port_to_bridge_no_address": "Aplikace je připojená přes port {port}, který "
    "import přidá do bridge {bridge}. Adresa na portu tím přestane platit a profil na bridge "
    "žádnou pevnou adresu nedává; po importu se k zařízení připojte přes MAC.",
    "apply.route_ambiguous":"Na zařízení je víc statických tras do {dst} (brány {gateways}). "
    "Skript žádnou nezmění a přidá další přes {gateway}; zkontrolujte je, jinak může provoz "
    "jít přes víc bran najednou.",
    "apply.own_rules_removed":"Profil už nepřidává pravidla do {table}; skript odtud "
    "odstraní pravidla z dřívějšího běhu QuickSet (komentář „quickset: …“, počet {count}).",
    "apply.bridge_port_moved":"Port {port} je teď v bridge {old}; skript ho přesune do "
    "bridge {new}. Pokud přes něj vede spojení, na chvíli se přeruší.",
    "apply.vlan_filtering_off": "Bridge {bridge} má na zařízení zapnutý vlan-filtering, "
    "profil ho nemá; skript ho vypne.",
    "apply.notice_ssh_port": "Po aplikaci poběží SSH na portu {port}.",
    "apply.notice_services_disabled": "Po aplikaci budou vypnuté služby: {services}.",
    "apply.notice_api_ssl_kept": "api-ssl ({port}) zůstává zapnuté – je to hlavní transport "
    "aplikace.",
    "apply.notice_rest_api": "REST API po aplikaci nepoběží, protože vyžaduje www-ssl.",
    "apply.notice_remaining_paths": "Zbývající přístupové cesty ({count}): {paths}.",
    # post-apply verification (checklist)
    "verify.identity": "Identity: očekáváno {expected}, na zařízení {actual}",
    "verify.services": "Služby /ip service odpovídají (rozdíly: {services})",
    "verify.bridge": "Bridge {name}: protocol-mode očekáván {expected}, na zařízení {actual}",
    "verify.lan_address": "Adresa {address} na {bridge}",
    "verify.default_route": "Výchozí trasa přes {expected} (statické výchozí trasy na zařízení: "
    "{actual})",
    "verify.dhcp_client": "DHCP klient na {interface} (stav: {status})",
    "verify.hw_offload":"HW offload odpovídá požadavku (rozdílů: {count})",
    "verify.offload_not_active": "Port {port}: hw=yes je nastaveno, ale offload na zařízení "
    "neběží. Důvod podle detekce: {reason}",
    "verify.offload_port_missing": "Port {port} na zařízení není v bridge.",
    "verify.offload_unexpected": "Port {port}: offload běží, přestože nebyl požadován.",
    "verify.offload_unreadable": "Skutečný stav HW offloadu nelze přečíst: {error}",
    # apply steps (progress log)
    "apply.step.backup": "Záloha a export konfigurace ({name})",
    "apply.step.download": "Stažení zálohy do {dir}",
    "apply.step.upload": "Nahrání skriptu {file} (způsob: {strategy})",
    "apply.step.rollback": "Rollback pojistka naplánována na {at} (čas zařízení)",
    "apply.step.execute_import": "Import skriptu {file}",
    "apply.step.execute_lines": "Aplikace příkazů řádek po řádku ({count})",
    "apply.step.execute_reset": "Reset konfigurace, po restartu se spustí {file}",
    "apply.reset_retry": "Zařízení zatím nahraný skript nepřijalo, zkouším znovu "
    "({attempt}/{total}).",
    "apply.step.reconnect": "Připojení po aplikaci na {hosts} (api-ssl {api_port}, "
    "SSH {ssh_port})",
    "apply.step.verify": "Ověření konfigurace na zařízení",
    "apply.step.cleanup": "Odstranění nahraného skriptu ze zařízení",
    "apply.step.prune": "Smazání starých záloh ze zařízení: {count} souborů (ponechány "
    "poslední {keep})",
    "apply.step.confirm": "Konfigurace potvrzena, rollback pojistka zrušena",
    "apply.connection_lost": "Spojení se ztratilo během kroku {step}, zkouším se přepojit.",
    "apply.rollback_pending": "Rollback pojistka obnoví zálohu v {at}, pokud konfiguraci "
    "nepotvrdíte.",
    "apply.cancelled": "Aplikace přerušena uživatelem, konfigurace zařízení se nezměnila.",
    "apply.failed.backup": "Zálohu se nepodařilo vytvořit, na zařízení se nic nezměnilo: {error}",
    "apply.failed.upload": "Skript se nepodařilo nahrát na zařízení: {error}",
    "apply.failed.upload_reset_needs_file": "Reset vyžaduje nahraný soubor, ale žádný způsob "
    "přenosu nefungoval.",
    "apply.failed.rollback": "Rollback pojistku se nepodařilo naplánovat, nic se nezměnilo: "
    "{error}",
    "apply.failed.execute": "Aplikace skriptu selhala: {error}",
    "apply.failed.reconnect": "Spojení se nepodařilo obnovit ({attempts} pokusů, api-ssl "
    "{api_port}, SSH {ssh_port}): {error}",
    "apply.failed.verify": "Ověření konfigurace selhalo: {error}",
    "apply.failed.confirm": "Rollback pojistku se nepodařilo zrušit: {error}",
    "apply.warning.download": "Soubor {file} se nepodařilo stáhnout (zůstává na zařízení): "
    "{error}",
    "apply.warning.cleanup": "Nahraný skript se nepodařilo smazat: {error}",
    "apply.warning.prune": "Staré zálohy se ze zařízení nepodařilo smazat: {error}",
    # RouterOS update from the Update directory
    "update.planned": "Po resetu se nainstaluje RouterOS {version} (zařízení má {current}): "
    "{files}. Skript se vygeneruje pro {version} a bez vlastností, které aplikace ověřila "
    "zkouškou na {current} (hw-offload u FastTracku).",
    "update.no_dir": "Adresář s aktualizacemi {dir} neexistuje, RouterOS se nebude aktualizovat.",
    "update.v7_only": "Aktualizace z adresáře Update je jen pro RouterOS 7, zařízení má "
    "{current}. RouterOS se nebude aktualizovat.",
    "update.unknown_arch": "Architekturu zařízení „{arch}“ aplikace nezná, RouterOS se nebude "
    "aktualizovat.",
    "update.no_package": "V adresáři {dir} není balíček RouterOS pro architekturu {arch}, "
    "RouterOS se nebude aktualizovat.",
    "update.up_to_date": "Zařízení už má RouterOS {current}, nejnovější balíček v adresáři "
    "Update je {version}. Aktualizace není potřeba.",
    "update.other_major": "Balíček RouterOS {version} je pro jinou hlavní verzi, než má zařízení "
    "({current}). Přechod mezi v6 a v7 aplikace nedělá, RouterOS se nebude aktualizovat.",
    "update.extra_missing": "Na zařízení je balíček {name} {installed}, ale v adresáři Update "
    "pro něj chybí verze {version} ({arch}). Po aktualizaci nemusí fungovat.",
    "update.unreadable": "Soubor {file} v adresáři Update se nedá použít: {error}",
    "apply.step.update_upload": "Nahrání aktualizace {file} ({size} MB, způsob: {strategy})",
    "apply.step.update_withdrawn": "Nahraná aktualizace smazána ze zařízení: {files}",
    "apply.failed.update_upload": "Aktualizaci {file} se nepodařilo nahrát, konfigurace "
    "zařízení se nezměnila: {error}",
    "apply.warning.update_left": "Aktualizace {files} zůstala na zařízení a nainstaluje se při "
    "příštím restartu. Smažte ji ve Files: {error}",
    "apply.warning.update_leftover": "Balíček {files} zůstal po restartu na zařízení "
    "(nenainstaloval se), aplikace ho smazala.",
    "update.routerboot": "Po aplikaci se aktualizuje RouterBOOT z {current} na {version} a "
    "zařízení se ještě jednou restartuje.",
    "update.routerboot_after": "Po aktualizaci RouterOS se aktualizuje i RouterBOOT (teď "
    "{current}) na {version} a zařízení se ještě jednou restartuje.",
    "apply.step.routerboot": "Aktualizace RouterBOOT {current} → {version} a restart",
    "apply.step.routerboot_current": "RouterBOOT {current} je aktuální",
    "apply.warning.routerboot": "RouterBOOT se nepodařilo aktualizovat, konfigurace zůstává: "
    "{error}",
    "verify.routeros_version": "RouterOS {expected} (na zařízení {actual})",
    "verify.routerboot": "RouterBOOT {expected} (na zařízení {actual})",
    "verify.reset_script": "Skript po resetu doběhl do konce (chyba: {error})",
    "verify.import_script": "Importovaný skript doběhl do konce (chyba: {error})",
    # UI input syntax
    "ui.syntax": "{message}",
    # profiles
    "profile.invalid_json": "Soubor není platný JSON.",
    "profile.wrong_format": "Soubor není profil MikroTik QuickSet Plus.",
    "profile.unsupported_version": "Profil má nepodporovanou verzi formátu.",
    "profile.invalid": "Profil obsahuje neplatné hodnoty.",
    "profile.unreadable": "Soubor s profilem nelze přečíst.",
    "profile.name_required": "Zadejte název profilu.",
    "profile.not_found": "Profil neexistuje.",
}


# One apply step, keyed by StepStatus value: the window and the CLI label them alike.
STEP_PREFIX: dict[str, str] = {
    "running": "…", "ok": "OK", "warning": "!", "failed": "CHYBA", "skipped": "–",
}


def format_message(code: str, params: Mapping[str, Any] | None = None) -> str:
    template = MESSAGES.get(code)
    if template is None:
        return code
    try:
        return template.format(**(params or {}))
    except (KeyError, IndexError):
        return template
