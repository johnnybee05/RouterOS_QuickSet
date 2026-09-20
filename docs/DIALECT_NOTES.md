# RouterOS dialect notes (v6 vs v7)

Every version-dependent behaviour the generator relies on is listed here. The code
counterpart is the table in `src/mqs/generator/features.py`; both must be changed together.

Legend:
- **OVĚŘENO** — confirmed on a real device (record version + board + date).
- **NUTNO OVĚŘIT** — taken from the spec or from memory, not yet confirmed. The generator
  surfaces these as warnings in the preview.

## 1. Differences table

| Feature key | Area | RouterOS v7 | RouterOS v6 | Status |
|---|---|---|---|---|
| `ntp_servers_menu` | NTP client | `/system ntp client set enabled=yes` + `/system ntp client servers add address=X` | no `servers` menu → `/system ntp client set enabled=yes primary-ntp=X secondary-ntp=Y`; newer v6 also `server-dns-names=` | NUTNO OVĚŘIT (min v6 version for `server-dns-names`) |
| `ipv6_disable_setting` | IPv6 off | `/ipv6 settings set disable-ipv6=yes` | `/ipv6` menu exists only with ipv6 package enabled. Suspicion: `disable-ipv6` does not exist on v6 at all → decision Q3: `/system package disable ipv6` (reboot needed) or omit | NUTNO OVĚŘIT (v6 property; first v7 version with `disable-ipv6`) |
| `snmp_any_address` | SNMP community | `addresses=::/0` | `addresses=0.0.0.0/0` | NUTNO OVĚŘIT (whether `::/0` covers IPv4 on v7 with IPv6 disabled) |
| `bridge_mstp` | Bridge protocol-mode | none/stp/rstp/mstp | mstp only from "newer 6.4x" | NUTNO OVĚŘIT (exact first v6 version) |
| `interface_list` | `/interface list` | yes | 6.41+; older → `out-interface=<port>` / `in-interface=<bridge>` | NUTNO OVĚŘIT |
| `ip_service_ssl` | `/ip service` | includes api-ssl, www-ssl | spec: "basic set only" | NUTNO OVĚŘIT (api-ssl exists on 6.40+?) |
| `fasttrack_hw_offload` | FastTrack rule | optional `hw-offload=yes`, only if the parameter exists at runtime | parameter does not exist — never emit | NUTNO OVĚŘIT (runtime probe method) |
| `bridge_port_hw` | Bridge HW offload | `/interface bridge port ... hw=yes`, actual state in `print detail` (`hw-offload`) | `hw=` 6.41+; older uses `master-port` → offload not offered | NUTNO OVĚŘIT |
| `logging_indexes` | Logging | `/system logging set 0 action=disk` | same; numeric indexes are fragile | NUTNO OVĚŘIT (robust `find` syntax) |
| `neighbor_discovery_interface_list` | Neighbor discovery | `/ip neighbor discovery-settings set discover-interface-list=!dynamic` | assumed 6.41+; older → command dropped with warning | NUTNO OVĚŘIT |
| `bridge_frame_types` | Bridge VLAN | `frame-types=` on `/interface bridge` | assumed 6.43+ | NUTNO OVĚŘIT |
| `ntp_server_dns_names` | NTP hostnames | n/a (servers menu accepts hostnames) | `server-dns-names=` assumed 6.45+; older → hostname dropped with warning | NUTNO OVĚŘIT |
| `api_plain_login` | API login | plain | plain from 6.43, token before | NUTNO OVĚŘIT |
| `mac_telnet_ec_srp5` | MAC-Telnet auth | EC-SRP5 | EC-SRP5 from 6.43, MD5 before | NUTNO OVĚŘIT |

### Generator conventions that depend on RouterOS syntax (single place each)

| Topic | Where | Current choice | Status |
|---|---|---|---|
| Bridge `priority` value | `blocks/bridge.py` | hex like `/export`: `priority=0x1000` | NUTNO OVĚŘIT |
| Time values | `blocks/common.py:format_duration` | `30m`, `1d1h1m1s` | NUTNO OVĚŘIT |
| Quoting/escaping | `render.py:quote` | bare when `[A-Za-z0-9_.:/,@!+*-]`, otherwise `"..."` with `\"`, `\\`, `\$`, `\?` | NUTNO OVĚŘIT |
| Robust logging | `blocks/system.py:LOGGING_INDEX_TOPICS` | index 0..3 → `set [ find topics=info|error|warning|critical ] action=disk` | NUTNO OVĚŘIT (default order and `find topics=` match on a list property) |
| v6 IPv6 disable | `ros6.py` | package enabled → `/system package disable ipv6` (reboot); missing/disabled → omitted | decision Q3; NUTNO OVĚŘIT |
| DHCP client / server `disabled=no` | `blocks/addressing.py` | always explicit (v6 adds disabled entries by default) | NUTNO OVĚŘIT |
| Router as DNS | `blocks/system.py` | DHCP server hands out the router → `/ip dns set allow-remote-requests=yes` (needed for LAN clients; not in spec) | design decision |
| PPPoE in WAN list | `blocks/addressing.py` | `pppoe-out1` added to WAN list right after the client is created (section 3), the physical port in section 2 | design decision |

