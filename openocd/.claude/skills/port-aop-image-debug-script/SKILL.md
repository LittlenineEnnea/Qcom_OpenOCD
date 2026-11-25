---
name: port-aop-image-debug-script
description: Add AOP/AOSS (Always-On Processor / Always-On Subsystem) debug support to a Qualcomm chip target that doesn't have it yet, by donating the image/aop.py implementation from a target that already has a working one and verifying every config value against the new target's own sources. Use for requests like "add AOP support to <target>", "port AOSS debug to <target>", "why doesn't <target> have an aop.py yet", or "create the AOP image script for <target>". Also use proactively when a target directory under scripts/lldb_pkg/target/ is missing image/aop.py while its settings.json/launch.json/chip_config.xml already have AOSS entries staged, or when image/xbl.py is also missing (a common hidden prerequisite).
---

# Porting AOP/AOSS debug support to a new target

AOP (Always-On Processor, also called AOSS — Always-On Subsystem) is a small
core present on modern Qualcomm Snapdragon SoCs, on some chips implemented as
RISC-V and on others as Cortex-M, alongside sibling debug-capable subsystems
like SOCCP, TME, and CPUCP. Its debug script (`image/aop.py`, implementing
the `debug -d aoss` command family) follows one of a small number of known
shapes across every target that already has it, and the moment a new target
needs it, the correct move is almost never a from-scratch implementation —
it's donating an existing, working implementation and verifying every value
it depends on against the new target's own sources.

**There is no single fixed reference implementation — identify the right
donor fresh every time.** Grep every target under
`scripts/lldb_pkg/target/*/image/aop.py`, pick the one whose schema and flow
best match the new target using the criteria in "Donor selection" below, and
always re-derive facts directly from that donor's own files rather than
trusting a summary from an earlier port.

**Naming quirk to know up front:** the debug command/subsystem name is
`aoss` (`debug -d aoss`, `<arch><aoss ...>`, `AOSS_BUILD_PATH` etc. in
settings.json), but the Python module and function are `image/aop.py` /
`debugAOSS()`. The command dispatcher (`scripts/openocd_lldb.py`) hardcodes
exactly this one translation (`if image_module_name == "aoss": image_module_name = "aop"`)
before dynamically importing `lldb_pkg.target.{chip}.image.aop` — this is
chip-agnostic and needs no changes, but don't be confused by the mismatched
names when reading or writing code.

## What AOP/AOSS debug support consists of, end to end

For any target `<t>`, complete AOP support means all of the following exist
and are internally consistent:

1. `scripts/lldb_pkg/target/<t>/image/aop.py` — the `debugAOSS()` command
   itself.
2. `scripts/lldb_pkg/target/<t>/chip_config.xml` — a
   `<chipset name="<t>"><source_config><aoss func="..." end="/aop_proc/">`,
   an `<arch><aoss arch="riscv|cortex_m" gdbPort="...">` (the `arch`
   attribute is the donor-family discriminator — see "Donor selection"), and
   either a `<riscv_config><aoss_config>` block (RISC-V family) or an
   `<aop_config>` block (Cortex-M family) with elements matching whichever
   donor family applies.
3. `scripts/lldb_pkg/target/<t>/<t>.cfg` — for the RISC-V family: a
   dedicated `DAP.cxapb_aoss`-style `mem_ap` target (AOSS commonly sits
   behind its *own* access port, separate from the AP shared by other
   RISC-V subsystems like SOCCP/TME/CPUCP — don't assume it reuses the same
   AP number) plus a `target create $_CHIPNAME.aoss riscv -dap ... -ap-num
   $AP_NUM_APB_AOSS -gdb-port $GDB_PORT_AOSS -defer-examine` line and an
   `examine-end` event calling `arp_halt`. For the Cortex-M family, confirm
   what the donor's own `.cfg` does instead — it is not guaranteed to follow
   the same `mem_ap`-plus-`riscv` shape.
4. `scripts/lldb_pkg/target/<t>/settings.json` —
   `AOSS_BUILD_PATH`/`AOSS_SRC_PATH`/`AOSS_BIN_PATH` keys.
