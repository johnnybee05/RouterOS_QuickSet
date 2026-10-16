"""English message catalog for issues. Same codes as mqs.messages_cs, which is the source."""

from __future__ import annotations

MESSAGES: dict[str, str] = {
    # identity
    "identity.empty": "The device identity must not be empty.",
    "identity.too_long": "The identity can have at most {max} characters.",
    "identity.invalid_chars": "The identity may contain only letters without diacritics, "
    "digits, dot, hyphen and underscore.",
    # port names
    "ports.unknown": "Port {default} is not on the device (or is not ethernet), the name "
    "{name} will not be set on it.",
    "ports.name_empty": "Port {default} has no name.",
    "ports.name_unusual": "The name {name} (port {default}) contains other characters than "
    "letters without diacritics, digits, dot, hyphen and underscore – RouterOS may not accept "
    "it.",
    "ports.name_duplicate": "Ports {default} and {other} both have the name {name}.",
    "ports.name_in_use": "Port {default} cannot be renamed to {name}: at that moment the name "
    "still belongs to port {other} (after a reset it is its factory name). Choose another "
    "name, or rename in two steps.",
    "ports.name_taken": "The name {name} (port {default}) already belongs to another "
    "interface – a bridge, PPPoE or an interface on the device.",
    # WAN
    "wan.no_interface": "Choose the WAN interface.",
    "wan.interface_in_bridge": "Interface {interface} is chosen as WAN, it cannot be a bridge "
    "member at the same time.",
    "wan.interface_unknown": "Interface {interface} does not exist on the device.",
    "wan.static.address_not_host": "Address {address} is a network or broadcast address.",
    "wan.static.gateway_is_address": "The gateway must not be the same as the WAN address.",
    "wan.static.gateway_outside_subnet": "Gateway {gateway} is not in network {network}, "
    "it is unreachable.",
    "wan.pppoe.username_empty": "Enter the PPPoE user name.",
    "wan.pppoe.password_empty": "Enter the PPPoE password.",
    "wan.pppoe.interface_name_invalid": "The PPPoE interface name contains invalid characters.",
    "wan.vlan.name_invalid": "The VLAN interface name contains invalid characters.",
    "wan.vlan.name_taken": "The name {name} already belongs to another interface on the "
    "device; name the VLAN interface differently.",
    # bridge
    "bridge.name_invalid": "The bridge name contains invalid characters.",
    "bridge.port_duplicate": "Port {interface} is listed in the bridge more than once.",
    "bridge.port_unknown": "Port {interface} does not exist on the device.",
    "bridge.no_ports": "The bridge has no member ports.",
    # LAN / DHCP
    "lan.address_missing": "Enter the bridge IP address.",
    "lan.address_not_host": "Address {address} is a network or broadcast address.",
    "lan.overlaps_wan": "The LAN network {lan} overlaps the WAN network {wan}.",
    "lan.gateway_is_address": "The gateway must not be the same as the bridge address.",
    "lan.gateway_outside_subnet": "Gateway {gateway} is not in network {network}, it is "
    "unreachable.",
    "lan.gateway_requires_static": "The gateway is used only with a static bridge address – "
    "with other addressing no default route is generated.",
    "lan.gateway_conflicts_wan": "WAN already adds a default route. Two default routes will "
    "overlap – keep only one.",
    "dhcp.requires_static": "The DHCP server needs a static IP address on the bridge.",
    "dhcp.pool_outside_subnet": "Range {pool} is not entirely inside network {network}.",
    "dhcp.pool_contains_address": "Range {pool} contains the bridge address {address}.",
    "dhcp.pool_contains_reserved": "Range {pool} contains the network or broadcast address.",
    "dhcp.gateway_outside_subnet": "DHCP gateway {gateway} is not in network {network}.",
    "dhcp.dns_router_not_answering": "DHCP hands out the router address {address} as DNS, "
    "but remote DNS requests are off, so the router will not answer clients. Allow them in "
    "Services and hardening, or leave the DNS field empty.",
    "dhcp.no_dns_servers": "The DHCP server has no DNS to hand out: remote DNS requests are "
    "off and neither the hardening block nor the static WAN has a DNS server with an IPv4 "
    "address. Add DNS in Services and hardening, enter it at the DHCP server, or allow remote "
    "DNS requests.",
    # DNS
    "dns.remote_requests_need_filter": "Remote DNS requests (allow-remote-requests) can be "
    "turned on only with the basic filter, without it the router would answer DNS from WAN "
    "too. They stay off and the DHCP server hands out the DNS servers from Services and "
    "hardening.",
    # firewall
    "firewall.fasttrack_shaping_unconfirmed": "FastTrack bypasses simple queues and HTB. "
    "Confirm that you do not use traffic shaping.",
    "firewall.fasttrack_pppoe_no_hw": "FastTrack works with a PPPoE WAN, but without hardware "
    "acceleration.",
    "firewall.ipv6_overrides_hardening": "IPv6 is on in the firewall settings, so "
    "disable-ipv6=yes from the hardening block is left out and IPv6 is not turned off.",
    "firewall.ipv6_management_subnet": "The management subnet is IPv4, so management from WAN "
    "is not opened over IPv6; it stays reachable from LAN.",
    "firewall.ipv6_v6_package": "On v6, IPv6 is the ipv6 package and it is not enabled (or "
    "its state is unknown), so no IPv6 filter is generated. Enable the package (/system "
    "package enable ipv6), reboot the device and apply again.",
    # mode
    "mode.router_without_wan": "Router mode is chosen, but WAN is off.",
    # hardening block parsing
    "hardening.parse.tokenize": "Line {line}: cannot be parsed (unclosed quote or bracket), "
    "passed on unchanged: {text}",
    "hardening.parse.unknown_verb": "Line {line}: unknown command, passed on unchanged: {text}",
    "hardening.parse.bad_argument": "Line {line}: unrecognised parameter, passed on unchanged: "
    "{text}",
    "hardening.parse.no_menu": "Line {line}: command without a menu (no line starting with /): "
    "{text}",
    "hardening.parse.scripting": "Line {line}: a scripting command is passed on unchanged: "
    "{text}",
    # access paths / services
    "access.path_disabled": "Access path {service} will be off after applying.",
    "access.api_ssl_disabled": "api-ssl will be off – after applying the application loses "
    "its main transport.",
    "access.no_paths": "No access path (api-ssl, SSH, Winbox) would remain after applying. "
    "Applying the configuration is blocked.",
    "access.uncertain": "Some changes in /ip service cannot be evaluated unambiguously. "
    "Check the access paths by hand.",
    "access.address_restricted": "Service {service} will be reachable only from {address}.",
    "service.port_invalid": "Invalid port {port} for service {service} (allowed 1–65535).",
    "service.port_collision": "Services {services} use the same port {port}.",
    "service.unknown": "Unknown service {service} in /ip service.",
    "service.find_takes_connections": "Selecting with [find …] in /ip service also takes the "
    "open connections to the service and RouterOS rejects such a change (“this is configured "
    "elsewhere”). On import this always happens for api-ssl, and for other services when "
    "someone is connected to them. Use the name form, e.g. “set {service} …”, or "
    "“[find name={service} dynamic=no]”.",
    # bridge HW offload
    "offload.version_too_old": "Bridge HW offload needs RouterOS 6.41 or newer (the device "
    "has {version}), the option is not offered.",
    "offload.disabled_by_user": "Turned off by the user.",
    "offload.port_sfp": "An SFP/SFP+ port is usually not offloaded.",
    "offload.port_wireless": "A wireless interface is not offloaded.",
    "offload.port_bonding": "Bonding is usually not offloaded.",
    "offload.port_other": "An interface of type {type} is not offloaded.",
    "offload.port_no_switch": "The port is not connected to a switch chip.",
    "offload.chip_unsupported": "MikroTik does not recommend bridge HW offload on chip "
    "{chip_type} (support is still being finished). Keep a software bridge with RSTP.",
    "offload.use_ip_firewall": "use-ip-firewall=yes on the bridge turns HW offload off.",
    "offload.mstp_unsupported": "protocol-mode=mstp turns offload off on chip {chip_type}.",
    "offload.mstp_conflict": "protocol-mode=mstp turns HW offload off on chip {chip_type} "
    "({chip}). Switch to rstp, or turn HW offload off.",
    "offload.vlan_filtering_unsupported": "vlan-filtering=yes turns HW offload off on chip "
    "{chip_type}.",
    "offload.stp_unsupported": "Chip {chip_type} does not offload a bridge with STP/RSTP/MSTP "
    "on this RouterOS version.",
    "offload.stp_no_offload": "Chip {chip_type} ({chip}) with RouterOS {version} does not "
    "offload a bridge with protocol-mode={mode}, the ports will run through the CPU. For HW "
    "offload choose protocol-mode=none.",
    "offload.vlan_filtering_uncertain": "Offload with vlan-filtering on chip {chip_type} is "
    "not certain, verify after applying.",
    "offload.ok": "Offload on chip {chip_type} ({chip}).",
    "offload.ok_unknown_chip": "Offload on chip {chip_type} ({chip}); the chip is not in the "
    "local database, the general rules apply.",
    "offload.forced": "Port {port}: offload forced by the user (otherwise: {original}).",
    "offload.software_bridge": "The bridge joins ports on several switch chips – {chips}. "
    "Traffic between the groups will run in software through the CPU.",
    "offload.mixed_software": "Ports {ports} will run in software; traffic between them and "
    "the offloaded ports goes through the CPU.",
    "offload.no_device_data": "Without a connected device the switch chips are not known – "
    "hw= is not generated and RouterOS keeps its default behaviour.",
    # generator
    "target.unsupported_version": "RouterOS {version} is not supported (supported: 6.40+ and "
    "7.x).",
    "bridge.mstp_unsupported_version": "protocol-mode=mstp is not available on RouterOS "
    "{version}. Use rstp.",
    "bridge.mstp_needs_vlan_filtering": "RouterOS {version} accepts protocol-mode=mstp only "
    "with vlan-filtering on. Turn vlan-filtering on, or use rstp.",
    "fasttrack.hw_offload_skipped": "hw-offload on the FastTrack rule is not generated – the "
    "version or hardware does not support it, or it was not verified on a device.",
    "dns.remote_requests_in_hardening": "The hardening block sets allow-remote-requests="
    "{value}. It runs after the “Allow remote DNS requests” option and so overrides it – "
    "remove the line from the block and use the option.",
    "wan.static.dns_overridden": "The DNS servers from the static WAN are overridden by the "
    "/ip dns settings in the hardening block.",
    "feature.unverified": "Unverified version differences in use (MUST BE VERIFIED): "
    "{features}",
    "logging.robust_unknown_index": "Logging rule with index {index} has no known topics, "
    "the index is kept.",
    # v6 dialect translation
    "v6.ntp_too_many": "RouterOS v6 supports only two NTP servers (primary/secondary); left "
    "out: {dropped}",
    "v6.ntp_hostname_unsupported": "NTP server {server} is a hostname, which this v6 version "
    "does not support; it is left out.",
    "v6.ntp_dropped_line": "A line from /system ntp client servers cannot be translated to "
    "v6, it is left out: {text}",
    "v6.ipv6_package_disable": "On v6, IPv6 is turned off with /system package disable ipv6 "
    "– it takes effect only after a reboot.",
    "v6.ipv6_omitted_missing": "The ipv6 package is not installed or enabled, the IPv6 block "
    "is left out.",
    "v6.ipv6_unknown_package": "The state of the ipv6 package is unknown (offline), the IPv6 "
    "block is left out – check on the device.",
    "v6.ipv6_dropped": "IPv6 is not active on the device, the command is left out: {text}",
    "v6.snmp_ipv6_address": "IPv6 address {address} in an SNMP community is left out on v6.",
    "v6.feature_missing": "RouterOS {version} has no parameter {feature}, the command is left "
    "out: {text}",
    "v6.raw_line_untranslated": "The line cannot be translated to v6 and is passed on "
    "unchanged, check it: {text}",
    "v7.feature_missing": "RouterOS {version} has no parameter {feature}.",
    # apply preflight
    "apply.blocked_by_errors": "The configuration contains errors, applying it to the device "
    "is blocked.",
    "apply.not_connected": "The application is not connected to a device.",
    "apply.dialect_mismatch_unconfirmed": "The script is generated for RouterOS {target}, but "
    "the device has {device}. Applying has to be confirmed explicitly.",
    "apply.dialect_mismatch": "The script for RouterOS {target} is applied to a device with "
    "{device} (confirmed by the user).",
    "apply.target_version_differs": "The script is generated for {target}, the device has "
    "{device}.",
    "apply.reset_identity_mismatch": "For the reset, type the device identity exactly: "
    "{identity}",
    "apply.reset_password_required": "After the reset admin would have no password – enter a "
    "new password.",
    "apply.reset_keep_users": "The reset keeps users and their passwords (keep-users), the "
    "application then logs in with the same account. The admin password changes only if you "
    "enter one.",
    "apply.rollback_ignored_reset": "The rollback safeguard is not used with a reset (a reset "
    "deletes the scheduled job too).",
    "apply.rollback_too_short": "The rollback safeguard needs at least {minimum} minutes.",
    "apply.lockout_wan": "The application is connected to the device through port {port}, "
    "which becomes WAN. The basic filter without management from WAN drops new connections "
    "there, so after applying it will not reconnect through this port. Turn on “Allow "
    "management from WAN” or connect from LAN.",
    "apply.lockout_wan_ipv6": "The application is connected to the device through port "
    "{port}, which becomes WAN. It reconnects over IPv4, but the IPv6 filter without "
    "management from WAN drops new connections there, the link-local address used to connect "
    "by MAC included. Turn on “Allow management from WAN”, connect from LAN or over IPv4.",
    "apply.session_by_mac_on_wan_pppoe": "The application is connected to the device by MAC "
    "on port {port}, which will run PPPoE. After applying it comes back the same way, through "
    "an address lent on this port; management from WAN stays reachable ({paths}).",
    "apply.lockout_wan_pppoe": "The application is connected to the device through port "
    "{port}, which will run PPPoE. The port itself will have no IP address, so after applying "
    "it will not reconnect through it. Connect from LAN.",
    "apply.session_by_mac_on_wan_vlan": "The application is connected to the device by MAC on "
    "port {port}, where WAN will run in VLAN {vlan}. After applying it comes back the same "
    "way, through an address lent on this port (the VLAN takes only tagged traffic, the "
    "untagged address is not affected); management from WAN stays reachable ({paths}).",
    "apply.lockout_wan_vlan": "The application is connected to the device through port "
    "{port}, where WAN will run in VLAN {vlan} (interface {interface}). The port itself will "
    "have no IP address, so after applying it will not reconnect through it. Connect from "
    "LAN.",
    "apply.session_on_wan": "The application is connected to the device through port {port}, "
    "which becomes WAN. Management from WAN stays reachable ({paths}).",
    "apply.lockout_unconfigured_port": "The application is connected through port {port}, "
    "which after the reset will have neither an IP address nor be in the bridge.",
    "apply.adopted": "The device already has {count} items the script adds ({items}). "
    "Instead of adding them the script modifies them (set) and leaves out items without "
    "further parameters.",
    "apply.import_wan_port_bridged": "Port {port}, which is to be WAN, is in bridge {bridge} "
    "on the device. The import does not take it out of the bridge, so the WAN address on it "
    "will not work and the connection through it will be lost. Change the mode bridge → "
    "router with a reset (Reset + apply).",
    "apply.import_router_to_bridge": "The device has a router configuration (WAN: {ports}), "
    "the profile has no WAN. The import does not remove it: the WAN list and the original "
    "bridge with its address and DHCP server stay. Better change the mode router → bridge "
    "with a reset.",
    "apply.session_port_to_bridge": "The application is connected through port {port}, which "
    "the import adds to bridge {bridge}. The address on the port stops working then, and the "
    "script moves it to the bridge ({address}) only one step later, so the connection drops "
    "for a moment.",
    "apply.session_port_to_bridge_no_address": "The application is connected through port "
    "{port}, which the import adds to bridge {bridge}. The address on the port stops working "
    "then and the profile gives the bridge no fixed address; after the import connect to the "
    "device by MAC.",
    "apply.route_ambiguous": "The device has several static routes to {dst} (gateways "
    "{gateways}). The script changes none of them and adds another through {gateway}; check "
    "them, otherwise traffic may go through several gateways at once.",
    "apply.own_rules_removed": "The profile no longer adds rules to {table}; the script "
    "removes the rules of an earlier QuickSet run from it (comment “quickset: …”, count "
    "{count}).",
    "apply.bridge_port_moved": "Port {port} is now in bridge {old}; the script moves it to "
    "bridge {new}. If a connection goes through it, it drops for a moment.",
    "apply.vlan_filtering_off": "Bridge {bridge} has vlan-filtering on on the device, the "
    "profile does not; the script turns it off.",
    "apply.notice_ssh_port": "After applying SSH will run on port {port}.",
    "apply.notice_services_disabled": "After applying these services will be off: "
    "{services}.",
    "apply.notice_api_ssl_kept": "api-ssl ({port}) stays on – it is the application's main "
    "transport.",
    "apply.notice_rest_api": "The REST API will not run after applying, because it needs "
    "www-ssl.",
    "apply.notice_remaining_paths": "Remaining access paths ({count}): {paths}.",
    # post-apply verification (checklist)
    "verify.identity": "Identity: expected {expected}, on the device {actual}",
    "verify.services": "/ip service matches (differences: {services})",
    "verify.bridge": "Bridge {name}: protocol-mode expected {expected}, on the device {actual}",
    "verify.lan_address": "Address {address} on {bridge}",
    "verify.default_route": "Default route through {expected} (static default routes on the "
    "device: {actual})",
    "verify.wan_vlan": "Interface {name}: VLAN {vlan} on port {port}",
    "verify.dhcp_client": "DHCP client on {interface} (state: {status})",
    "verify.ipv6": "IPv6 filter: {expected} rules at the top of the table (on the device: "
    "{actual}, disable-ipv6: {disabled})",
    "verify.hw_offload": "HW offload matches the request (differences: {count})",
    "verify.offload_not_active": "Port {port}: hw=yes is set, but offload is not running on "
    "the device. Reason according to detection: {reason}",
    "verify.offload_port_missing": "Port {port} is not in the bridge on the device.",
    "verify.offload_unexpected": "Port {port}: offload is running although it was not "
    "requested.",
    "verify.offload_unreadable": "The actual HW offload state cannot be read: {error}",
    # apply steps (progress log)
    "apply.step.backup": "Configuration backup and export ({name})",
    "apply.step.download": "Downloading the backup to {dir}",
    "apply.step.upload": "Uploading script {file} (method: {strategy})",
    "apply.step.rollback": "Rollback safeguard scheduled at {at} (device time)",
    "apply.step.execute_import": "Importing script {file}",
    "apply.step.execute_lines": "Applying commands line by line ({count})",
    "apply.step.execute_reset": "Configuration reset, {file} runs after the reboot",
    "apply.reset_retry": "The device has not accepted the uploaded script yet, trying again "
    "({attempt}/{total}).",
    "apply.step.reconnect": "Connecting after applying to {hosts} (api-ssl {api_port}, "
    "SSH {ssh_port})",
    "apply.step.verify": "Verifying the configuration on the device",
    "apply.step.cleanup": "Removing the uploaded script from the device",
    "apply.step.prune": "Deleting old backups from the device: {count} files (the latest "
    "{keep} kept)",
    "apply.step.confirm": "Configuration confirmed, rollback safeguard cancelled",
    "apply.connection_lost": "The connection was lost during step {step}, trying to "
    "reconnect.",
    "apply.rollback_pending": "The rollback safeguard restores the backup at {at} unless you "
    "confirm the configuration.",
    "apply.cancelled": "Applying aborted by the user, the device configuration has not "
    "changed.",
    "apply.failed.backup": "The backup could not be created, nothing changed on the device: "
    "{error}",
    "apply.failed.upload": "The script could not be uploaded to the device: {error}",
    "apply.failed.upload_reset_needs_file": "A reset needs an uploaded file, but no transfer "
    "method worked.",
    "apply.failed.rollback": "The rollback safeguard could not be scheduled, nothing changed: "
    "{error}",
    "apply.failed.execute": "Applying the script failed: {error}",
    "apply.failed.reconnect": "The connection could not be restored ({attempts} attempts, "
    "api-ssl {api_port}, SSH {ssh_port}): {error}",
    "apply.failed.verify": "Verifying the configuration failed: {error}",
    "apply.failed.confirm": "The rollback safeguard could not be cancelled: {error}",
    "apply.warning.download": "File {file} could not be downloaded (it stays on the device): "
    "{error}",
    "apply.warning.cleanup": "The uploaded script could not be deleted: {error}",
    "apply.warning.prune": "Old backups could not be deleted from the device: {error}",
    # RouterOS update from the Update directory
    "update.planned": "RouterOS {version} is installed after the reset (the device has "
    "{current}): {files}. The script is generated for {version} and without the features the "
    "application verified by trying them on {current} (hw-offload on FastTrack).",
    "update.no_dir": "The update folder {dir} does not exist, RouterOS will not be updated.",
    "update.no_space": "There is not enough space on the device for RouterOS {version}: the "
    "packages take {size} MB, {free} MB is free. RouterOS will not be updated – free some "
    "space in Files, or use Netinstall.",
    "update.no_ram": "There is not enough free memory on the device for RouterOS {version}: "
    "the packages take {size} MB, {free} MB of memory is free. The device uploads packages to "
    "RAM (its files are in flash/), RouterOS will not be updated – try again after a reboot.",
    "update.unknown_arch": "The application does not know the device architecture “{arch}”, "
    "RouterOS will not be updated.",
    "update.no_package": "Folder {dir} has no RouterOS package for architecture {arch}, "
    "RouterOS will not be updated.",
    "update.up_to_date": "The device already has RouterOS {current}, the newest package in "
    "the Update folder is {version}. No update is needed.",
    "update.other_major": "The RouterOS {version} package is for another major version than "
    "the device has ({current}). The application does not move between v6 and v7 here, "
    "RouterOS will not be updated.",
    "update.extra_missing": "The device has package {name} {installed}, but the Update folder "
    "lacks version {version} ({arch}) of it. It may not work after the update.",
    "update.unreadable": "File {file} in the Update folder cannot be used: {error}",
    "apply.step.update_upload": "Uploading update {file} ({size} MB, method: {strategy})",
    "apply.step.update_withdrawn": "Uploaded update deleted from the device: {files}",
    "apply.failed.update_upload": "Update {file} could not be uploaded, the device "
    "configuration has not changed: {error}",
    "apply.warning.update_left": "Update {files} stayed on the device and will be installed "
    "on the next reboot. Delete it in Files: {error}",
    "apply.warning.update_leftover": "Package {files} stayed on the device after the reboot "
    "(it was not installed), the application deleted it.",
    "update.routerboot": "After applying RouterBOOT is updated from {current} to {version} "
    "and the device reboots once more.",
    "update.routerboot_after": "After the RouterOS update RouterBOOT (now {current}) is "
    "updated to {version} too and the device reboots once more.",
    "apply.step.routerboot": "RouterBOOT update {current} → {version} and reboot",
    "apply.step.routerboot_downgrade": "Lowering RouterBOOT {current} → {version} and reboot",
    "apply.step.routerboot_current": "RouterBOOT {current} stays, the device offers no newer "
    "firmware",
    "apply.warning.routerboot": "RouterBOOT could not be updated, the configuration stays: "
    "{error}",
    # Move to another RouterOS version (downgrade, v6 <-> v7)
    "version.routerboot_pending": "The device has RouterBOOT {version} ready (running now: "
    "{current}). It is written and the device reboots once; the configuration does not "
    "change.",
    "version.routerboot_lower": "The device has an older RouterBOOT {version} ready (running "
    "now: {current}). It is written and the device reboots once; the configuration does not "
    "change. An older RouterOS runs under a newer RouterBOOT too, so lowering it is not "
    "necessary.",
    "version.routerboot_none": "RouterBOOT {current} is the latest the device offers – there "
    "is nothing to write.",
    "version.routerboot_stays": "RouterBOOT {current} is newer than RouterOS {version} and "
    "stays; it can be lowered only explicitly with “Lower RouterBOOT”. An older RouterOS "
    "runs under a newer RouterBOOT without trouble.",
    "version.routerboot_after": "RouterOS {version} brings a newer RouterBOOT too (now "
    "{current}). With the step “Update RouterBOOT” ticked it is written after the last "
    "reboot and the device reboots once more; without it RouterBOOT stays old.",
    "version.routerboot_before_v7": "Before the move to RouterOS {version} RouterBOOT {via} "
    "is written (now {current}), as MikroTik recommends: RouterOS 7 is installed only on a "
    "board with the newest v6 firmware. The device reboots once more for it; the "
    "configuration does not change.",
    "version.planned": "Move from RouterOS {current} to {version}: {reboots}x reboot, "
    "uploading {files}.",
    "version.no_dir": "The package folder {dir} does not exist, cannot move to another "
    "version.",
    "version.unknown_arch": "The application does not know the device architecture "
    "“{arch}”, cannot move to another version.",
    "version.no_package": "Folder {dir} has no RouterOS {version} package for architecture "
    "{arch}. It belongs in {where}.",
    "version.same": "The device already has RouterOS {version}.",
    "version.below_factory": "RouterOS {version} is older than the version the board was "
    "made with ({floor}). This device cannot go below {floor}.",
    "version.no_factory_firmware": "The device does not state which version it was made with "
    "(neither minimum-firmware nor minimum-version, up to RouterOS 7.23 factory-firmware and "
    "factory-software), so the application does not know the lowest possible version. The "
    "device may refuse a downgrade to {version}.",
    "version.v7_needs_649": "RouterOS 7 can be reached only from {gate}, the device has "
    "{current}. But folder {dir} has no v6 package of version {gate} or newer to make the "
    "move in two steps (it belongs in {dir}\\<version>\\<architecture>).",
    "version.via_last_v6": "RouterOS {version} is reached through the highest RouterOS 6 in "
    "the Update folder: first {current} → {via}, only then from {via} to {version}.",
    "version.crosses_major": "A move between RouterOS 6 and 7 (from {current} to {version}): "
    "the RouterOS major version changes, check the settings after the move.",
    "version.v7_config_converted": "From RouterOS 6 to {version} RouterOS converts the "
    "configuration by itself during the reboot, so addresses and services stay and the "
    "device comes back in the same place. What RouterOS 7 does not know does not carry over, "
    "though – go through the settings, or set the device up again (Reset + apply).",
    "version.v6_config_lost": "From RouterOS 7 back to {version} the configuration is not "
    "converted – what you set on v7 may not be kept. After the reboot the device may not come "
    "back on the same address – the application tries to find it by MAC too, otherwise "
    "Netinstall remains.",
    "version.extras_dropped": "Packages {names} will not be in RouterOS {version}: they do "
    "not carry over across a major version, install them on the device again after the move.",
    "version.extra_missing": "The device has package {name} {installed}, but the Update "
    "folder lacks version {version} ({arch}) of it. It may not work after the move.",
    "version.no_space": "There is not enough space on the device for RouterOS {version}: the "
    "packages take {size} MB, {free} MB is free. Free some space in Files, or use "
    "Netinstall.",
    "version.no_ram": "There is not enough free memory on the device for RouterOS {version}: "
    "the packages take {size} MB, {free} MB of memory is free. The device uploads packages to "
    "RAM (its files are in flash/) – try again after a reboot.",
    "version.unreadable": "File {file} in the package folder cannot be used: {error}",
    # checks after the reboot: package versions and wireless interfaces
    "verify.package_versions": "All packages on the device have version {expected} and "
    "nothing waits for a reboot (other version: {wrong}; waiting: {waiting})",
    "verify.radios": "Wireless interfaces stayed ({before}) – missing menus: {menus_lost}; "
    "missing interfaces: {lost}; not running: {stopped}; new: {new}",
    "verify.registrations": "The registration table filled again on {radios} "
    "(empty: {empty}, waited {waited} s)",
    "apply.step.radio_check": "Checking wireless interfaces after the reboot",
    "apply.step.radios_wait": "Waiting for the radios to come up ({stopped}) – at most "
    "{timeout} s (a DFS channel listens for radar first)",
    "apply.step.registrations_wait": "Waiting for stations to return: {returned} of "
    "{expected} interfaces",
    "apply.failed.version_radio_lost": "After installing RouterOS {version} a wireless "
    "interface the device had before is missing ({detail}). The batch stops – no further "
    "devices are updated. Check the device before going on.",
    "apply.warning.version_registrations": "Stations did not return to {empty} within "
    "{waited} s. The interface exists and runs, but nobody is on it – check the other side.",
    "apply.warning.version_package_versions": "After installing RouterOS {version} not all "
    "packages have the same version (other version: {wrong}; waiting for a reboot: "
    "{waiting}). The wireless interfaces stayed, so the link holds – but install the package "
    "in the right version before the device reboots next time.",
    # batch update
    "fleet.not_confirmed": "{count} devices are left out because the application has not "
    "logged in to them ({names}). Only a device whose state was read directly from it can be "
    "updated – packages are not chosen by a guess.",
    "fleet.order_uncertain": "The update order is not certain: the topology has {roots} "
    "roots, {orphans} unlinked devices and {cycles} loops. Go through the order and confirm "
    "it by hand – a device must not be updated before the ones that go through it.",
    "fleet.stopped": "The batch stopped at device {name}: {error}. The remaining {rest} "
    "devices were not updated. Check what happened before going on.",
    "fleet.blocked": "The batch will not start: {count} devices have no usable plan "
    "({names}). It is usually the same missing package for all of them – finding that out "
    "after four reboots helps nobody.",
    "fleet.stale": "Device {name} is elsewhere than the inventory says: the plan counted on "
    "RouterOS {planned} and path {route}, the device now reports {current} and path {fresh}. "
    "Nothing changed on it – log in to it again and check the batch.",
    "fleet.no_address": "Device {name} has no address to connect through.",
    "fleet.check_failed": "A check failed after the move: {detail}",
    # downloading packages from download.mikrotik.com
    "download.not_published": "MikroTik does not publish package {name} version {version} for "
    "this architecture ({url}). Check that the version exists for this device.",
    "download.failed": "Package {name} {version} could not be downloaded from {url}: {error}",
    "download.not_a_package": "The downloaded file {file} is not a RouterOS package "
    "({error}). Something else came from {url} – typically an error page. The file was "
    "deleted.",
    "download.wrong_package": "The downloaded file {file} is {name} {version} ({arch}), but "
    "{want_name} {want_version} ({want_arch}) is needed. The file was deleted and will not be "
    "uploaded – uploading a package for another version or architecture is exactly what "
    "makes interfaces disappear.",
    # wireless: driver packages, the 7.12 stop and the interface check after the update
    "update.no_snapshot": "The application has not read the device's list of wireless "
    "interfaces, so it does not know whether to upload a driver package with RouterOS. An "
    "empty list does not mean “the device has no radio” – it means “nobody looked”. Connect "
    "to the device again.",
    "version.no_snapshot": "The application has not read the device's list of wireless "
    "interfaces, so it does not know which drivers to upload. An empty list does not mean "
    "“the device has no radio” – it means “nobody looked”, and an update based on it would "
    "delete the antenna's interface. Connect to the device again.",
    "version.needs_712": "RouterOS {current} cannot jump to {version}: wireless packages are "
    "converted on 7.12 and without that stop the interfaces disappear. But neither the folder "
    "nor the cache has any 7.12.x version to make the stop with.",
    "version.route_too_long": "The route from RouterOS {current} to {version} would have more "
    "than {stops} stops – that is a bug in the application's rules, not a property of the "
    "device. Move to the target version in smaller steps.",
    "version.driver_unknown": "The device answers on menu {menus}, but does not list the "
    "package that serves it – so the application does not know which driver to upload. An "
    "update would delete the interface, so the application refuses it. Send the output of "
    "/system package print.",
    "version.driver_no_v6": "The radios of this device run on driver {drivers}, which does "
    "not exist in RouterOS 6 – after the move to {version} the device would have no wireless "
    "interface at all. The application refuses the move to v6.",
    "version.driver_switch": "Driver {chosen} would replace {current} on this device. "
    "Changing the driver deletes the interface configuration, so the application does not do "
    "it. The driver choice only serves to split wifiwave2 on 7.13, not to switch from legacy "
    "wireless.",
    "version.wifi_choice_needed": "Device {model} runs wifiwave2, which from 7.13 splits into "
    "{options} – and the device did not say which of them belongs on the board. Choose it by "
    "hand (--wifi-package), the application does not guess: a wrong choice deletes the "
    "interface configuration.",
    "version.wifi_choice_invalid": "Driver {chosen} cannot be used here, the choice is "
    "{options}.",
    "version.driver_missing": "The device runs a wireless driver that needs package {name} "
    "in RouterOS {version} – and it is nowhere for architecture {arch} (neither in the "
    "Update folder nor in the cache). Without it the device would have no wireless interface "
    "after the reboot and the link would not come back, so the application refuses the "
    "update.",
    "version.extra_missing_blocking": "The device has package {name} {installed}, but "
    "version {version} for architecture {arch} is nowhere. The update would remove it; if "
    "you do not need it on this device, allow that explicitly (--allow-missing-package "
    "{name}).",
    "version.extra_missing_allowed": "Package {name} {installed} will not be in RouterOS "
    "{version} – you allowed that explicitly. Install it after the move if needed.",
    "apply.step.version_upload": "Uploading package {file} ({size} MB, method: {strategy})",
    "apply.step.version_install_reboot": "Installing RouterOS {version} by reboot",
    "apply.step.version_install_downgrade": "Downgrade to RouterOS {version} (the device has "
    "{current}) and reboot",
    "apply.step.version_installed": "The device has RouterOS {version}",
    "apply.step.version_withdrawn": "Uploaded package deleted from the device: {files}",
    "apply.failed.version_upload": "Package {file} could not be uploaded, nothing changed on "
    "the device: {error}",
    "apply.failed.version_missing_file": "Package {files} is not on the device even though "
    "the upload finished without an error. The installation does not start: the downgrade "
    "command reboots the device even when it has nothing to install.",
    "apply.failed.version_install": "The RouterOS installation could not be started: {error}",
    "apply.failed.version_not_installed": "The device came back with RouterOS {actual}, "
    "{expected} was expected. The package was not installed and the application deleted it "
    "from the device.",
    "apply.failed.version_routerboot": "RouterBOOT could not be written before the move to "
    "RouterOS {version} ({detail}). The move stopped, RouterOS 7 was not installed and the "
    "device keeps running RouterOS 6.",
    "apply.failed.version_reconnect": "After the reboot the connection could not be restored "
    "({attempts} attempts, api-ssl {api_port}, SSH {ssh_port}): {error}",
    "apply.warning.version_leftover": "Package {files} stayed on the device after the reboot "
    "(it was not installed), the application deleted it.",
    "apply.warning.version_left": "Package {files} stayed on the device and will be installed "
    "on the next reboot. Delete it in Files: {error}",
    "verify.routeros_version": "RouterOS {expected} (on the device {actual})",
    "verify.routerboot": "RouterBOOT {expected} (on the device {actual})",
    "verify.reset_script": "The script after the reset ran to the end (error: {error})",
    "verify.import_script": "The imported script ran to the end (error: {error})",
    # UI input syntax
    "ui.syntax": "{message}",
    # profiles
    "profile.invalid_json": "The file is not valid JSON.",
    "profile.wrong_format": "The file is not a MikroTik QuickSet Plus profile.",
    "profile.unsupported_version": "The profile has an unsupported format version.",
    "profile.invalid": "The profile contains invalid values.",
    "profile.unreadable": "The profile file cannot be read.",
    "profile.name_required": "Enter a profile name.",
    "profile.not_found": "The profile does not exist.",
}