### Switch chip database (`src/mqs/data/switch_chips.yaml`)

Values come from MikroTik's documentation read on 2026-09-15, **not** from devices, so every
row is NUTNO OVĚŘIT. Sources: *Bridging and Switching → Bridge Hardware Offloading* (the +/-
table of STP/RSTP, MSTP and VLAN Filtering per chip), *Switch Chip Features*, *Marvell
Prestera switch chip features*, *CRS1xx and 2xx series switches*.

| Topic | Where | Current choice | Status |
|---|---|---|---|
| Chip `type` strings | `capabilities/chips.py:_normalize` | The documentation spells the same chip both ways — `Atheros-8327` in the `/interface ethernet switch print` sample, `Atheros8327` in the feature table. Lookup ignores case and punctuation, so either matches one entry | NUTNO OVĚŘIT (the real strings a device reports) |
| MSTP with HW offload | `data/switch_chips.yaml` | Offload table: `+` for Prestera, 88E6393X/88E6191X/88E6190, MT7621/MT7531/EN7523, RTL8367; `-` for QCA8337, Atheros83xx/82xx/7240, CRS1xx/2xx, IPQ-PPE, ICPlus | NUTNO OVĚŘIT |
| HW vlan-filtering version | `data/switch_chips.yaml` | Added in RouterOS 7.1 for RTL8367, MT7621, MT7531, EN7523 → v6 `false`, v7 `true`; the same for 88E619x/88E6393X, which are documented for v7 only. Prestera is `true` for both | NUTNO OVĚŘIT (behaviour on 7.0.x) |
| IPQ-PPE offload | `data/switch_chips.yaml`, `capabilities/offload.py` | Footnote 9: HW offloaded bridge support is still work in progress and MikroTik recommends the non-offloaded bridge with RSTP → `hw_offload: false`, offload is not offered (the user can still force it) | NUTNO OVĚŘIT |
| Unlisted Prestera parts | `data/switch_chips.yaml` families | `Marvell-98DX*` and `Marvell-98CX*` that are not listed fall back to Prestera capabilities | NUTNO OVĚŘIT |
| 98PX1012 | `data/switch_chips.yaml` | Switch Chip Features gives it `no` for port switching, mirroring, host and VLAN tables, and it is absent from the offload table → no bridge HW offload | NUTNO OVĚŘIT |
| Known device notes | `data/known_devices.yaml`, `capabilities/chips.py:device_notes` | Boards and their chips come from the RouterBoard-to-chip table of *Switch Chip Features* plus the model tables of the Prestera and CRS1xx/2xx manuals. Keyed and matched on `model` from `/system routerboard print`, with `board-name` only as a fallback, by longest normalised prefix: reported names carry suffixes the documentation omits (`RB4011iGS+5HacQ2HnD`), while `RB750G`, `RB750Gr2` and `RB750Gr3` are three different chips | **OVĚŘENO 2026-09-16** that `board-name` is unusable for this: a hEX S reports `board-name=hEX S` for both the MT7621 RB760iGS and the EN7523 E60iUGS |

## 2. Transport and apply notes