5. `scripts/lldb_pkg/target/<t>/launch.json` — for the two-step RISC-V
   family: an "AOSS Debug - STEP 1" and "AOSS Debug - STEP 2" launch-config
   pair. For the single-shot family: a single "AOP Debug" (or similarly
   named) config with no step split.
6. `scripts/lldb_pkg/target/<t>/image/xbl.py` — **not AOP-specific, but a
   hard, easy-to-miss dependency of the RISC-V two-step family's STEP 1**,
   which unconditionally calls `debugXBL()` imported from the same chip's
   own `image/xbl.py`. Check whether it already exists before assuming
   nothing needs to be written beyond `aop.py` itself — it is entirely
   plausible for a target to have `image/soccp.py`/`tme.py` already working
   while still missing `xbl.py`, especially if the target's own
   `launch.json` already stages an "XBL Debug" entry that nothing currently
   backs. If missing, port it too: it is normally chip-agnostic aside from
   one import line (its own `target_utils/pbl_load_fbc` import), and depends
   on `target_utils/pbl_load_fbc.py` / `target_utils/qreset.py` already
   existing for the target (they usually do if it has any other RISC-V
   subsystem support already).

**Audit all six independently before writing any code.** In practice,
several of these are frequently already staged — sometimes even already
correct — well before `image/aop.py` itself exists. Don't assume "no
aop.py" means nothing else exists either, and don't assume "no aop.py"
is the *only* gap — check `xbl.py` explicitly, every time.

## The two-step AOP/AOSS debug flow (RISC-V two-step family)

**STEP 1** (`debug -d aoss -s 1`) brings AOSS's RISC-V debug module up to
the point OpenOCD's `riscv` target driver can examine it, synchronized
against the target's own XBL/APSS boot flow rather than a hand-rolled
bring-up sequence:

1. Look up the `aoss_config` block (debug base address, `dmcontrol`
   offset/data, the AOP ELF name) in `chip_config.xml` for the current
   chipset.
2. `debugXBL(debugger, command, exe_ctx, result, "run")` — reuses the
   target's own already-working XBL/APSS flow to reach the point where
   AOSS's debug module can safely be armed.
3. Arm `dmcontrol` via two writes (via the dedicated `DAP.cxapb_aoss`-style
   mem_ap target) at `aoss_debug_base + dmcontrol.offset_addr`.
4. Connect to the APSS subsystem, set a one-shot (hardware) breakpoint at
   the shared halt symbol used elsewhere in this target's own boot-sync
   flow, continue, and wait for the hit.
5. `{CHIP}.aoss arp_examine`. The session then simply ends — STEP 2 is a
   fresh attach, with no detach step in between.

**STEP 2** (`debug -d aoss -s 2`) reads the AOP ELF filename from
`chip_config.xml`'s `<aoss_config><aop_elf name="...">`, resolves its path
via `g_dbgContext.openocdConfig.getBinaryPath("aop", elf_name)`, creates an
LLDB target from it, sets the source map, and issues `connect_remote -s
aoss`. Note that in at least one known-working donor, STEP 2 does **not**
wait on any breakpoint before declaring success — it logs a success message
immediately after the `connect_remote` command returns, with no
confirmation that the remote actually attached cleanly. This is a property
of that reference implementation, not something to silently "fix" while
porting (see "Known limitations" below).

## Donor selection — never assume the first implementation you find is the right one

Grep every target under `scripts/lldb_pkg/target/*/image/aop.py` before
starting. More than one incompatible flow can exist simultaneously in this
codebase — at least two families are known:

- **RISC-V two-step family**: `debug -d aoss -s 1` / `-s 2`, RISC-V only,
  hard-depends on the same chip's `image/xbl.py` for STEP 1's bring-up, arms
  `dmcontrol` via a dedicated AOSS-only mem_ap target, no `cortex_m`
  handling anywhere in the file. The dispatcher passes a step number
  positionally as this family's 6th parameter when `-s` is given — confirm
  by inspecting the function signature that this parameter is actually read
  and used to branch STEP 1 vs STEP 2 logic.