# One apply step, keyed by StepStatus value: the window and the CLI label them alike.
STEP_PREFIX: dict[str, str] = {
    "running": "…", "ok": "OK", "warning": "!", "failed": "ERROR", "skipped": "–",
}


# Device notes in data/known_devices.yaml are kept in Czech; these phrases turn them into
# English for display. Longer phrases first: they contain shorter ones.
NOTE_PHRASES: tuple[tuple[str, str], ...] = (
    ("MSTP ani vlan-filtering se na tomto čipu neoffloadují.",
     "Neither MSTP nor vlan-filtering is offloaded on this chip."),
    ("MSTP ani vlan-filtering se na těchto čipech neoffloadují.",
     "Neither MSTP nor vlan-filtering is offloaded on these chips."),
    ("MSTP ani vlan-filtering se na QCA8337 neoffloadují.",
     "Neither MSTP nor vlan-filtering is offloaded on QCA8337."),
    ("MSTP ani vlan-filtering se neoffloadují.",
     "Neither MSTP nor vlan-filtering is offloaded."),
    ("MSTP i vlan-filtering se offloadují.", "Both MSTP and vlan-filtering are offloaded."),
    ("MSTP se offloaduje, vlan-filtering až od RouterOS 7.1.",
     "MSTP is offloaded, vlan-filtering only from RouterOS 7.1."),
    ("Vlan-filtering se offloaduje až od RouterOS 7.1.",
     "Vlan-filtering is offloaded only from RouterOS 7.1."),
    ("MikroTik doporučuje softwarový bridge s RSTP.",
     "MikroTik recommends a software bridge with RSTP."),
    ("SFP+ port není na switch čipu, neoffloaduje se.",
     "The SFP+ port is not on the switch chip and is not offloaded."),
    ("HW offload bridge se zde nenabízí.", "bridge HW offload is not offered here."),
    ("provoz mezi skupinami jde přes CPU.", "traffic between the groups goes through the CPU."),
    ("mezi skupinami jde provoz přes CPU.", "traffic between the groups goes through the CPU."),
    ("ether1 a sfp1 nejsou na switch čipu.", "ether1 and sfp1 are not on the switch chip."),
    ("ether1 je přímo na CPU.", "ether1 is connected directly to the CPU."),
    ("ostatní porty jsou přímo na CPU.", "the other ports are connected directly to the CPU."),
    ("starší kusy", "older units"),
    ("(novější revize)", "(newer revision)"),
    ("Dva switch čipy", "Two switch chips"),
    ("Dva čipy", "Two chips"),
    ("Tři čipy", "Three chips"),
    (" a ether", " and ether"),
    (") a Atheros", ") and Atheros"),
)
