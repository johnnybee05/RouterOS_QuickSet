# MikroTik QuickSet Plus — Implementation Plan

Status: **DRAFT — waiting for approval.** No code is written until this plan is approved
and the open questions in §9 are answered.

---

## 1. Key architectural decisions

### 1.1 Python builder + intermediate representation (no Jinja2)

Jinja2 is rejected. Output branches on major *and* minor version (6.40 vs 6.41 vs 7.x),
on live device capabilities (ipv6 package, switch chips, `hw-offload` parameter) and on
user choices. In templates this becomes nested `{% if %}` soup that cannot be unit-tested
per decision.

Instead the generator builds a small **intermediate representation (IR)** and a single
renderer serialises it:

```
DeviceProfile (pydantic) + TargetContext (version, capabilities)
        │
        ▼
 blocks/*.py  ──► Section[Command | Comment | RawLine]      (dialect-neutral IR)
        │
        ▼
 Dialect (ros6.py / ros7.py) — translation passes over IR    (NTP, IPv6, SNMP, fasttrack…)
        │
        ▼
 render.py ──► .rsc text      +   GenerationResult.warnings / .errors
```

`Command(path="/ip/firewall/filter", verb="add", args=OrderedDict, find=None, comment=None)`

Why an IR:
- dialect translation operates on structured data, not on string replace,
- "`/ip service` is always last" is enforced structurally and asserted in tests,
- quoting/escaping of values lives in exactly one function,
- the same IR is reused by the line-by-line upload fallback and by the API transport
  (`execute(path, **args)`), so we never parse our own output back,
- tests can assert on commands (`result.find("/interface/bridge/port", verb="set")`)
  in addition to golden files.

### 1.2 Feature table — the "fix it in one place" rule

`src/mqs/generator/features.py` contains one declarative table of every version-dependent
fact, e.g.:

```python
FEATURES = {
    "interface_list":        Feature(min_v6=(6, 41), v7=True,  verified=False),
    "bridge_port_hw":        Feature(min_v6=(6, 41), v7=True,  verified=False),
    "bridge_mstp":           Feature(min_v6=None,    v7=True,  verified=False),  # NUTNO OVĚŘIT
    "ntp_servers_menu":      Feature(min_v6=None,    v7=True,  verified=False),
    "fasttrack_hw_offload":  Feature(min_v6=None,    v7=RUNTIME_PROBE, verified=False),
    ...
}
```

Every entry has a matching row in `docs/DIALECT_NOTES.md`. Entries marked
`verified=False` produce a visible "NUTNO OVĚŘIT" note in the preview warnings panel.
Blocks never compare version numbers themselves — they only call `ctx.has("interface_list")`.

### 1.3 Hardening block: raw text is the source of truth

- The editable block is stored as **raw RouterOS script text** (v7 dialect), exactly as in
  the spec, so it stays copy-pasteable and hand-editable.
- `generator/hardening/parser.py` parses a *restricted subset* of RouterOS script:
  menu path lines (`/ip service`), `set`/`add`/`remove` lines with `key=value` args,
  `[ find ... ]` selectors, numeric indexes (`set 0 ...`), comments. Anything else becomes
  a `RawLine` that is passed through verbatim — with a warning in v6 output
  ("řádek nelze přeložit, ověř ručně").
- **Two-way binding** of the form fields (DNS servers, SSH port, SNMP community, NTP servers,
  timezone): editing a field rewrites the corresponding line in the text; editing the text
  re-parses it and refreshes the fields. There are no `{{placeholders}}` in the text.
- The parsed block is split by menu path into the mandated order:
  DNS / NTP / clock / SNMP / neighbor discovery / IPv6 / logging go to section 5,
  `/ip service` goes to section 6 (always last), unknown menus go to section 5 in
  their original relative order.
- Access-path analysis (`generator/hardening/access.py`) evaluates the final
  `/ip service` state = device state (if connected) or RouterOS defaults (offline)
  overlaid with the block → counts remaining paths (api-ssl, ssh, winbox) → red warning
  on any disabled path, hard block on zero.

### 1.4 Pure core, injected I/O

Layers and allowed imports (enforced by a test that inspects imports):

