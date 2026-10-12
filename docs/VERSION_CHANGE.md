# Changing the RouterOS version on a device (design)

Status: **implemented** (`apply/version_change.py`, `mqs.cli version`, the window's
*Přejít na jinou verzi RouterOS*) and verified on the mmips hEX S on 2026-09-27 on **every
route it can plan**: up and down inside RouterOS 6, 6.49.21 to 7.23.7 across the major
version, 7.23.7 back to 6.49.21, and the **two-hop route 6.49.12 → 6.49.21 → 7.23.7** in one
command. RouterOS converted the configuration on the way up and the board kept it on the way
back as well, so it answered on the same address every time; §11 says what that leaves open.
On 2026-09-28, after the gate moved to 6.49.22, the two-hop route was walked again on the same
board — 6.49.12 → **6.49.22** → 7.23.7, with the 6.49.21 in the directory no longer chosen as
the first hop (§11, row 12).
Where the design below
differs from what was built, the built thing is described here and the reason given.

Today the application can only move a device *forward inside its own major version*
(`apply/update.py`, `--update`): the packages are uploaded to the file root and the reset's
reboot installs them. Three things it cannot do, and this design adds them as one feature:

1. **Downgrade** — install a package older than the one running. A reboot never does this;
   it takes `/system package downgrade`, which reboots by itself.
2. **v6 → v7** — a move MikroTik allows only from the last v6 release, 6.49.22.
3. **v7 → v6** — a downgrade across the major version, bounded by the board's factory
   firmware and, for most boards, impossible.

## 1. Why this is not "update with a `<` instead of a `>`"

| | update (today) | version change (this design) |
|---|---|---|
| target | implicit: the newest package in the directory for the device's major | **explicit: the user picks it**, a downgrade is never implicit |
| install | the reset's reboot, as a side effect | its own command, its own reboot, **before** any reset |
| hops | always one | one or two (v6 → 6.49.22 → v7) |
| configuration | wiped by the reset that follows anyway | **stays** between the hops, and a downgrade may leave it unusable |
| lower bound | none | `factory-firmware` of the board |
| reachability | the device comes back on the applied configuration | after a cross-major hop the device may come back **anywhere** (§5.4) |

So the version change is a **phase of its own that runs before the apply**, and the existing
update path stays exactly as it is — it is verified on hardware (v6 and v7) and this design
does not touch its steps. When the whole job is one upgrade hop, the planner keeps folding it
into the reset's reboot, as today.

## 2. The two hard rules

### 2.1 The floor: `factory-firmware`

A device cannot be downgraded below the RouterOS version its board was shipped with.
`/system routerboard print` reports it as `factory-firmware`, next to `current-firmware` and
`upgrade-firmware`, which the application already reads.

- `factory-firmware` is a **RouterBOOT** version, and the planner compares it with **RouterOS**
  versions. The two numbering schemes have run together since 6.x (the firmware ships inside
  the RouterOS package: after the update to 7.23.7 the hEX S offered `upgrade-firmware=7.23.7`),
  so the comparison is `RosVersion` against `RosVersion` — NUTNO OVĚŘIT that no test board
  reports a firmware version this parser rejects.
- No `factory-firmware` (CHR, x86, `routerboard=false`, or a field this version does not have)
  → **no floor is known**. The downgrade is then offered only with an explicit confirmation and
  a warning that the board may refuse the package or fail to boot.
- Prediction to verify: the mmips RB760iGS (2016 hardware) has a v6 factory firmware and can go
  down to roughly 6.4x; the arm hEX S E60iUGS was shipped with v7 and **cannot reach v6 at all**.
  That asymmetry is exactly what the floor rule is there to show the user before the reboot.

### 2.2 The gate between v6 and v7

- **v6 → v7 only from 6.49.22.** Implemented as one constant,
  `V6_TO_V7_FROM = RosVersion(major=6, minor=49, patch=22)`, and read as a **minimum**, not as
  an exact version: a v6 release newer than 6.49.22, should one ever appear, would go straight
  to v7 instead of being sent down to 6.49.22 first.