- **Single-shot, dual-architecture family**: no step split at all — one
  call does hard-reset/XBL bring-up, dmcontrol arm (RISC-V) or a `DEMCR`
  register write (Cortex-M), APSS breakpoint/continue/detach, then loads the
  AOP ELF and reconnects, all in one function. Reads
  `chip_config.xml`'s `<arch><aoss arch="...">` at runtime to decide which
  architecture branch to take, and supports both. If this family's
  equivalent step parameter exists in its signature at all, check whether
  the function body actually reads it anywhere — a family that accepts a
  step argument without ever branching on it does not actually support
  step-based debugging, regardless of what its docstring or the dispatcher
  contract implies.

**Check the new target's own launch.json/chip_config.xml conventions
first.** If the new target's *other* RISC-V subsystems (SOCCP, TME, CPUCP)
already use a STEP 1/STEP 2 launch-config pattern, that is strong evidence
the target's whole generation of tooling expects the two-step family for
AOSS too — don't introduce the single-shot family into an otherwise
step-based target's directory, even if the single-shot family happens to be
the first `aop.py` you find. Conversely, if the new target's own
`chip_config.xml` already has (or needs) an `<arch><aoss arch="cortex_m">`
entry, only the dual-architecture single-shot family can serve it — confirm
the real architecture from the new target's own vendor debug-tooling source
(see below) before assuming RISC-V by default.

**A target's real AOP debug-base address matching another target's address
is not sufficient evidence of anything about which family applies.**
SoC-wide debug-fabric placement addresses are often shared by convention
across many chips regardless of which debug-script architecture or even
which core architecture (RISC-V vs Cortex-M) they actually use. Family
choice is a question about the *Python flow, dispatch signature, and XML
schema*, not about address values.

## Verifying config values against the new target's own sources

Never trust a value just because it matches the donor's `chip_config.xml`
— that file is copy-pasted across targets more often than any other config
surface and is the **least** trustworthy source for a per-target value.
Ground-truth priority order, highest confidence first:

1. **The new target's own already-written `.cfg` file** — if it
   independently defines an AOSS debug-base variable (or equivalent), trust
   it over `chip_config.xml` when they disagree. `.cfg` files are typically
   hand-edited per target, not blindly copied.
2. **The new target's own vendor debug-tooling source** for the same
   register/purpose (its own low-level DAP-setup script for AOP/AOSS, its
   own chip-specific branch in a boot-flow orchestration script) — not the
   donor's copy. If the vendor-tooling tree has more than one flow variant
   for the same target (e.g. a real-silicon flow and a separate
   pre-silicon-emulation/RUMI-style flow), only the real-silicon variant is
   trustworthy ground truth for a real-hardware port.
3. **Corroboration from other, unrelated targets** that independently
   arrive at the same value the new target's own sources already point to.
4. **Absence from all of the above is itself a finding** — say so
   explicitly rather than defaulting to "no evidence against it, so leave
   it."