| Package            | May import                      | Network | Qt |
|--------------------|---------------------------------|---------|----|
| `mqs.model`        | pydantic                        | no      | no |
| `mqs.capabilities` | model                           | no      | no |
| `mqs.generator`    | model, capabilities             | no      | no |
| `mqs.profiles`     | model                           | no      | no |
| `mqs.transport`    | model (inventory types)         | yes     | no |
| `mqs.apply`        | all above except ui             | via injected `Transport` | no |
| `mqs.ui`           | everything                      | only through workers | yes |

Two packages are added compared to the spec's tree, both to keep the generator pure:
- `mqs/capabilities/` — capability matrix and per-port HW offload decisions computed
  from a `DeviceInventory` (pure data). Tested with simulated RB4011 etc.
- `mqs/apply/` — the apply orchestrator (backup → upload → rollback guard → import/reset →
  reconnect → verify). Takes a `Transport` and `FileUploader` by injection, so the whole
  flow is tested with mocks.

---

## 2. Project tree

```
mikrotik-quickset-plus/
├─ src/mqs/
│  ├─ model/
│  │   ├─ version.py        # RosVersion (major, minor, patch, channel), parsing "7.15.3 (stable)"
│  │   ├─ network.py        # IPv4 interface/CIDR helpers, pool range type
│  │   ├─ profile.py        # DeviceProfile = identity, mode, wan, bridge, lan, firewall, offload, services
│  │   ├─ wan.py            # WanConfig: DhcpWan | StaticWan | PppoeWan (discriminated union)
│  │   ├─ bridge.py         # BridgeConfig, LanConfig, DhcpServerConfig
│  │   ├─ firewall.py       # FirewallConfig, FastTrackConfig, HwOffloadMode(AUTO/FORCE_ON/OFF)
│  │   ├─ services.py       # ServicesConfig: hardening raw text, robust_logging, cert/mgmt-subnet options
│  │   ├─ inventory.py      # DeviceInventory: resource, identity, routerboard, interfaces, switch chips, packages
│  │   ├─ presets.py        # "Router" / "Bridge / AP" / "Vlastní" form pre-fill
│  │   └─ validation.py     # cross-field validators (WAN∉bridge, pool⊂subnet, gw reachable, identity chars)
│  ├─ capabilities/
│  │   ├─ matrix.py         # Capabilities built from inventory (+ offline assumptions)
│  │   ├─ offload.py        # per-port decision: OffloadDecision(port, chip, hw: bool|None, reason)
│  │   └─ known_devices.py  # optional enrichment loader for data/known_devices.yaml
│  ├─ generator/
│  │   ├─ ir.py             # Command, Comment, RawLine, Section, Script
│  │   ├─ render.py         # IR → .rsc text, value quoting
│  │   ├─ features.py       # the single version/feature table (§1.2)
│  │   ├─ context.py        # TargetContext(version, capabilities, assumptions)
│  │   ├─ base.py           # Generator: runs blocks in mandated order, collects warnings
│  │   ├─ ros6.py           # Dialect6 translation passes
│  │   ├─ ros7.py           # Dialect7 translation passes
│  │   ├─ hardening/
│  │   │   ├─ default_v7.rsc  # the default block, verbatim from spec
│  │   │   ├─ parser.py
│  │   │   ├─ binding.py    # form fields <-> text lines
│  │   │   └─ access.py     # remaining-access-path analysis
│  │   └─ blocks/
│  │       ├─ identity.py
│  │       ├─ bridge.py     # bridge, ports, hw=, interface lists
│  │       ├─ addressing.py # IP addresses, DHCP client, PPPoE, pool, DHCP server
│  │       ├─ firewall.py   # filter, fasttrack, NAT, MSS clamp
│  │       ├─ system.py     # hardening non-service part (DNS, NTP, clock, SNMP, ND, IPv6, logging)
│  │       └─ services.py   # /ip service (always last)
│  ├─ transport/
│  │   ├─ base.py           # Transport protocol, TransportError hierarchy, Reply types
│  │   ├─ tls.py            # SSLContext factories: anonymous-DH vs verified (+ CA import)
│  │   ├─ api_ssl.py        # librouteros over TLS, port 8729
│  │   ├─ ssh.py            # paramiko exec_command, user+cet, ANSI sanitising, timeouts
│  │   ├─ cli.py            # (path, args) → single-line CLI command for SSH
│  │   ├─ upload.py         # FileUploader + strategies: SFTP, SCP, HTTP+/tool fetch, line-by-line
│  │   ├─ factory.py        # API-SSL with automatic SSH fallback
│  │   └─ discovery.py      # runs the inventory commands, builds DeviceInventory
│  ├─ apply/
│  │   ├─ plan.py           # ApplyMode (GENERATE_ONLY / IMPORT / RESET_AND_IMPORT), ApplyPlan
│  │   ├─ steps.py          # backup, export, download, upload, rollback guard, import, reset
│  │   ├─ rollback.py       # scheduler script generation + cancel
│  │   ├─ reconnect.py      # reconnect with new SSH port / wait for reboot
│  │   ├─ verify.py         # post-apply checklist + HW offload actual-vs-requested diff
│  │   └─ orchestrator.py   # cancellable step runner, emits progress events
│  ├─ profiles/
│  │   └─ store.py          # JSON profiles in %LOCALAPPDATA%, import/export, password stripping
│  ├─ ui/
│  │   ├─ main_window.py    # left navigation + stacked pages, status bar, dialect header
│  │   ├─ pages/            # device, basic, lan_bridge, firewall, services, preview, apply
│  │   ├─ widgets/          # port selector, offload table, validated line edit, log view
│  │   ├─ highlighter.py    # QSyntaxHighlighter for RouterOS script
│  │   ├─ workers.py        # QThread workers with cancel
│  │   ├─ theme.py          # Windows light/dark following
│  │   └─ strings_cs.py     # all Czech UI strings in one place
│  ├─ logging_setup.py      # rotating file log in %LOCALAPPDATA%\MikrotikQuickSetPlus\logs\
│  └─ app.py
├─ data/known_devices.yaml, data/switch_chips.yaml
├─ tests/ (golden/, fixtures/inventories/, fixtures/tls/, test_*.py)
├─ docs/DIALECT_NOTES.md
├─ assets/icon.ico
├─ build.ps1, README.md (Czech), PLAN.md, pyproject.toml
```