- **The gate moved up on 2026-09-28**: it was 6.49.21 until then, which is the version the
  hardware run of 2026-09-27 (§11, rows 8 and 10) actually crossed from. That run stands as a
  record of what the device does; it is not what the planner offers any more — 6.49.21 now
  takes the two-hop route like any other release below the gate, **OVĚŘENO** on hardware the
  same day (§11, row 12). Nothing else about the rule
  changed, and the gate is still one constant with one meaning.
- A device below the gate is not refused: the planner returns a **two-hop route**, first to the
  newest 6.49.x ≥ 6.49.22 in the Update directory, then to the v7 package. Without such a
  package in the directory it is refused with a message naming the file that is missing.
- **Since 2026-10-12 every v6 device goes through the newest v6 in the Update directory**, the
  user's rule: a 6.49.22 device with 6.49.23 in the directory takes `6.49.22 → 6.49.23 → 7.x`,
  although MikroTik would let it cross by itself. Only a device already on that newest v6, or
  past it, crosses in one hop — also when the directory holds no v6 at all, as long as the
  device is at 6.49.22 or newer. `V6_TO_V7_FROM` stays the floor of that newest v6, and the
  rule itself is the `v6_to_v7` gate in `wireless_packages.yaml` (`when_below: 7.0.0`). The
  plan says so in an INFO line, `version.via_last_v6`. **OVĚŘENO** on the mmips hEX S on
  2026-10-12: 6.49.22 → 6.49.23 → 7.23.8 in one command (§11, row 15).
- **Since 2026-10-12 the RouterBOOT of that newest v6 goes on before v7 does**, as MikroTik
  recommends and on the user's word: the board crosses with the newest v6 RouterOS *and*
  its firmware. The hop that crosses carries `Hop.routerboot_first` when the board's
  `current-firmware` is behind the v6 it crosses from, and the runner writes the firmware
  with a reboot of its own after the last v6 hop (or after the backup, when the device is
  already on the newest v6) and before the v7 package is uploaded. It is part of the route,
  not of the *Aktualizovat RouterBOOT* switch, which still only says whether the target's
  firmware is written at the end. A firmware already there or newer (a v7 RouterBOOT left
  behind by a downgrade) is left alone; a board without RouterBOOT (CHR, x86) skips it. The
  plan says so in `version.routerboot_before_v7` (INFO) and counts the extra reboot in
  `VersionPlan.reboots`. A write that fails stops the route on v6 with
  `apply.failed.version_routerboot`, v7 not uploaded. **OVĚŘENO** on the mmips hEX S on
  2026-10-12: 6.49.22 → 6.49.23, RouterBOOT 6.49.22 → 6.49.23, then 7.24.4 (§11, row 16).
- **v7 → v6** is a downgrade like any other, with the floor rule and one addition: the
  configuration does not come back (§5.4), so it is gated behind the same typed-identity
  confirmation as a reset.
- RouterOS's own `force-v6-to-v7-configuration-upgrade` (a `/system reset-configuration`
  parameter, seen in `/console inspect` on 7.20.7) is **deliberately not used**: the application
  never migrates a configuration, it generates a fresh one for the target version and imports it
  after a `no-defaults` reset. That is what makes a cross-major move safe here at all.

## 3. Prerequisite: read the factory firmware

`RouterboardInfo` gains `factory_firmware: str = ""`, filled in `transport/discovery.py`
next to `current_firmware` / `upgrade_firmware` from the same `/system routerboard print` row.
One field, one line, and the only change outside the new module — everything else in §4 to §9
is additive.

## 4. The planner (`src/mqs/apply/version_change.py`)

The package layer moves out of `apply/update.py` into `apply/packages.py`
(`read_npk`, `NpkPackage`, `ARCHITECTURES`, `scan_update_dir`, `UpdateDir`, `is_main_package`,
`extra_packages`, the free-room rule); `update.py` keeps `plan_update` and the RouterBOOT
helpers and imports from it, so its verified behaviour is untouched and the tests for it keep
passing unchanged.

