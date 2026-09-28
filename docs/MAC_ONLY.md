# Working with a device over MAC alone (design)

Status: **design only, nothing built** (written 2026-09-28). Everything below is a proposal
for how the application can run a whole session — commands *and* files — over the MAC-Telnet
console, without lending the device an IPv4 address and without the device having any usable
IP path at all. Rows marked **NUTNO OVĚŘIT** are guesses about RouterOS behaviour and must be
measured on the two test boards (`docs/DIALECT_NOTES.md`, "Device A" mmips / "Device B" arm)
before a line of it is written.

---

## 1. Where we are, and what the bootstrap costs

`transport/mac_connect.py` treats "connect by MAC" as a *bootstrap into an IP session*:
MNDP finds the device, MAC-Telnet logs in, `/ip address add … comment=mqs-temp` puts one of
this PC's addresses on the device, and from then on the real work runs over api-ssl or SSH to
that address. MAC-Telnet is used for the two bootstrap commands and (on v7) for files
(`mactelnet/files.py`); it is explicitly *not* a `Transport` (PLAN.md §10.2).

That has a price, and every item on this list is something already paid:

| cost | what it looked like |
|---|---|
| we write to the device before we have a session | the address is the first thing an unconfigured device gets from us |
| the lent address may not be free | 2026-09-28: a stranger held 192.168.55.254, the backup, the file list and the reset went to *another* MikroTik (`answers_for_mac` is the guard that was added) |
| the session's lifeline is the lent address | removing it kills the connection (`remove_temp_address` has to tolerate a missing answer), and a failure in between leaves it behind (`TempAddressLeft`) |
| it needs a free address in *our* subnet | a PC on 10.0.0.0/24 and a device on 192.168.88.0/24 have no common address to lend that the user will recognise as safe |
| v6 has no console file transfer | `/file add`, `/file read` and `:convert` are "bad command name" on 6.49.10 (verified 2026-09-25), so on v6 the address is not an optimisation, it is the only path |
| the device may have no IP path at all | mac-server allowed but api-ssl/SSH closed, firewalled, or the ports moved: the address is added and nothing answers on it |

So the address is load-bearing for three different jobs — carrying commands, carrying files,
and being findable again after a reset. This design separates the three and gives each one a
path that needs no address.

---

## 2. Part A — the console as a real `Transport`

The orchestrator only ever talks to the `Transport` protocol (`transport/base.py`: `execute`,
`execute_command`, `file_reference`, `reply_timeout`, `close`). `SshTransport` implements it
over a channel that carries **one CLI line in, one block of text out** — exactly what
`MacTelnetSession.run()` already is.

**Proposal: extract the CLI layer, give it two channels.**

```
transport/cli.py          CliTransport         — execute / execute_command / execute_raw /
                                                 file_reference, singleton menus, /log,
                                                 /console inspect, terse + key-value parsing
       │                        │               abstract: run(line) -> str
       ├── transport/ssh.py     SshTransport    — run() = paramiko exec_command
       └── transport/mactelnet/ MacTelnetTransport — run() = MacTelnetSession.run
```

Everything in `ssh.py:212-250` moves up unchanged, which is the point: the console then
answers `/system/resource/print`, `/interface/print`, `/ip/address/print`, `/export`,
`/import`, the rollback scheduler and the whole verify checklist **with the code that is
already verified on hardware**, because it is the same code.

What the console subclass has to add on top of the shared layer:

* **`confirm` semantics.** `MacTelnetSession.run(confirm=True)` demands the prompt back.
  Commands whose answer cannot come (`/system/reboot`, `/system/reset-configuration`, and
  removing an address we came in on — which the console never does, see §5) map to
  `confirm=False` plus a re-read of the device afterwards, which is the rule the bootstrap
  already follows.
* **`reply_timeout()`** sets the collect timeout for one command instead of the connection's.
* **`peer_security`** gains a value of its own. EC-SRP5 proves both ends know the password,
  but the terminal payloads this client sends and receives are **not encrypted** — `client.py`
  writes and reads them in clear and a 7.20.7 device accepts that. Everything typed into the
  console is readable on the local segment, so the UI must say so, and passwords must not be
  typed into it where another path exists (§4.1 makes that possible).
* **No parallelism, and a round trip per line.** `discovery.collect_inventory` issues on the
  order of 15–25 prints. Measure it first (§8, row 1); if it is too slow, an opt-in
  `run_many` that joins commands with `;` on one console line is the lever — NUTNO OVĚŘIT
  that `;` chaining works and that the outputs stay separable, on both majors.
* **No `/file` menu assumptions.** `file_reference()` returns the bare name, as SSH does.

Nothing above changes the orchestrator, the generator or the GUI: `open_mac_session` returns
a `DeviceSession` whose `transport` happens to be the console, and the apply runs.

---

## 3. Part B — files, split by what actually has to move

Three payloads move today, and they are not the same problem:

| payload | size | direction | needs a file on the device? |
|---|---|---|---|
| the generated `.rsc` | 5–40 KB | up | only because `/import` and `run-after-reset` take a file name |
| the backup + export | 20–200 KB | down | it is the safety copy on *this* PC |
| a RouterOS package | 8–20 MB | up | yes, a real file |

### 3.1 The script: stop making it a file

Two existing facts make this possible:

* `ApplyMode.IMPORT` already has `UploadStrategy.LINE_BY_LINE` — the orchestrator executes the
  IR item by item over `execute_raw`, no file at all (`orchestrator.py:343`). The console
  provides `execute_raw` via the shared CLI layer, so on the console this fallback simply
  works, on both majors.
* The *only* reason `RESET_AND_IMPORT` needs a file is `run-after-reset=`, and the only reason
  we need `run-after-reset` is that after a reset the device might be unreachable. **Over MAC
  it is not**: it is on the same segment, we know its MAC, and mac-server is on in the factory
  default. So the MAC-only reset becomes:

```
backup → reset (no run-after-reset, --keep-users as chosen) → wait for the board
       → MAC-Telnet login again (factory admin, or the kept user)
       → send the configuration line by line over the console
       → verify, still over the console
```

  This removes the file from the reset path on **every version**, which is what unblocks v6,
  where the console cannot write a file at all. The price is a window in which the device is
  reset but not yet configured — acceptable exactly because the way back in does not depend on
  its configuration. NUTNO OVĚŘIT, and it is the decisive experiment of this design: after
  `reset-configuration no-defaults=yes`, does the board still answer MNDP and MAC-Telnet?
  (§8, row 2. If it does not, the file path below is mandatory for no-defaults resets.)

* When a file is wanted anyway (v7, and it is nicer for auditing), the existing
  `MacTelnetStrategy` stays as it is, with one fix worth making: `/file add contents=…` is
  logged whole (2026-09-23, "Password in the device log"). With the script executed line by
  line the password never goes into a file at all; `/user set … password=` is logged as
  `(/user set admin)` without the value, so the line-by-line path is also the one that stops
  leaking it.

* v6 file writing, if it turns out to be needed: candidates to test, in this order —
  `/system script add source=…` + `/system script run` (no file, covers IMPORT), then the old
  `/file print file=x` (creates `x.txt`) + `/file set x.txt contents=$var` trick. Both NUTNO
  OVĚŘIT on 6.49.10, including the length limits and whether `/import` accepts the result.

### 3.2 Getting the configuration copy back here

* v7: `mactelnet/files.py` `read()` already does it — `/file read … as-value` +
  `:convert to=base64`, chunked, size-checked. It carries a binary `.backup` intact.
* v6: none of those commands exist. Proposal: **capture `/export` from the terminal** instead
  of downloading a file — the console prints it, the CLI layer already sanitises it, and the
  result is the same text the `.rsc` export file holds. The binary `.backup` stays on the
  device, which is where the rollback scheduler restores it from anyway; the user is told that
  over MAC on v6 the copy on this PC is the export, not the backup. NUTNO OVĚŘIT: a full
  export over the console on 6.49.10 — size, time, and whether anything is truncated or
  line-wrapped (the console is 80 columns wide whatever we ask for).
* Optional on v6: `:put [/file get [find name=…] contents]` for the export file, if `contents`
  is populated there — NUTNO OVĚŘIT, and only worth it if the terminal capture proves lossy.

### 3.3 Packages: the console cannot carry them, and should not pretend to

One console line carries ~800 characters, which is 270–800 bytes of payload after `\XX`
escaping, and costs one round trip. Even at an optimistic 100 lines per second that is tens of
KB/s; a 12 MB package is somewhere between 20 minutes and several hours, through a channel
with no flow control beyond ACK-per-packet. `MAX_UPLOAD = 64 KiB` is an honest limit and stays.

**A package needs an IP path. That is the one thing this design does not remove** — it only
makes sure an address is lent for the package alone, for as long as the transfer lasts, over a
console session that is not itself riding on it (§5). If no IP path can be had, the version
change and the update are refused *before* anything is touched, with a message that says why.

---

## 4. Part C — the address ladder

Ordered by how much they disturb the device. The application walks it downwards and stops at
the first rung that carries the job in hand:

| rung | path | writes on the device | needs |
|---|---|---|---|
| 0 | **MAC-Telnet console** (§2) | nothing | mac-server allowed on the interface |
| 1 | **IPv6 link-local** `fe80::…%ifindex` | nothing | ipv6 up on the device (rare on v6) |
| 2 | **an address on _our_ NIC, inside the device's own subnet** | nothing | the device has an IPv4 (MNDP reports it), and admin rights here |
| 3 | **an address lent to the device** (today's bootstrap) | `/ip address add comment=mqs-temp` | a free address in our subnet, confirmed by the user |

Rung 2 is new and is the direct answer to "without a temporary IP address": the device already
has an address it answers on — a factory 192.168.88.1, or whatever it was configured with —
and what is missing is only that *this PC* has no address in that subnet. Adding a secondary
address on our own NIC (Windows: `netsh interface ipv4 add address … store=active`, removed
again on close; elevation required, which the GUI must detect and explain) reaches the device
without touching it at all, and cannot collide with a stranger the way a lent address did.
It is checked before use the same way rung 3 is: connect, then `answers_for_mac()`.

Rung 3 keeps everything that was learned the hard way — the `answers_for_mac` guard, the
withdrawal over the console, `LentAddressStrategy` for a package on a link-local session — and
becomes rare: it is reached only for a package, or when rungs 0–2 all fail.

**What changes structurally:** today the console is opened *to set up* the IP session; in this
design the console *is* the session and an address is a temporary accelerator hanging off it.
That inverts the failure mode. The lent address can be withdrawn the moment the transfer ends,
because nothing depends on it; `TempAddressLeft` stops being a class of bug, since the console
that can take the address back is still logged in, and can log in again even after a reboot.

---

## 5. Part D — getting back in, without an address to go back to

`apply/reconnect.py` already carries a `MacRoute` (device, local NIC, address). Over a console
session the reconnect loses its hardest part: there is no address to predict, because the way
back in is MAC-Telnet on the same NIC. Two details:

* **Do not require a fresh MNDP announcement.** A `MacRoute` holds the MAC and the NIC, which
  is all `default_session_factory` needs. This matters because the application's own hardening
  block can narrow `/ip neighbor discovery-settings discover-interface-list`, so a device we
  just configured may stop announcing itself while MAC-Telnet still answers. (The generator
  never touches `/tool mac-server` — verified by reading it, no occurrence in `src/mqs`.)
* **A reboot is just a login that has to be retried.** The retry loop becomes "authenticate
  until the board answers", with the same budget as today's reconnect.

The device-side requirement for all of this is one line, and it must be in the checklist the
application shows before a MAC-only apply: `/tool mac-server` must allow the interface the
device is heard on, and it must stay allowed by the configuration being applied.

---

## 6. What this removes

* No address is written to a device before there is a session (the 2026-09-28 collision class).
* No address is left behind when something fails halfway (`TempAddressLeft`).
* No "free address in our subnet" question for the user in the common case.
* v6 by MAC stops being second-class: the script path (line by line) and the safety copy
  (export over the terminal) no longer depend on `/file` commands 6.49.10 does not have.
* A device whose IP services are closed, firewalled or moved is still fully configurable.

What it does not remove: a package still needs an IP path (§3.3), and everything still needs
mac-server on the interface.

---

## 7. What must be verified on hardware first

In this order; each row is one experiment, on both boards where the major matters.

| # | question | why it decides something |
|---|---|---|
| 1 | how long does `collect_inventory` take over the console, on v6 and v7? | if it is minutes, `run_many` (§2) is mandatory rather than an optimisation |
| 2 | after `/system/reset-configuration no-defaults=yes`, does the board answer MNDP and MAC-Telnet? and after `no-defaults=no`? | the whole file-less reset path (§3.1) stands or falls here |
| 3 | does `;` chaining of commands on one console line work, and stay parseable, on 6.49.10 and 7.23.x? | the only lever against the round-trip cost |
| 4 | a full `/export` captured from the console on 6.49.10: size, time, truncation, wrapping | decides the v6 safety copy (§3.2) |
| 5 | `/system script add source=…` + `run` on 6.49.10: source length limit, escaping | the v6 IMPORT path without a file |
| 6 | `/file print file=x` + `/file set x.txt contents=$var` on 6.49.10 | only needed if row 2 says a no-defaults reset needs `run-after-reset` |
| 7 | line-by-line apply of a full profile over the console, v7 then v6, timed | proves §3.1 end to end, and gives the progress estimate the GUI needs |
| 8 | a secondary address on this PC's NIC (rung 2): elevation, removal, route behaviour | decides whether rung 2 is offered or dropped |

Row 2 is the one to run first: if a reset board does not answer over MAC, this design shrinks
to "console for commands, file only on v7" and the v6 story keeps the lent address.

---

## 8. Work order

| step | scope | rough size |
|---|---|---|
| A1 | extract `CliTransport` from `SshTransport`, no behaviour change, tests stay green | 1 commit |
| A2 | `MacTelnetTransport` on top of `MacTelnetSession`, `peer_security`, timeouts | 1–2 commits |
| A3 | `open_mac_session` gains a "console only" route that skips the lend entirely | 1 commit |
| B1 | reconnect by MAC without a fresh MNDP announcement (§5) | 1 commit |
| B2 | file-less reset path (§3.1), gated on experiment 2 | 2 commits |
| B3 | v6 safety copy from `/export` over the terminal (§3.2) | 1 commit |
| C1 | the ladder (§4) as an explicit, logged choice, rung 3 only for packages | 1 commit |
| C2 | rung 2 (address on our own NIC), gated on experiment 8 | 2 commits |
| D | GUI: say which rung is in use, what it costs, and what mac-server must allow | 1 commit |

A1–A3 alone already give a MAC session that needs no address for anything but files, and
B2 is what makes the whole apply address-free.