**A stale-looking file header is not, by itself, proof that the value
inside is wrong — and a clean header is not proof it's right.** It's
entirely possible for a per-target low-level debug-tooling file to carry
forward an unrelated or outdated `@file`/edit-history header while still
having had its actual numeric value correctly updated for the new target
(confirm this by checking whether *other*, unrelated per-target files with
the identical stale header pattern have values that are independently known
to be correct for the new target — if they do, header staleness alone
doesn't predict correctness either way). Judge the value on: whether it fits
the numeric pattern of the new target's other, independently-confirmed
debug-fabric addresses, and whether it's corroborated by a second,
independent source — not on how clean the surrounding comment looks.

**When the new target's own low-level source for this specific register is
missing or numerically implausible, look for a named/symbolic form of the
same register on the new target and cross-derive the address-space
transform from sibling targets.** AOP/AOSS's debug-fabric address has been
observed to live in a different address space than the same register's
own named/symbolic identifier (e.g. an SoC-wide HWIO-style symbol table
entry) used elsewhere in the same vendor-tooling tree — and the arithmetic
relationship between the two address spaces can be a target-family-wide
constant. If the new target's own tooling defines the named/symbolic form
(even if no low-level DAP-setup file resolves it to an address directly),
and at least two *other*, unrelated sibling targets each independently
provide **both** their own named/symbolic value **and** their own already
-confirmed-correct debug-fabric address for the same register, check whether
a single consistent transform (a fixed offset, an XOR mask, etc.) converts
one to the other across those known-good siblings. If it does, apply the
same transform to the new target's own named/symbolic value as a
best-evidence derivation — but label it explicitly as derived-by-analogy,
not hardware-confirmed, both in your own reporting and as an inline comment
in `chip_config.xml`, and recommend hardware/vendor-tooling confirmation
before relying on it for anything destructive.

## Dependency closure to confirm on the new target

- `utils.source_map.setSourceMap`, `utils.debug_context.g_dbgContext`/
  `logger`, `utils.stop_wait.wait_for_breakpoint_hit` — shared,
  chip-agnostic; should already work for any registered target with no
  changes.
- `image/xbl.py`'s `debugXBL()` — see "What AOP/AOSS debug support
  consists of" above; check for its existence explicitly, don't assume.
- **The one chip-specific line to change** in `aop.py`: the `debugXBL`
  import path, from the donor's package to the new target's own. This is
  the entire expected code diff beyond the file copy itself, for the
  RISC-V two-step family — chip-name resolution and every XML/ELF-path
  lookup are already chip-agnostic.
- Confirm the command dispatcher already lists `AOSS` as a valid subsystem
  and resolves `lldb_pkg.target.{chip}.image.aop` dynamically by chipset
  name (with the `aoss`->`aop` module-name translation noted above) — in a
  properly generalized setup this needs no change; verify rather than
  assume by reading the dispatcher's subsystem-name handling directly.

## Known limitations to carry forward, not silently fix

- In at least one known-working RISC-V two-step donor, STEP 2 does not wait
  on any breakpoint or connection-health check after issuing
  `connect_remote` before logging success — a silently-failed remote
  attach would be reported as success. This is a property of that
  reference implementation itself — port it as-is for fidelity to the
  proven-working donor; don't fix it mid-port. If asked to harden it, treat
  that as a separate, explicit follow-up, ideally applied to every target's
  `aop.py` at once for consistency.
- `getBinaryPath("aop", ...)` ultimately resolves through a build-output
  convention outside this repository. Confirm the new target's build
  produces an `"aop"` image bucket with the expected ELF naming, or set a
  manual binary-path override if it doesn't yet.
- The single-shot dual-architecture family predates the newer STEP1/STEP2
  launch-config convention used elsewhere in this codebase's more recently
  onboarded targets. If a target needs Cortex-M support specifically, that
  family is the only known option today — there is no known Cortex-M
  variant of the two-step family. Flag this as a real, named limitation
  rather than silently forcing the two-step shape onto a Cortex-M target.

## chip_config.xml editing convention

When correcting a wrong value, match the donor's plain style by default —
don't add an explanatory comment unless the target's own file has an
unambiguous, already-established convention for exactly this kind of
correction, or you're explicitly asked to document it. The one exception:
a value derived by analogy rather than direct confirmation (see the
address-space-transform guidance above) should always get an inline
comment stating the derivation and its confidence level — future readers
must not mistake "derived by analogy" for "hardware-confirmed." Ask if
genuinely unclear rather than guessing either way. Never let a literal `--`
(double hyphen) appear inside an XML comment body — it's illegal per the
XML spec and produces a parse error that can be far from the actual
mistake; use a semicolon, comma, or single hyphen instead, and always
re-parse the file after any comment edit.

## Validation

- `py_compile` the new `aop.py` (and `xbl.py`, if newly added);
  `xml.etree.ElementTree.parse` any edited `chip_config.xml`. Cheap,
  mandatory, and catch mechanical mistakes immediately — neither proves a
  value is hardware-correct, only that the file parses.
- This codebase's `settings.json`/`launch.json` files are JSONC (JSON with
  `//` line comments), so a plain `json.load` will reject them — that's
  expected, not a bug. If you write an ad hoc comment-stripping check to
  validate them, make it string-aware: a naive `//`-strips-to-end-of-line
  regex will misfire on legitimate string content containing `//`, most
  notably `"protocol-server start MCP listen://localhost:PORT"` lines that
  already exist in every target's `launch.json` — stripping mid-string
  content there produces a bogus "invalid control character" parse error
  that has nothing to do with your actual edit. Strip comments by matching
  whole string literals first (leaving them untouched) and only treating
  a bare `//` outside any string as a comment.