```python
class Install(StrEnum):
    BY_REBOOT = "reboot"      # package newer than the running one: any reboot installs it
    BY_DOWNGRADE = "downgrade"  # older: /system package downgrade, which reboots itself

@dataclass(frozen=True)
class Hop:
    """One reinstall: the files to upload and how the device is made to install them."""
    packages: tuple[NpkPackage, ...]
    version: RosVersion
    install: Install
    crosses_major: bool

@dataclass(frozen=True)
class VersionPlan:
    current: RosVersion
    target: RosVersion | None = None
    hops: tuple[Hop, ...] = ()
    floor: RosVersion | None = None       # factory-firmware, None = unknown
    issues: tuple[Issue, ...] = ()

    @property
    def changes(self) -> bool: ...          # any hop at all
    @property
    def needs_confirmation(self) -> bool: ...  # any downgrade, or any crossing hop
    @property
    def reboots(self) -> int: ...           # for the dialog: one per hop
```

Two entry points:

```python
def available_versions(inventory: DeviceInventory, found: UpdateDir) -> list[VersionOffer]
def plan_version(inventory: DeviceInventory, found: UpdateDir,
                 target: RosVersion) -> VersionPlan
```

`available_versions` is what fills the GUI combo: every main package in the directory built for
the device's architecture, each with the reason it cannot be used (`blocked_by`), so the list
shows *why* 6.49.21 is greyed out on an arm hEX S instead of hiding it. `plan_version` is the
one the apply asks, and it refuses rather than guesses.

Rules, in the order the planner applies them:

| # | Rule | Outcome when it fails |
|---|---|---|
| 1 | the architecture is known and the package is built for it | `version.unknown_arch`, `version.no_package` |
| 2 | target ≠ current | `version.same` (INFO, no hops) |
| 3 | target ≥ floor, when a floor is known | `version.below_factory` (ERROR, names `factory-firmware`) |
| 4 | no floor known → allowed, with `version.no_factory_firmware` (WARNING) | — |
| 5 | v6 → v7 and current < `V6_TO_V7_FROM` → insert the 6.49.x hop, and say that RouterOS converts the configuration (`version.v7_config_converted`, WARNING) | `version.v7_needs_649` when no such package is in the directory |
| 6 | v7 → v6 → one hop, `crosses_major`, plus `version.v6_config_lost` (WARNING) | — |
| 7 | every installed extra package exists in the target version for the arch | `version.extra_missing` (WARNING, per package) |
| 8 | the files of each hop fit `DeviceInventory.package_space` | `version.no_ram` / `version.no_space` (ERROR) |
| 9 | a hop that only goes forward inside the major gets `Install.BY_REBOOT`, anything else `BY_DOWNGRADE` | — |

Rule 7 on a cross-major hop is a different animal: a v7 `wifi-qcom` has no v6 counterpart at all,
and v6 brings its extras inside the bundle. There the planner does not ask for files, it warns
that the packages are dropped (`version.extras_dropped`) — what RouterOS actually does with them
is NUTNO OVĚŘIT (they are expected to be left in `/system package print` unusable, or removed).

Rule 8 measures each hop separately: the second hop's files are uploaded after the first hop's
reboot, so they never have to fit at the same time.

## 5. Orchestration

### 5.1 Where it runs

`VersionChangeRunner` (same module) drives the hops against a live session, before the apply:

```
backup + download        (the existing apply steps, unchanged, on the version that still works)
  └─ per hop:  upload .npk → install → reboot → reconnect → verify version → remove leftovers
       └─ re-discover the device  → regenerate the script for what came back
            └─ the existing apply (reset + import), with update=None
```

The backup is the version change's **own first step** since 2026-09-28, and no longer
something an apply happened to have done earlier. Until then it was only ever taken by
`ApplyOrchestrator`, so the route walked from the window's *Přejít na jinou verzi RouterOS
(samostatně, bez resetu)* button — and from `mqs.cli version --to`, which is the same flow —
rebooted the device into another RouterOS with **nothing saved**, which is exactly where it is
needed most: a downgrade drops the configuration and a move across the major version converts
it one way only. The step now lives in `apply/backup.py` and both flows run the same one, the
way they already share `apply/routerboot.py`:

| | apply | version change |
|---|---|---|
| when | first, before anything is uploaded | first, before the **first hop** is uploaded; skipped when the route has no hops (the RouterBOOT step on its own changes no configuration) |
| what | `/system backup save` + `/export`, both under `flash/` where there is one | the same, the same names (`pre-quickset-<stamp>`) and the same codes |
| copy here | the *Stáhnout zálohu* switch and its directory, `--download-dir` | the same switch and directory in the window; `mqs.cli version --download-dir` |
| kept on the device | *Ponechat záloh*, `--keep-backups` (pruned at the end of the run) | the same, pruned right after the copy, which also frees room for the packages |
| when it fails | `apply.failed.backup`, the run stops before the device is changed | the same code, the same stop — nothing is uploaded and nothing has rebooted |

The `.backup` file restores only on the RouterOS that wrote it, so across a major version the
readable `.rsc` export is what is left of the configuration; that is why both halves are
written and both are fetched.

It is a phase of its own and not a step inside `ApplyOrchestrator.run()`, because it ends by
**re-running discovery**: the version, the package list, the wireless stack, the switch chip
strings and the `flash/` layout all change under a cross-major hop, and the generator then works
from the device as it really is instead of from `TargetContext.after_update`. Guessing the target
version's capabilities is good enough for 7.20.7 → 7.23.7; it is not good enough for 7.x → 6.49.x,
where the ipv6 package, `MediaTek-MT7621` and the v6 `find` semantics all appear at once.

### 5.2 Installing a hop

- Upload every file of the hop to the **file root** (not `flash/`), as `_upload_update` does.
- `Install.BY_REBOOT`: `/system reboot`.
- `Install.BY_DOWNGRADE`: `/system package downgrade`. The command reboots the device itself, so
  no `/system reboot` follows it. Whether it asks a yes/no question over SSH exec and over the
  API is **NUTNO OVĚŘIT** — the reset and `/system routerboard upgrade` do not (verified on both
  devices), and `/console inspect request=child path=system,package,downgrade` is read first to
  see what parameters it takes, the way the RouterBOOT commands were checked.
- Both are treated like the existing reboot steps: `ConnectionLost` or a 10 s reply timeout means
  the device is going down, anything else is a failed step with the packages taken back off the
  device.

### 5.3 Verifying a hop

`check_routeros_version` already exists and is reused per hop. A hop whose version did not change
is a **hard stop**, not a warning: the leftover `.npk` is removed (a plain reboot would not
install an older package, but nothing is left lying around either) and the run ends before the
reset. The likely causes are named in the message: the question the command asked, the floor, or
a package RouterOS refused.

### 5.4 Reconnecting after a hop

Between hops nothing resets, so the device keeps its addresses and comes back where the session
was — `_reconnect_after_reboot` covers it. Two exceptions:

- **v6 → v7** converts the configuration, and **OVĚŘENO on the mmips board** (2026-09-27)
  it keeps the identity, the services and their ports, the addresses and the users, so the
  device answers where it did and the plain reconnect is enough.
- **v7 → v6** has no configuration conversion backwards, so the device may come back with its
  configuration partly or wholly gone, and then it is **not at the address it answered on**.
  The reconnect therefore takes the same ladder the post-reset reconnect uses — the profile's
  addresses, then the session port, then **connect by MAC** (MAC-Telnet / link-local), which is
  implemented and verified on the mmips board. A device that stays unreachable is reported as
  what it is: the version change went through, the apply did not, and Netinstall is the way back.
  The confirmation dialog says this *before* the reboot, not after it.
  On 2026-09-27 the mmips board did **not** need the ladder (**OVĚŘENO**): 7.23.7 → 6.49.21
  came back on the same 192.168.55.129:122 with the whole v6 configuration in place. That
  configuration was v6-made and untouched while v7 ran, so this says nothing about a device
  really configured on v7 — the warning stays, and so does the ladder behind it.

### 5.5 RouterBOOT