| Topic | Note | Status |
|---|---|---|
| API-SSL anonymous DH | `certificate=none` → ADH. Python/OpenSSL 3 needs `set_ciphers("ADH:@SECLEVEL=0")`, no hostname check, `CERT_NONE`. ADH does not exist in TLS 1.3 → cap max version to TLS 1.2 | NUTNO OVĚŘIT |
| API login | plain login from 6.43; legacy token (challenge/MD5) before | NUTNO OVĚŘIT |
| SSH username suffix | `user+cet` disables colors and terminal detection | from spec (user experience) |
| `/import` via API | reply format and error reporting per version | NUTNO OVĚŘIT |
| Files on flash | devices with a `flash/` directory lose files in the RAM root on reboot → upload to `flash/<name>` | NUTNO OVĚŘIT |
| run-after-reset | interfaces may not be ready when the script starts → configurable `:delay` | NUTNO OVĚŘIT (default value) |
| `backup load` in scheduler | non-interactive syntax per version | NUTNO OVĚŘIT |
| Default `/ip service` after `no-defaults` reset | which services are enabled (api-ssl? www-ssl without cert?) | NUTNO OVĚŘIT |
| MNDP TLV type codes | UDP 5678; header 2 B + sequence 2 B (BE); TLV type/length 2 B each (BE); codes 1 MAC, 5 identity, 7 version, 8 platform, 10 uptime, 11 software-id, 12 board, 14 unpack, 15 IPv6, 16 interface, 17 IPv4 — table in `transport/mndp.py:TlvType` | NUTNO OVĚŘIT (compare with a Wireshark capture) |
| MNDP uptime encoding | 4 bytes little endian seconds | NUTNO OVĚŘIT |
| MNDP solicit | 4 zero bytes sent to the subnet broadcast and 255.255.255.255:5678 make devices announce themselves | NUTNO OVĚŘIT |
| MAC-Telnet | UDP 20561; MD5 auth < 6.43, EC-SRP5 6.43+; requires `/tool mac-server` allowed on the interface | NUTNO OVĚŘIT |
| MAC-Telnet EC-SRP5 | curve/protocol in `transport/mactelnet/` ported from MarginResearch/mikrotik_authentication (Apache-2.0, see THIRD_PARTY_NOTICES.md). Curve = Curve25519 in short Weierstrass form; public values are the Montgomery x + y parity; client adds `redp1(vx,1)` back to the server point; `Cc = SHA256(SHA256(Wc|Ws) | z.x)`. Tests prove client↔server self-consistency only | NUTNO OVĚŘIT — never run against a real device |
| MAC-Telnet packet layout | header `>BB6s6sHHI` = version, type, src_mac, dst_mac, session_id, client_type(21), byte_counter; control packet magic `56 34 12 ff`, type codes 0 begin-auth, 1 encryption-key, 2 password, 3 username, 4-6 terminal type/width/height, 9 end-auth; auth-failure arrives as terminal data, not a control packet | NUTNO OVĚŘIT |
| MAC-Telnet bootstrap | on success we add a temporary `/ip address ... comment=mqs-temp` on the interface the device announced from, then connect over IPv4; MD5 auth for < 6.43 is NOT implemented (EC-SRP5 only) | NUTNO OVĚŘIT (also: the `interface=` name for the announcing port) |
| IPv6 link-local connect | `fe80::…%<ifindex>` on Windows; only works when IPv6 is up on the device (usually not on v6) | NUTNO OVĚŘIT |
| Identity charset | conservative `[A-Za-z0-9._-]`, max 64 | NUTNO OVĚŘIT (real RouterOS rules) |
| Verified TLS strictness | Python 3.13 strict X.509 checks are disabled (`VERIFY_X509_STRICT` off) because device-generated certificates may lack e.g. Authority Key Identifier; chain is still verified | NUTNO OVĚŘIT |
| SSH output parsing | list menus: `print terse` (+ `detail` for bridge ports), singleton menus: `print` with `key: value`; singleton list in `transport/ssh.py:SINGLETON_MENUS` | NUTNO OVĚŘIT |
| SSH error detection | output patterns in `transport/cli_output.py:ERROR_PATTERNS` (`failure:`, `bad command name`, `syntax error`, `expected ...`, `no such item`, ...) | NUTNO OVĚŘIT |
| API selectors | CLI index / item name / `[ find k=v ]` resolved to `.id` via print; `[ find where ... ]` requires SSH | NUTNO OVĚŘIT (console index order = API print order) |
| FastTrack `hw-offload` probe | v7: `/console/inspect request=child path=ip,firewall,filter,add` must list `hw-offload`; failure = not confirmed = parameter omitted | NUTNO OVĚŘIT |
| Flash directory detection | `/file print` item `name=flash` with `type=disk` or `directory` | NUTNO OVĚŘIT |
| Session ingress port | `/ip arp` lookup of the client IP, then `/interface bridge host` `on-interface` for bridged MACs | NUTNO OVĚŘIT |
| Wireless stack presence | v7: `/interface/wifi` then `/interface/wireless`; v6: `/interface/wireless` | NUTNO OVĚŘIT |

## 3. Verification log

Board, version and chip come from the device the row was tested on.

Device A: **hEX S**, `board-name=hEX S`, `model=E60iUGS`, switch chip `EN7523`, RouterOS **7.20.7**
(long-term), reached over SSH on port 122. The QuickSet Plus hardening block had already been
applied to it, so several rows below are confirmed by the resulting device state.