---

## 3. Core details

### 3.1 Model

- pydantic v2, `model_config = ConfigDict(extra="forbid")`, all defaults explicit.
- Secrets (`device password`, `PPPoE password`) are `SecretStr` and marked
  `exclude=True` for profile serialisation. They exist only in memory.
- Validation errors carry a field path so the UI can paint the right widget red with the
  Czech message (messages mapped in `ui/strings_cs.py`, model raises error codes).
- Identity: RouterOS-accepted characters and max length — the exact rule is
  **NUTNO OVĚŘIT** (see §9, Q9); implemented as one regex constant.

### 3.2 Capabilities & HW offload decision (`capabilities/offload.py`)

Input: `DeviceInventory` (or an offline assumption set), `BridgeConfig`, `HwOffloadMode`.
Per member port, first matching rule wins:

1. version < 6.41 → feature not offered at all, nothing emitted (`bridge_port_hw`).
2. mode OFF → `hw=no`, reason "vypnuto uživatelem".
3. port type SFP/SFP+/wireless/bonding/VLAN → no, with specific reason
   (SFP on a switch chip: listed as "obvykle ne" and kept overridable by FORCE_ON).
4. port not attached to any switch chip → no, "port není na switch čipu".
5. `use-ip-firewall=yes` → no + warning.
6. `protocol-mode=mstp` + chip without MSTP offload → **conflict** (not a silent `hw=yes`);
   `GenerationResult.errors` contains `OffloadConflict` with a one-click "přepnout na rstp" fix.
7. `vlan-filtering=yes` → lookup chip in `data/switch_chips.yaml`; unknown → `hw=None`
   ("nejisté, ověř po aplikaci").
8. otherwise → yes.

Then ports are grouped by chip → `OffloadGroup`s; if a bridge spans more than one group,
a "softwarový můstek mezi čipy X a Y" note is produced. The chip capability table
(`data/switch_chips.yaml`) is data, not code, and every row carries `verified: false`
until confirmed.

### 3.3 Generator

- Deterministic: ordered dicts, sorted where order has no semantics (never sort firewall
  rules), no timestamps in the script body (timestamp only in the optional header comment,
  disabled in golden tests via injected clock).