- A plain `python -c "import ..."` of the new module will typically fail on
  an unrelated `ModuleNotFoundError: No module named 'lldb'` outside the
  LLDB runtime — that's expected and not a sign of a real bug; `py_compile`
  is the right tool for a syntax-only check.
- Diff the new `aop.py` (and `xbl.py`, if added) against the donor file
  directly and confirm the only differences are the intended
  chip-specific import line(s) — treat any other difference, even
  cosmetic, as worth a second look.
- Before calling the port done, have an independent pass re-derive every
  `aoss_config`/`aop_config` value directly from the new target's own raw
  sources (not from a summary of earlier findings), and confirm the
  donor-family choice against the architecture/flow-signature evidence
  above, not address-value similarity.
- State plainly which values are independently confirmed versus
  derived-by-analogy/plausible-but-unverified for the new target. A clean
  syntax/XML check must not be read as hardware validation.

## Output checklist

- [ ] Every existing `aop.py` across all targets was found and the donor
  family (RISC-V two-step vs. single-shot dual-architecture — see "Donor
  selection") was matched against the new target's own launch-config
  convention and real core architecture, not assumed from whichever donor
  was found first.
- [ ] `image/xbl.py` was explicitly checked for existence on the new
  target (RISC-V two-step family only) — not assumed present just because
  other RISC-V subsystems already work.
- [ ] All `aoss_config`/`aop_config` elements are present in the new
  target's `chip_config.xml`, each labeled confirmed-with-source,
  derived-by-analogy-with-documented-reasoning, or explicitly flagged
  unverified.
- [ ] The AOSS debug-base address was specifically cross-checked against
  the new target's own `.cfg` file and its own vendor debug-tooling
  source, with a documented fallback derivation (and confidence label) if
  neither directly resolved it.
- [ ] `settings.json`, `launch.json`, the `.cfg` AOSS target-create
  line(s), and the `image/xbl.py` prerequisite were each independently
  checked for what's already staged, before writing anything.
- [ ] The command dispatcher's `aoss`->`aop` module-name translation was
  confirmed to need no changes (generic dispatch verified, not assumed).
- [ ] The new `aop.py` (and `xbl.py`, if added) is the donor file plus
  only the intended chip-specific import line(s) — no unrelated changes,
  confirmed by direct diff.
- [ ] Syntax check (Python) and well-formedness check (XML/JSONC, using a
  string-aware comment strip) both pass.
- [ ] An independent review pass re-derived the value claims and the
  donor-family choice from the new target's raw sources.

## Related skills

- `port-soccp-image-debug-script`, `port-tme-image-debug-script` — the
  same donation/verification pattern for sibling RISC-V subsystems; useful
  side-by-side reading since AOP/AOSS's own two donor families deliberately
  diverge from (and from each other in) STEP-split convention and
  architecture support in ways SOCCP/TME's donor families don't.
- `qreset-bring-core-out-of-reset` — the same verification discipline,
  scoped to a target's reset-helper/core-bring-up script pair rather than
  an image-debug script.
- `reorganize-image-scripts` — restructuring/consolidating image scripts
  across targets; a different operation from porting AOP to one new
  target.
- `loadbuild-cmm-to-python`, `convert-cmm-script` — useful when the new
  target's own vendor-tooling source needs to be read closely to resolve
  the donor-family or address-verification questions above.