**`--routerboot-downgrade` was built after all** — on 2026-09-27, once the device was shown
to allow it — but as a switch of its own that nothing implies, not as part of the route. On 2026-09-27 RouterOS 6.49.12 ran on RouterBOOT 6.49.21 and booted normally, and
after a downgrade `upgrade-firmware` reads *older* than `current-firmware` (6.49.12 next to
6.49.21), so nothing is written. `/console inspect`, which the design meant to probe the
command with, does not exist on RouterOS 6 at all ("bad command name inspect").

What *is* built, since 2026-09-27, is the other half: **the version change ends with the same
*Aktualizovat RouterBOOT* step an apply ends with**. RouterBOOT comes with RouterOS, so a new
package leaves a new firmware image in `upgrade-firmware` — after the move to 7.23.7 the mmips
board offered 7.23.7 while still running RouterBOOT 6.49.21 — and before this the only way to
write it was a full *Reset + aplikovat*.

The step itself lives in `apply/routerboot.py` and both callers use it unchanged:

| Where | How it is asked for | When it runs |
|---|---|---|
| apply (`ApplyOrchestrator`) | the *Aktualizovat RouterBOOT* switch, `--routerboot`, `--mode reset` | last, after the configuration is verified |
| version change (`VersionChanger`) | the same switch; `mqs.cli version --routerboot` | once, after the **last** hop — an in-between version's firmware is of no interest |
| version change, v6 → v7 | nothing: part of the route (§2.2) | before the hop that crosses, when the board's firmware is behind the v6 it crosses from |
| nothing to install | `mqs.cli version --routerboot` with no `--to`, or `--to` the running version | on its own: one reboot, no configuration touched (`version.routerboot_pending` / `version.routerboot_none`) |
| going **down** on purpose | *Snížit RouterBOOT* next to the switch above, `--routerboot-downgrade` (refused without `--routerboot`) | the same step, allowed to write a firmware older than the running one: `apply.step.routerboot_downgrade` |

`RouterbootUpgrade` reads the state itself rather than trusting the inventory, decides from
`firmware_newer(upgrade, current)`, and reports through the caller's own event and check
machinery, so a downgrade ends with `apply.step.routerboot_current` and **no** extra reboot.
A refused write is a failed check, not a reboot. The plan says in advance what is coming
(`version.routerboot_after`, INFO) whenever the route goes up on a routerboard.

Two things follow from "the step belongs to whatever runs last":

- `apply --to X --routerboot` leaves it to the apply, not to the version change, or the
  firmware would be written before the reset instead of after it.
- Going **down** never writes firmware, so a board that went v7 → v6 keeps its v7 RouterBOOT.
  That is harmless: **OVĚŘENO** 2026-09-27, RouterOS 6.49.21 boots and runs normally under
  RouterBOOT 7.23.7, with its configuration, its address and its users.
- **A RouterBOOT can be written backwards**, which an earlier note here denied. `/system
  routerboard upgrade` writes whatever firmware the installed RouterOS carries, in either
  direction: **OVĚŘENO** 2026-09-27 on the same board — with RouterOS 6.49.21 installed under
  RouterBOOT 7.23.7 the command answered `Firmware upgraded successfully`, `current-firmware`
  stayed 7.23.7 until the reboot and read **6.49.21** after it.
- **Writing it down is therefore offered, on its own switch**: *Snížit RouterBOOT* /
  `--routerboot-downgrade`, default off, and refused on the command line without
  `--routerboot` because it says which way that step may write rather than being a step of
  its own. `RouterbootState.writable(allow_older=…)` is the whole decision; without it
  `firmware_newer()` alone still means a plain RouterOS downgrade leaves the bootloader
  where it is. The plan says so in advance (`version.routerboot_stays`, INFO) instead of
  leaving it to a silent skip at the end of the run.

## 6. Combining with "Reset + aplikovat"

- The reset is what makes the configuration match the target version, so a version change is
  normally run **with** it: `mqs.cli apply --to <version> --mode reset` walks the route, reads
  the device again and then applies the configuration generated for what came back.