- Section order (hard-coded list in `base.py`, asserted in tests):
  1 identity → 2 bridge/ports/lists → 3 addressing/DHCP/PPPoE/pool → 4 firewall/NAT →
  5 DNS/NTP/clock/SNMP/ND/IPv6/logging → 6 `/ip service`.
- Dialect passes (examples):
  - v6 NTP: `/system ntp client servers add` ×N → `set enabled=yes primary-ntp=… secondary-ntp=…`;
    more than 2 servers → warning; hostnames → `server-dns-names=` only if feature says so.
  - v6 IPv6: depends on detected ipv6 package state (see §9, Q3).
  - SNMP `addresses=::/0` handling (see §9, Q4).
  - interface lists < 6.41 → `out-interface=<port>` / `in-interface=<bridge>`.
  - v6 fasttrack → `hw-offload` never emitted (also asserted by test for every v6 golden).
  - logging: literal `set 0..3 action=disk` by default; "robustní varianta" →
    `set [find where topics=…] action=disk` (exact find syntax **NUTNO OVĚŘIT**).
- Firewall: fasttrack rule immediately followed by its accept rule (asserted in tests).
- Lockout guard: when connected, the app finds which interface our own session arrives on
  (`/ip arp` lookup of the client IP) and warns if that port becomes WAN or leaves the bridge.

---

## 4. Transport

