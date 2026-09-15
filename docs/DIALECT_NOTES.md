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

(empty — add rows as `YYYY-MM-DD | RouterOS version | board | feature key | result`)