- **Nothing is folded into the reset's reboot.** The design would have folded a single upgrade
  hop into it to save a reboot; the built code does not, because it would have meant two ways of
  installing one package and a plan that means different things in different modes. The update
  checkbox (`--update`) is still exactly that fold, unchanged and hardware-verified, and it is
  the cheaper answer whenever the newest version in the directory is what is wanted.
- A version change **without** an apply is a first-class use: the window's own button, or
  `mqs.cli version --to`. The device is left on the new version with the configuration it
  happened to have, which is what a downgrade before a manual job wants.

## 7. GUI

The Apply page's "RouterOS" group grows a third row under the existing update and RouterBOOT
switches:

```
[x] Aktualizovat RouterOS z adresáře Update
[ ] Přejít na jinou verzi RouterOS:  [ 6.49.21 (mmips, 10,1 MB) ▾ ]
    Nejnižší možná verze: 6.43.16 (factory firmware)
    Přechod: 6.49.12 → 6.49.21, jeden restart, pak reset a aplikace konfigurace
[x] Aktualizovat RouterBOOT
```

- The combo lists what `available_versions` found, blocked entries greyed out with their reason in
  the tooltip; the two switches are mutually exclusive.
- The line under it is the plan in words, with the floor underneath.
- A plan with `needs_confirmation` opens the reset dialog's sibling (`IdentityDialog`, now shared
  by both): current, target, the number of reboots, that the configuration is not carried over,
  and the identity typed by hand. An upgrade inside the major version asks a plain question.
- **The window runs the version change on its own button**, not as part of an apply: the combined
  flow lives in the CLI (`apply --to`), where the re-read between the two is straightforward.
  Afterwards the device page reads the device again and the user presses *Aplikovat*.

## 8. CLI

```
python -m mqs.cli version --host 192.168.55.68 --list
python -m mqs.cli version --host 192.168.55.68 --to 6.49.12 [--update-dir DIR]
                          [--download-dir DIR] [--keep-backups N]
                          [--routerboot-downgrade] [--confirm-identity NAME] [-y]
python -m mqs.cli apply --mode reset --to 7.23.5 ...   # version change, then the apply
```

`--list` prints the offers with their floor and their blocking reason and touches nothing.
`--to` without `-y` prints the plan and asks. `--to` with a version the planner refuses exits
non-zero with the message, before the device is touched.

## 9. Messages (`messages_cs.py`)

New codes, all `version.*` for the planner and `apply.step.version_*` for the steps:
`version.planned`, `version.two_hops`, `version.same`, `version.no_package`,
`version.unknown_arch`, `version.below_factory`, `version.no_factory_firmware`,
`version.v7_needs_649`, `version.v6_config_lost`, `version.extras_dropped`,
`version.extra_missing`, `version.no_ram`, `version.no_space`;
`apply.step.version_upload`, `apply.step.version_install`, `apply.step.version_verified`,
`apply.failed.version_install`, `apply.failed.version_not_installed`,
`apply.warning.version_unreachable`. Czech, in the wording the existing `update.*` messages use,
and none of them carries a character a cp1250 console cannot print (`cli.tolerate_unencodable`
covers it, but the RouterBOOT step already proved this is worth avoiding).

## 10. Tests

- `tests/test_version_change.py`, pure planner, built like `test_update.py` with `make_npk` and
  `make_inventory`: the floor (below, equal, above, unknown), the v6 → v7 gate (6.49.12 and
  6.49.21 → two hops, 6.49.22 → one, a hypothetical newer v6 → one, no 6.49.22 in the
  directory → refused), v7 → v6 (floor
  permitting and not), same version, wrong architecture, per-hop space on a `flash/` board and
  on one without, extras missing and extras dropped, and that `Install` comes out right for each
  direction.
- `tests/test_orchestrator.py` gains the runner against `fake_router.py`: two hops with two
  reboots, a hop that comes back on the old version (hard stop, file removed), a downgrade whose
  reconnect only succeeds by MAC, the folded single-upgrade case producing byte-identical
  behaviour to today's `--update`, and the packages taken back off the device when a hop fails.
- `test_discovery.py`: `factory-firmware` read into the inventory.
- `test_cli.py`: `version --list`, `--to` refused, `--to` accepted with `-y`.