- `Transport` protocol: `connect()`, `execute(path, **args) -> list[dict]`, `close()`,
  plus `kind`, `peer_identity: PeerSecurity` (`ANONYMOUS_DH` → UI "nešifrovaná identita
  protistrany", `VERIFIED` → "ověřený certifikát").
- Errors: `AuthError`, `ConnectTimeout`, `CommandTimeout`, `CommandError(path, message)`,
  `ConnectionLost`, `Cancelled`.
- **API-SSL** (`api_ssl.py`, librouteros): `ssl_wrapper` from `tls.py`.
  Anonymous branch: `set_ciphers("ADH:@SECLEVEL=0")`, `check_hostname=False`,
  `CERT_NONE`, and TLS max version capped to 1.2 (ADH does not exist in TLS 1.3 —
  cap **NUTNO OVĚŘIT** on a real device). Verified branch: default context + optional CA file.
  Strategy: try verified → on handshake failure try anonymous only if the user allowed it
  (see §9, Q6). Both branches tested against a local Python TLS server in pytest.
  Login method (plain vs legacy token for < 6.43) handled by librouteros; **NUTNO OVĚŘIT**.
- **SSH** (`ssh.py`, paramiko): username suffix `+cet`, `exec_command` only (no PTY),
  one line per command, ANSI/CSI/OSC stripping, connect timeout 10 s / command timeout 30 s
  (configurable), password or key. RouterOS CLI errors are detected from output patterns
  (`failure:`, `expected …`, `syntax error`, `bad command name`) — pattern list in one place.
- **Fallback factory**: API-SSL → on connect failure offer SSH (auto if the user chose "auto").
- **Discovery**: runs the spec's inventory commands via whichever transport is active,
  parses into `DeviceInventory`. v7 wifi vs wireless presence detected, not configured.
  Also collects `/file` (flash dir detection), `/ip service` state, `/certificate` names.

### 4.1 FileUploader

`FileUploader.upload(local_bytes, remote_name) -> UploadResult(strategy, remote_path)`,
strategies tried in order, each failure logged with reason:

1. SFTP (paramiko), 2. SCP (`scp` package on the paramiko transport),
3. temporary local HTTP server on an ephemeral port bound to the interface that routes to the
   device + `/tool fetch url=http://<my-ip>:<port>/<token>/config.rsc dst-path=…`
   (random URL token, server serves exactly one file, auto-shutdown; Windows Firewall may
   prompt — shown in the log),
4. line-by-line: IR commands executed one by one via the active transport (not a file;
   valid only for mode IMPORT, not for RESET which needs a file).

Remote path: `flash/<name>` when a `flash` directory exists (files in RAM root would not
survive the reset reboot) — **NUTNO OVĚŘIT**.

---

## 5. Apply flow (`mqs/apply`)

Every step is a cancellable object with `run(ctx)` and a human-readable Czech label.

```
preflight  → version/dialect match (explicit confirm on mismatch), model valid,
             access paths > 0, offload conflicts resolved, lockout guard
dry-run    → show exact script + exact list of device commands, require confirm
backup     → /system backup save name=pre-quickset-<ts> ; /export file=pre-quickset-<ts>
download   → offer download of both files (SFTP/SCP), failure = warning, not abort
upload     → FileUploader (modes IMPORT and RESET)
rollback   → (opt-in, IMPORT only) scheduler job, see below
execute    → IMPORT: /import file-name=…      RESET: /system reset-configuration
             no-defaults=yes skip-backup=no run-after-reset=… (typed-identity confirm dialog)
reconnect  → IMPORT: keep API-SSL session if alive; else retry API-SSL, SSH on new port
             RESET: wait for the device to go down and come back, poll with backoff
verify     → checklist: identity, /ip service, bridge, addresses; HW offload
             requested vs actual from /interface bridge port print detail
confirm    → "Potvrdit, že konfigurace funguje" removes the rollback scheduler job
```

**Rollback guard (anti-loop design):**
- The backup is taken *before* the scheduler job is created, so restoring that backup
  cannot bring the job back.
- The job's script first removes itself (`/system scheduler remove [find name=…]`), then
  loads the backup (which reboots). If loading fails, the job is already gone → no loop.
- One-shot schedule (start-date/start-time = now + N min, `interval=0`), computed from the
  **device clock** read at scheduling time, not from the PC clock.
- Exact `backup load` non-interactive syntax per version: **NUTNO OVĚŘIT**.
- Not offered for RESET mode (reset wipes the scheduler anyway).

Connection loss during `execute` never fails silently: the orchestrator classifies the loss
(expected after SSH port change / reboot vs unexpected), retries with the new port, and the
final report says which step lost the link and what the reconnect attempt found.

Logs: rolling UI view + rotating file `%LOCALAPPDATA%\MikrotikQuickSetPlus\logs\mqs-YYYYMMDD.log`,
passwords masked by a logging filter.

---

## 6. GUI (PySide6)

- Left navigation list, right `QStackedWidget`: Zařízení, Základ, LAN / Bridge, Firewall,
  Služby a hardening, Náhled, Aplikovat.
- Header strip: large dialect badge (RouterOS v6 / v7 / offline), status bar: connection
  state, IP, version, model.
- One `ProfileController` holds the current `DeviceProfile`; every edit revalidates in real
  time (debounced) and regenerates the preview; invalid fields red with message tooltip +
  inline label; "Aplikovat" disabled while invalid.
- Offline mode: target version selected manually (e.g. 6.49 / 7.x), port list entered
  manually or from `known_devices.yaml`, ipv6-package assumption selectable.
- Port selector: WAN port automatically removed/disabled in the bridge member list.
- Offload table: port | chip | offload ANO/NE/nejisté | reason; group summary above it.
- FastTrack: separate panel with explanation; enabling it triggers the "používáte
  tvarování provozu?" question.
- Services page: form fields + `QPlainTextEdit` with `QSyntaxHighlighter`, "Obnovit výchozí",
  access-path indicator (red when a path is disabled).
- Preview: v6 / v7 / side-by-side, copy, save .rsc, warnings list.
- All network work in `QThread` workers; cancel closes the socket to unblock paramiko/librouteros.
- Light/dark: Qt 6's Windows color-scheme support (`QStyleHints.colorScheme`), highlighter
  palette per scheme.

---

## 7. Tests

- `tests/golden/` — named scenarios × {v6.40, v6.49, v7.x}: router-dhcp, router-static,
  router-pppoe, bridge-ap, rb4011-offload, custom-hardening, robust-logging.
  `pytest --update-golden` rewrites files; diff review is manual.
- Mandated tests: NTP & IPv6 translation to v6; `/ip service` always last; fasttrack followed
  by accept; v6 output never contains `hw-offload`; 6.40 output contains no `hw=`;
  RB4011 → two offload groups + software bridge note; mstp + offload → conflict;
  validation negatives (WAN in bridge, pool outside subnet, pool colliding with bridge IP,
  unreachable static gw, bad identity, port range); no IPv6-only constructs when IPv6 disabled.
- Transport: TLS ADH + verified branches against a local TLS server; SSH with mocked paramiko
  (`+cet` username, ANSI stripping, timeouts, error patterns); uploader fallback order.
- Apply: full orchestrator runs against a `FakeRouter` transport (in-memory state machine that
  records commands, can simulate connection loss, port change, reboot, failing strategies).
- Architecture test: generator/model/capabilities import neither Qt nor network libraries.
- Coverage gate: `model`, `capabilities`, `generator` ≥ 90 % (`pytest --cov`, fail-under).
- UI: smoke tests with `pytest-qt` (window builds, validation disables Apply) — kept small.

---

## 8. Implementation order (each milestone = commit(s) + short report to you)

| # | Milestone | Content |
|---|-----------|---------|
| M0 | Scaffolding | `git init`, pyproject (hatchling, src layout), ruff, pytest, `docs/DIALECT_NOTES.md` seeded with the spec table + NUTNO OVĚŘIT items |
| M1 | Model + validation | pydantic models, presets, validators, tests |
| M2 | IR + renderer + features + hardening parser/binding/access | tests |
| M3 | Capabilities + offload decisions | simulated inventories (RB4011, hAP, CRS3xx, 6.40 device), tests |
| M4 | Generator blocks + ros6/ros7 + golden files | coverage gate ≥ 90 % |
| M5 | Transport | tls, api_ssl, ssh, cli, discovery, fallback factory, tests |
| M6 | Upload strategies + apply orchestrator + rollback + verify | FakeRouter tests |
| M7 | Profiles + logging setup | tests |
| M8 | GUI | pages incrementally, workers, theme |
| M9 | Packaging + docs | `build.ps1` (PyInstaller onefile, windowed, icon), Czech README |

Core (M0–M4) is fully usable offline via a tiny dev CLI (`python -m mqs.generator …`) before
any GUI exists — handy for you to review generated scripts early.

---

## 8a. Progress

| Milestone | State |
|---|---|
| M0 scaffolding | done |
| M1 model + validation | done |
| M2 IR, renderer, feature table, hardening parser/binding/access | done |
| M3 capabilities + HW offload | done |
| M4 generator v6/v7 + golden files | done |
| M5a transports (API-SSL, SSH), fallback, discovery | done |
| M5b MNDP network discovery | done |
| M5c MAC connection | done (B + C): link-local, EC-SRP5 MAC-Telnet bootstrap, GUI button |
| M6 apply flow (backup, upload, rollback, reconnect, verify) | done |
| M7 profiles + logging | done |
| M8 GUI | done |
| M9 packaging + README | in progress |

Deviation from §2: `blocks/services.py` was folded into `blocks/system.py`; the default hardening
block lives in `model/resources/`; data files live in `src/mqs/data/` so they are packaged.

## 9. Decisions (plan approved 2026-09-14, recommendations accepted)

| # | Topic | Decision |
|---|-------|----------|
| Q1 | Python | 3.13 is fine; `requires-python >=3.12` |
| Q2 | Password after reset | Reset script sets the admin password (entered for this run only); the uploaded `.rsc` removes itself at the end |
| Q3 | IPv6 on v6 | ipv6 package enabled → `/system package disable ipv6` + "reboot required" warning; package missing → block omitted with warning. `disable-ipv6` on v6 marked NUTNO OVĚŘIT |
| Q4 | SNMP addresses | v7 keeps `::/0` (RouterOS default), v6 generates `0.0.0.0/0` |
| Q5 | Import on running config | Plain script; pre-flight collision check against the device with warnings |
| Q6 | Anonymous DH | Automatic fallback, permanently visible "nešifrovaná identita protistrany" badge |
| Q7 | Minimum v6 | 6.40 supported; older versions refused |
| Q8 | api-ssl certificate | Choose from certificates already on the device; no generation |
| Q9 | Identity | Conservative `[A-Za-z0-9._-]`, max 64 chars, one constant |
| Q10 | run-after-reset | Configurable `:delay` at the start of the reset file only (default NUTNO OVĚŘIT) |
| Q11 | Milestones | No waiting for approval — commit + short report, then continue |
| Q12 | LAN gateway (added 2026-09-15) | A static bridge address can carry a gateway, generating `/ip route add dst-address=0.0.0.0/0`. Bridge / AP has no WAN of its own, so this is its only way out; Winbox QuickSet offers the same field in bridge mode. A gateway next to a WAN that already routes is a warning, not an error |

---

## 10. Addendum: network discovery and connecting by IP or MAC (added 2026-09-14)

### 10.1 Discovery before connecting (`transport/mndp.py`, `transport/netifs.py`)

- **MNDP** (MikroTik Neighbor Discovery Protocol, UDP 5678):
  - one socket per local IPv4 interface (enumerated with `ifaddr`), bound to that
    interface's address, so each reply is linked to the local NIC it arrived on,
  - active: send a refresh/solicit packet to `255.255.255.255:5678` on each interface,
  - passive: keep listening, because devices also announce themselves periodically,
  - parse the TLVs (MAC, identity, version, platform, board, uptime, software-id,
    interface name, IPv4, IPv6). The type codes are in one table in `mndp.py` and marked
    NUTNO OVĚŘIT until checked against a real capture.
- The parser is pure (bytes → `DiscoveredDevice`) and tested on captured sample packets
  kept in `tests/fixtures/mndp/`. The socket layer is a thin wrapper.
- UI: the "Zařízení" page gets a "Vyhledat v síti" table (identity, IP, MAC, version,
  board, local NIC, remote interface) with auto-refresh. A row fills the connect form.
  Addresses are shown for information only; the dialect is still taken from the version
  read after connecting.
- Possible problems shown to the user: Windows Firewall may block inbound UDP 5678, and a
  running Winbox may already hold the port (`SO_REUSEADDR` is attempted).

### 10.2 Connecting by MAC address

API-SSL and SSH both need IP, so "connect by MAC" is implemented as a **bootstrap**.
The strategies below are tried in order:

1. **IPv6 link-local.** If MNDP reports a `fe80::` address, connect to API-SSL or SSH at
   `fe80::…%<local ifindex>`. It is cheap, but it only works when IPv6 is enabled on the
   device (usually not on v6).
2. **MAC-Telnet bootstrap** (`transport/mactelnet/`, UDP 20561, layer-2 broadcast):
   - log in with MD5 on old devices (< 6.43) or EC-SRP5 on 6.43+ (the protocol is
     reverse-engineered, so the whole package is NUTNO OVĚŘIT),
   - add a temporary management IP to the device interface the MNDP reply came from:
     `/ip address add address=<free IP in the PC's subnet>/<prefix> interface=<if> comment=mqs-temp`.
     The IP is proposed automatically, checked as free (ARP/ping) and confirmed by the user,
   - switch to API-SSL or SSH over that IP, so the rest of the app works unchanged,
   - cleanup: IMPORT mode removes `comment=mqs-temp` as the last verified step, and RESET
     mode wipes it anyway.
- MAC-Telnet is a terminal session (no exec channel). It is used only for the few bootstrap
  commands, with prompt detection and ANSI stripping. It is not a full `Transport`.
- Requirement on the device: `/tool mac-server` is allowed on that interface. The hardening
  block does not change it; this is noted in DIALECT_NOTES.
- Tests: packet encode/decode and session state machine against a fake UDP peer. The
  EC-SRP5 math is tested for internal consistency (client and simulated server derive the
  same key); interoperability can only be verified on a device.

### 10.3 Status of the MAC connection (2026-09-15)

Not implemented yet. MAC-Telnet authentication on RouterOS 6.43+ (EC-SRP5) is only known from
reverse engineering; implementing it from memory would break the "never invent" rule. Options
put to the user: A) MD5-only MAC-Telnet, B) EC-SRP5 based on a public reference implementation
(after a licence check), C) IPv6 link-local only. Recommendation: B + C. The GUI button
"Připojit přes MAC" is disabled until this is decided.

**Decision 2026-09-15: B + C.** IPv6 link-local is tried first; the MAC-Telnet bootstrap uses
EC-SRP5 implemented from a public reference after a licence check (see
`docs/DIALECT_NOTES.md` for the reference and what remains NUTNO OVĚŘIT).

### 10.4 Updated milestones

M5 becomes **M5a** Transport (TLS, API-SSL, SSH, discovery) + **M5b** MNDP discovery +
**M5c** MAC connection (link-local, MAC-Telnet bootstrap). All other milestones are unchanged.

