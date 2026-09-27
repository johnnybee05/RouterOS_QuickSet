# Changing the RouterOS version on a device (design)

Status: **implemented** (`apply/version_change.py`, `mqs.cli version`, the window's
*Přejít na jinou verzi RouterOS*) and verified on the mmips hEX S on 2026-09-27: both
directions inside RouterOS 6, and **6.49.21 to 7.23.7 across the major version**, where
RouterOS converted the configuration and the device came back on the same address. The way
back from v7 to v6 is the one path still unrun; §11 says what that leaves open. Where the design below
differs from what was built, the built thing is described here and the reason given.

Today the application can only move a device *forward inside its own major version*
(`apply/update.py`, `--update`): the packages are uploaded to the file root and the reset's
reboot installs them. Three things it cannot do, and this design adds them as one feature:

1. **Downgrade** — install a package older than the one running. A reboot never does this;
   it takes `/system package downgrade`, which reboots by itself.
2. **v6 → v7** — a move MikroTik allows only from the last v6 release.
3. **v7 → v6** — a downgrade across the major version, bounded by the board's factory
   firmware and, for most boards, impossible.

## 1. Why this is not "update with a `<` instead of a `>`"

| | update (today) | version change (this design) |
|---|---|---|
| target | implicit: the newest package in the directory for the device's major | **explicit: the user picks it**, a downgrade is never implicit |
| install | the reset's reboot, as a side effect | its own command, its own reboot, **before** any reset |
| hops | always one | one or two (v6 → 6.49.21 → v7) |
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

- **v6 → v7 only from 6.49.21 or newer.** Implemented as one constant,
  `V6_TO_V7_FROM = RosVersion(major=6, minor=49, patch=21)`, and read as a **minimum**, not as
  an exact version: a device on 6.49.22 goes straight to v7, it is not sent down to 6.49.21
  first (decided 2026-09-27).
- A device below the gate is not refused: the planner returns a **two-hop route**, first to the
  newest 6.49.x ≥ 6.49.21 in the Update directory, then to the v7 package. Without such a
  package in the directory it is refused with a message naming the file that is missing.
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
| 5 | v6 → v7 and current < `V6_TO_V7_FROM` → insert the 6.49.x hop | `version.v7_needs_649` when no such package is in the directory |
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
- **v7 → v6** has no configuration conversion backwards. The device may come back with its
  configuration partly or wholly gone, and then it is **not at the address it answered on**.
  The reconnect therefore takes the same ladder the post-reset reconnect uses — the profile's
  addresses, then the session port, then **connect by MAC** (MAC-Telnet / link-local), which is
  implemented and verified on the mmips board. A device that stays unreachable is reported as
  what it is: the version change went through, the apply did not, and Netinstall is the way back.
  The confirmation dialog says this *before* the reboot, not after it.

### 5.5 RouterBOOT

RouterBOOT is left alone, and **the `--routerboot-downgrade` option of the design was not
built**: it turned out not to be needed. On 2026-09-27 RouterOS 6.49.12 ran on RouterBOOT
6.49.21 and booted normally, and after the downgrade `upgrade-firmware` reads *older* than
`current-firmware` (6.49.12 next to 6.49.21), so `firmware_newer()` makes the existing
RouterBOOT step skip itself. `/console inspect`, which the design meant to probe the command
with, does not exist on RouterOS 6 at all ("bad command name inspect"). The `--routerboot`
upgrade keeps working unchanged and is still the natural last step of the v6 → v7 route.

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
  `make_inventory`: the floor (below, equal, above, unknown), the v6 → v7 gate (6.49.12 → two
  hops, 6.49.21 → one, 6.49.22 → one, no 6.49.x in the directory → refused), v7 → v6 (floor
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

Still NUTNO OVĚŘIT, and the reason each is still open:

| # | What | Why it is not done |
|---|---|---|
| 1 | that the device itself refuses a package below `factory-firmware` | no package older than 6.42.4 is on this PC; the planner refuses it first either way |
| 2 | v7 → v6 on the mmips board: where it comes back, and whether the MAC ladder reaches it | the risk is a test bed that needs Netinstall; worth doing deliberately |
| 3 | the two-hop route (6.49.12 → 6.49.21 → 7.23.7) end to end | both hops are verified on their own; only their sequence is not |
| 4 | why 7.23.7 on the mmips board lists no downloadable (`XA`) packages while 7.23.5 on the arm one lists seventeen | noticed in passing, and it only affects how many `extra_missing` warnings an update can raise |

Each answer goes into `docs/DIALECT_NOTES.md` as a log row, as the update's did.

## 12. Deliberately out of scope

- **Netinstall.** It needs a serial console or a netboot server and a PC-side TFTP/BOOTP
  implementation; it is named in the messages as the way back, and nothing more.
- **Configuration migration between majors.** The application generates the configuration for the
  target version; that is the whole point of the reset that follows.
- **Downloading packages from MikroTik.** The Update directory is filled by hand, as today.
- **Beta and release candidates.** `read_npk` accepts final releases only, and this design does
  not change it.