## 11. Open questions and what has to be verified on hardware

Both questions the design asked were answered on 2026-09-27: the gate is a **minimum**, and
**v7 → v6 is offered**, gated and warned, with the MAC ladder of §5.4.

Answered on hardware the same day (log rows in `docs/DIALECT_NOTES.md`):

| # | What | Result |
|---|---|---|
| 1 | `factory-firmware` / `factory-software` on both boards | 6.42.4 on the mmips RB760iGS, 7.20.7 / 7.20 on the arm E60iUGS; both parse, the firmware field wins |
| 2 | `/system package downgrade` over SSH exec | no question, no output, the session drops in 1 s, one reboot, 84 s, the `.npk` deleted, the configuration untouched |
| 3 | the same command with nothing to install | **reboots anyway** (40 s), version unchanged — hence `_confirm_uploaded` |
| 4 | `/console inspect` on RouterOS 6 | does not exist, so nothing on v6 can be probed that way |
| 5 | a newer package by a plain `/system reboot`, no reset | installs, 102 s, the `.npk` deleted |
| 6 | RouterBOOT across a RouterOS downgrade | untouched, and `upgrade-firmware` then reads older than `current-firmware` |
| 7 | the whole feature through `mqs.cli version` | 6.49.21 → 6.49.12 and back, checklist OK both ways |
| 8 | 6.49.21 → 7.23.7 | one reboot, RouterOS converted the configuration, the device answered on the same address, one `routeros` package where the v6 bundle had eleven |
| 9 | 7.23.7 → 6.49.21 | one `/system package downgrade`, back on the same address with identity, services, addresses, bridge, users and the disabled `ipv6` package all in place; api-ssl refused the login once before SSH took over |
| 10 | the two-hop route 6.49.12 → 6.49.21 → 7.23.7 in one command | both hops walked in order, each uploaded only after the one before it had rebooted, each reconnected and verified on its own, two rows in the checklist; `version.planned` named both files and `2x restart` up front |
| 11c | the whole *Snížit RouterBOOT* switch, through the application | `version --to 7.23.7 --routerboot` installed v7 and wrote firmware 7.23.7 (two checklist rows), then `version --to 6.49.21 --routerboot --routerboot-downgrade` installed v6 and wrote firmware 6.49.21 back, logging `Snížení RouterBOOT 7.23.7 → 6.49.21 a restart`; afterwards there was nothing left to write |
| 11b | RouterBOOT back to 6.49.21 under RouterOS 6.49.21 | `/system routerboard upgrade` wrote the **older** firmware: `Firmware upgraded successfully`, unchanged until the reboot, 6.49.21 after it. The board is left on RouterOS 6.49.21 with RouterBOOT 6.49.21 and its configuration |
| 11a | writing the firmware across the major version, on its own | `version --routerboot` with no `--to` on a board left with `current-firmware 6.49.21` under RouterOS 7.23.7: `version.routerboot_pending`, one reboot, `OK RouterBOOT 7.23.7`, the configuration untouched |
| 13 | the release just below the moved gate: 6.49.21 → 6.49.22 → 7.23.7 (2026-09-28) | the same command that crossed in **one** hop the day before planned `2x restart` and walked both, each verified on its own. What 6.49.21 gets is the ordinary route of a release below the gate, on hardware as well as in the planner's tests |
| 12 | the moved gate, end to end: 6.49.12 → 6.49.22 → 7.23.7 (2026-09-28) | the plan named `routeros-mmips-6.49.22.npk` as the first hop with 6.49.21 sitting in the same directory, `2x restart` up front, both hops uploaded, installed, reconnected and verified on 192.168.55.254:122, the configuration converted on the way up and one `routeros 7.23.7` package at the end. Getting there took the way down first: 7.23.7 → 6.49.12 in one `/system package downgrade`, configuration intact |
| 11 | the version row **in the window**, not on the command line | the user's own 6.49.21 → 6.49.12 and then the two-hop route to 7.23.7 both ran from the *Přejít na jinou verzi RouterOS* row, with the same steps in the log and the device read again afterwards; the reconnect there also offers the profile's addresses (`192.168.55.129, 192.168.88.1`), which the CLI does not when it runs without a profile |
| 15 | the newest v6 in the Update directory as the stop, from a release that could cross by itself: 6.49.22 → 6.49.23 → 7.23.8 (2026-10-12) | the Update directory in its new layout (`<version>\<arch>\`, with 6.49.12, 6.49.22, 6.49.23, 7.23.8, 7.24.4 and every v6 split package next to its bundle) offered each v6 once. `version --to 7.23.8` from 6.49.22 planned `version.via_last_v6` and `2x restart` with `routeros-mmips-6.49.23.npk` first, then walked both hops over MAC-Telnet with 192.168.55.123 lent: backup fetched, sftp upload, reboot, reconnect, `OK RouterOS 6.49.23 (stable)`, then `OK RouterOS 7.23.8 (long-term)`, package parity OK after both. The board had come up from 6.49.21 to 6.49.22 the same way first. That first run also found a false FAIL: `ipv6`, switched off by the profile long before, counted as "waiting for a reboot"; the check now ignores a package that was disabled before the update |
| 16 | the newest v6 RouterBOOT before v7: 6.49.22 → 6.49.23 → 7.24.4 with RouterBOOT 6.49.22 (2026-10-12) | the board first taken from 7.24.4 down to 6.49.22 with `--routerboot --routerboot-downgrade`, so its firmware was behind the newest v6. `version --to 7.24.4 --routerboot` then planned `version.routerboot_before_v7` and walked: 6.49.23 by reboot and verified, `Aktualizace RouterBOOT 6.49.22 → 6.49.23` with its own reboot and `OK RouterBOOT 6.49.23`, only then the 7.24.4 upload and install, and the target firmware 7.24.4 at the end. Six checklist rows OK, four reboots, the board left where it was found |

| 14 | the backup the standalone route takes, on hardware (2026-09-28) | `version --to 6.49.21 --download-dir …` on the board at 192.168.55.254, running 6.49.12: `flash/pre-quickset-20260928-194704.backup` and `.rsc` written and fetched here (17 453 B and 1 443 B, the export headed `by RouterOS 6.49.12`, `model = RB760iGS`), two older backup files pruned, and only then the package, the reboot, the reconnect and `OK RouterOS 6.49.21`. Before this the same command saved nothing at all |

Still NUTNO OVĚŘIT, and the reason each is still open:

| # | What | Why it is not done |
|---|---|---|
| 1 | that the device itself refuses a package below `factory-firmware` | no package older than 6.42.4 is on this PC; the planner refuses it first either way |
| 2 | the MAC ladder of §5.4 *after a version change* | the one move that could have needed it (7.23.7 → v6) came back on its own address, so the ladder was never entered; it is verified after a reset, not after a downgrade |
| 3 | what v7 → v6 does to a configuration actually **made on v7** | the board's configuration was v6-made, so this run does not answer it; it would take configuring something v6 has no notion of and going back |
| 4 | why 7.23.7 on the mmips board lists no downloadable (`XA`) packages while 7.23.5 on the arm one lists seventeen | noticed in passing, and it only affects how many `extra_missing` warnings an update can raise |
| 5 | whether a device exists whose RouterBOOT **refuses** to go back (protected-routerboot, a newer board) | the one board here has `protected-routerboot: disabled` and wrote both ways; a refusal shows up as `apply.warning.routerboot` and a failed check, which is tested but not seen on hardware |

Each answer goes into `docs/DIALECT_NOTES.md` as a log row, as the update's did.

## 12. Deliberately out of scope

- **Netinstall.** It needs a serial console or a netboot server and a PC-side TFTP/BOOTP
  implementation; it is named in the messages as the way back, and nothing more.
- **Configuration migration between majors.** The application generates the configuration for the
  target version; that is the whole point of the reset that follows.
- **Downloading packages from MikroTik.** The Update directory is filled by hand, as today.
- **Beta and release candidates.** `read_npk` accepts final releases only, and this design does
  not change it.