| Date | Version | Board | Feature key | Result |
|---|---|---|---|---|
| 2026-09-16 | 7.20.7 | hEX S | `ntp_servers_menu` | **OVĚŘENO** — `/system ntp client servers print` lists both servers the block added; `/system ntp client print` mirrors them in a `servers=` property |
| 2026-09-16 | 7.20.7 | hEX S | `snmp_any_address` | **OVĚŘENO** — `addresses=::/0` is accepted on v7 and `set [ find default=yes ] disabled=yes` disabled the `public` community |
| 2026-09-16 | 7.20.7 | hEX S | `bridge_port_hw` | **OVĚŘENO** — `hw=yes/no` is a property, the active state is the `H` flag in `print detail`; `to_optional_bool(p, "hw-offload", "H")` reads both. ether2–5 (on the chip) ran `H`, ether1 `hw=no` |
| 2026-09-16 | 7.20.7 | hEX S | `logging_indexes` | **OVĚŘENO** — default rules are info, error, warning, critical (`.id=*1`..`*4`), the order `LOGGING_INDEX_TOPICS` assumes |
| 2026-09-16 | 7.20.7 | hEX S | `fasttrack_hw_offload` | **OVĚŘENO** — `/console inspect request=child path=ip,firewall,filter,add` lists `hw-offload`, so the runtime probe finds it |
| 2026-09-16 | 7.20.7 | hEX S | `neighbor_discovery_interface_list` | **OVĚŘENO** — `discover-interface-list=!dynamic` in effect |
| 2026-09-16 | 7.20.7 | hEX S | `ipv6_disable_setting` | **OVĚŘENO** — `/ipv6 settings set disable-ipv6=yes` on v7 |
| 2026-09-16 | 7.20.7 | hEX S | `ip_service_ssl` | **OVĚŘENO** — api-ssl 8729 present with `certificate=none`, www-ssl disabled, ssh moved to 122 |
| 2026-09-16 | 7.20.7 | hEX S | Files on flash | **OVĚŘENO** — this board has no `flash/` directory; uploads live in the root |
| 2026-09-16 | 7.20.7 | hEX S | Chip `type` string | **OVĚŘENO** — `/interface ethernet switch print` reports `EN7523`: bare, no vendor prefix and no hyphen. The `Atheros-8327` spelling in the docs is therefore not universal — normalising the key was the right call |
| 2026-09-16 | 7.20.7 | hEX S | API-SSL anonymous DH | **OVĚŘENO** — the device serves api-ssl 8729 with `certificate=none` and the application's own log records `connected to 192.168.55.68 via api-ssl`, so the ADH path (SECLEVEL=0, TLS 1.2 cap, no hostname check) completes a real handshake |
| 2026-09-16 | 7.20.7 | hEX S | `board-name` vs `model` | **PROBLÉM (opraveno)** — `board-name` is the marketing name and MikroTik reuses it across revisions: the documented hEX S (`RB760iGS`) has MT7621, this one is `model=E60iUGS` with EN7523. Device notes must be matched on `model`, never on `board-name` |
| 2026-09-16 | 7.20.7 | hEX S | MAC-Telnet transport | **OVĚŘENO po opravě** — the device does answer START_SESSION, but only if the client sends from an *ephemeral* source port (it replies to that port, from source IP 0.0.0.0) and the socket binds INADDR_ANY (the reply is a broadcast, and Windows does not deliver broadcasts to a socket bound to a unicast address). Both were wrong before; interface selection now comes from the directed broadcast |
| 2026-09-16 | 7.20.7 | hEX S | MAC-Telnet header echo | **OVĚŘENO** — the device returns the two 16-bit header fields in the opposite order from the request: a request carrying session `0xBEEF` in the first field is answered with `0x0015` there and `0xBEEF` in the second. The session filter now accepts our id in either field |
| 2026-09-16 | 7.20.7 | hEX S | `mac_telnet_ec_srp5` | **OVĚŘENO** — a real login succeeds, so the from-scratch curve arithmetic and the confirmation code are correct. Caveat for diagnosis: an expired account (`E` flag in `/user print`) is refused with exactly the same message as a wrong password |
| 2026-09-16 | 7.20.7 | hEX S | MAC-Telnet login failure | **OVĚŘENO** — a refused login arrives as terminal text ("Login failed, incorrect username or password") *after* a perfectly normal END_AUTHENTICATION. `authenticate()` reported those as success until this was fixed |
| 2026-09-16 | 7.20.7 | hEX S | MAC-Telnet terminal | **OVĚŘENO po opravě** — the console is silent until its first input and then takes ~2.5 s before sending the banner, the log backlog and two prompts. The read must therefore wait the whole timeout for the first byte, and must not stop on a prompt seen before the command echo, or the leftover prompt is returned as the output. With that, commands run and return their output |
| 2026-09-16 | 7.20.7 | hEX S | MAC-Telnet byte counter | **OVĚŘENO** — the header counter is the 4-byte field. It was wrapped at 2**16, which desynchronised every ACK past 64 KiB, less than one banner plus log backlog |
| 2026-09-16 | 7.20.7 | hEX S | MAC-Telnet source MAC | **OVĚŘENO** — the device answers the MAC carried in the payload. `uuid.getnode()` returns a random locally-administered address on a multi-adapter machine and the device never answers it; the NIC's real MAC now comes from `GetAdaptersAddresses` (`netifs.interface_mac`) |
| 2026-09-16 | 7.20.7 | hEX S | Bootstrap interface | **OVĚŘENO** — MNDP announces the port as `bridge/ether1`; the temporary address must be added to `bridge`, which is where the device carries its own address |
| 2026-09-16 | 7.20.7 | hEX S | Removing the temp address | **OVĚŘENO** — the removal kills the very session it is issued over: RouterOS drops the address and our api-ssl connection before the reply arrives. `ConnectionLost` there means success, not failure |
| 2026-09-16 | 7.20.7 | hEX S | **connect-by-MAC, end to end** | **OVĚŘENO** — MNDP discovery, MAC-Telnet login, `/ip address add ... comment=mqs-temp` on `bridge`, api-ssl connect over the temporary address, then removal. Ran twice, both clean; the device was left with only its original address |
| 2026-09-16 | 7.20.7 | hEX S | `run-after-reset` over the API | **OVĚŘENO** — the API matches file arguments against `.id` values, not names: passing `quickset-….rsc` is refused with "input does not match any value of run-after-reset" although `/file print` lists it, and the same call with the file's `.id` resets the device and runs the script. The name is right for the console, so the transport decides (`Transport.file_reference`). The error message is identical for a missing file, a directory and a valid name, so it points nowhere on its own |
| 2026-09-16 | 7.20.7 | hEX S | **Reset + apply, end to end** | **OVĚŘENO** — after the fix the device reset, rebooted, ran the uploaded script (identity, hardening, admin password) and came back announcing itself over MNDP within ~2 minutes |
| 2026-09-20 | 7.20.7 | hEX S | MAC-Telnet console timing | **OVĚŘENO** — the banner and the unread log backlog arrive in bursts with pauses between them, so a read that stops at the first quiet gap returns while the console is still talking, and the next command is swallowed without a trace. The device logged the login and the logout, and nothing was configured. Priming now waits for a prompt rather than for a gap |
| 2026-09-20 | 7.20.7 | hEX S | MAC-Telnet command confirmation | **OVĚŘENO** — a swallowed command produces no output and no error, so `run()` used to report success for a command the device never ran; `connect_by_mac` then announced a bootstrap address that did not exist. `run()` now requires the prompt back before it calls a command executed, and the bootstrap asks the device whether the address is really there |
| 2026-09-20 | 7.20.7 | hEX S | Accounts without a password | **OVĚŘENO** — a user created with an SSH key and no password is flagged `E - expired` in `/user print` and cannot log in over MAC-Telnet, which is a password protocol. `/user settings` carries no expiry policy on this device, so the flag comes from the empty password alone |
| 2026-09-20 | 7.20.7 | hEX S | Licence question after a reset | **OVĚŘENO** — from 7.13 the first console login after a reset asks "Do you want to see the software license? [Y/n]:" and waits for an answer instead of showing a prompt, so every command sent afterwards is swallowed. This is why MAC-Telnet worked before the device was reset and stopped afterwards. Priming answers it with "n" |
| 2026-09-20 | 7.20.7 | hEX S | Echo of a long command | **OVĚŘENO** — a command wider than the terminal comes back split by carriage returns and padding, so matching the echo literally never succeeds. Matching ignores whitespace, and the session now advertises a 200-column terminal |
| 2026-09-20 | 7.20.7 | hEX S | Judging a MAC-Telnet command | **OVĚŘENO** — console readability is not evidence either way: an unreadable prompt does not mean the command failed (a leftover address proved one had run), and silence does not mean it succeeded. Write commands run with `confirm=False` and the bootstrap asks the device itself whether the address is there; leftovers are cleared before adding and after any failure |
