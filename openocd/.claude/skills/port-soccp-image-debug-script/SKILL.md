---
name: port-soccp-image-debug-script
description: Add SOCCP debug support to a Qualcomm chip target that doesn't have it yet, by donating the image/soccp.py implementation from a target that already has a working one and verifying every config value against the new target's own sources. Use for requests like "add SOCCP support to <target>", "port SOCCP debug to <target>", "why doesn't <target> have a soccp.py yet", or "create the SOCCP image script for <target>". Also use proactively when a target directory under scripts/lldb_pkg/target/ is missing image/soccp.py while its settings.json/launch.json/chip_config.xml already have SOCCP entries staged.
---

# Porting SOCCP debug support to a new target

SOCCP is a RISC-V debug core present on modern Qualcomm Snapdragon SoCs, alongside sibling RISC-V subsystems like TME and CPUCP. Its debug script (`image/soccp.py`, implementing `debug -d soccp -s 1` / `debug -d soccp -s 2`) follows the same general shape across every target that already has a working one, and the moment a new target needs it, the correct move is almost never a from-scratch implementation — it's donating an existing, working implementation and verifying every value it depends on against the new target's own sources.

**There is no single fixed reference implementation — identify the right donor fresh every time.** Grep every target under `scripts/lldb_pkg/target/*/image/soccp.py`, pick the one whose schema and flow best match the new target using the criteria in "Donor selection" below, and always re-derive facts directly from that donor's own files rather than trusting a summary from an earlier port — the best donor to copy from can change as more targets get ported.

## What SOCCP debug support consists of, end to end

For any target `<t>`, complete SOCCP support means all of the following exist and are internally consistent:

1. `scripts/lldb_pkg/target/<t>/image/soccp.py` — the `debugSOCCP()` command itself.
2. `scripts/lldb_pkg/target/<t>/chip_config.xml` — a `<chipset name="<t>"><source_config><soccp func="main" end="/soccp_proc/">`, an `<arch><soccp arch="riscv" gdbPort="...">`, and a `<riscv_config><soccp_config>` block. The modern schema (see "Donor selection") has three elements:
   - `soccp_debug_base` (`addr`) — the SOCCP core's APB debug-window base address.
   - `dmcontrol` (`offset_addr`, `dmcontrol_data_1`, `dmcontrol_data_2`) — the RISC-V debug-module arm sequence, applied at `soccp_debug_base + offset_addr`.
   - `soccp_dbg_clocks` — a list of `<clock name= addr= val=>` entries: every debug/APB/ATB/trigger/timestamp clock-enable register that must be written before `dmcontrol` can be armed. **The presence of this element is the donor-family discriminator** — see "Donor selection".
3. `scripts/lldb_pkg/target/<t>/<t>.cfg` — a `target create $_CHIPNAME.soccp riscv -dap $_CHIPNAME.dap -dbgbase $DBG_BASE_SOCCP_HART0 -ap-num $AP_NUM_APB -gdb-port $GDB_PORT_SOCCP -defer-examine` line plus an `examine-end` event calling `arp_halt`, using the same shared `DAP.cxapb`/`DAP.cxaxi` mem_ap targets already created for other RISC-V subsystems (CPUCP, TME) on the same target.
4. `scripts/lldb_pkg/target/<t>/settings.json` — `SOCCP_BUILD_PATH`/`SOCCP_SRC_PATH`/`SOCCP_BIN_PATH` keys.
5. `scripts/lldb_pkg/target/<t>/launch.json` — a "SOCCP Debug - STEP 1" and "SOCCP Debug - STEP 2" launch config pair.
6. `scripts/lldb_pkg/target/<t>/image/xbl.py` — not SOCCP-specific, but a load-bearing dependency of the modern `soccp.py` (see below): check whether it already exists before assuming it needs to be created (it usually does if `<t>` already has any XBL support at all).

**Audit all six independently before writing any code.** In practice, several of these are frequently already staged — sometimes even already correct — well before `image/soccp.py` itself exists. Don't assume "no soccp.py" means nothing else exists either.

## The two-step SOCCP debug flow (modern schema)

**STEP 1** (`debug -d soccp -s 1`) brings SOCCP's RISC-V debug module up to the point OpenOCD's `riscv` target driver can examine it, piggy-backing on the same target's own XBL debug flow rather than hand-rolling a separate PBL bring-up sequence (contrast this with TME's own STEP 1, which hand-rolls two APSS PBL breakpoints itself — SOCCP's donor delegates that whole job to the target's existing `debugXBL()`):

1. Look up `soccp_config` (base address, `dmcontrol` offset/data, the debug-clock list) in `chip_config.xml` for the current chipset.
2. `debugXBL(debugger, command, exe_ctx, result, "run")` — reuses the target's own already-working XBL/APSS flow to reach the point (in the known implementation, a TZ/XBL-side halt symbol, not a SOCCP-side one) where SOCCP's debug module can safely be armed.
3. Enable every debug clock in the `soccp_dbg_clocks` list via `DAP.cxaxi mww` writes (a short sleep between each).
4. Arm `dmcontrol` via two `DAP.cxapb mww` writes at `soccp_debug_base + dmcontrol.offset_addr`.
5. Set a one-shot breakpoint at the halt symbol and continue past it, then delete the breakpoint.
6. LLDB settings tweaks (auto-confirm, a long gdb-remote packet timeout).
7. `{CHIP}.soccp arp_examine`. No detach — the session simply ends; STEP 2 is a fresh attach.

**STEP 2** (`debug -d soccp -s 2`) picks the SOCCP ELF by a naming convention built from the chipset name, resolves its path via `g_dbgContext.openocdConfig.getImageBasePath("soccp")`, creates an LLDB target from it, resolves its `__start` symbol via a hand-rolled module/symbol walk (no live process needed — it only needs the ELF's own symbol table), then forces the program counter via a **raw telnet client** command rather than an LLDB breakpoint — a different mechanism than TME's STEP 2, which plants a real one-shot breakpoint at a config-supplied entry address. Don't assume every subsystem's STEP 2 uses the same PC-setting mechanism; confirm which one the actual donor uses. Finally sets the source map and issues `connect_remote -s soccp`.

## Donor selection — never assume the first implementation you find is the right one

Grep every target under `scripts/lldb_pkg/target/*/image/soccp.py` before starting. More than one incompatible flow can exist simultaneously in this codebase — at least two families are known:

- **Modern family** (described above): config-driven debug-clock list, delegates entirely to the target's own `debugXBL()` to reach the halt point, uses a modern telnet-client helper for STEP 2's PC-forcing, two independent single-purpose LLDB sessions (STEP 1 / STEP 2) with no detach in between.
- **Legacy family**: a single-shot flow (attach → XBL → dmcontrol toggle → arp_examine → load ELF → explicit `detach` → reattach/`connect_remote`) built on a deprecated telnet helper, with no debug-clock-enable modeling at all.

**Check the new target's own `chip_config.xml` first** — if a `soccp_config` block already exists, check for a `soccp_dbg_clocks` child element. Its presence means the modern family's schema is already assumed; its absence (or the whole block's absence) means you're starting from scratch or dealing with a legacy-family remnant. **Only the modern family should be used as a donor for a new port** — the legacy family predates the current architecture and doesn't model the debug-clock-enable requirement the modern schema (and, per the target's own vendor debug-tooling scripts, the real hardware) needs. Copying a legacy-family file forward would silently drop that requirement.

**A target's real SOCCP debug-base address matching another target's address is not sufficient evidence of anything about which family applies.** SoC-wide debug-fabric placement addresses are often shared by convention across many chips regardless of which debug-script architecture they actually use. Family choice is a question about the *Python flow and XML schema*, not about address values.

## Verifying config values against the new target's own sources

Never trust a value just because it matches the donor's `chip_config.xml` — that file is copy-pasted across targets more often than any other config surface and is the **least** trustworthy source for a per-target value. Ground-truth priority order, highest confidence first:

1. **The new target's own already-written `.cfg` file** — if it independently defines `DBG_BASE_SOCCP_HART0` (or equivalent), trust it over `chip_config.xml` when they disagree. `.cfg` files are typically hand-edited per target, not blindly copied.
2. **The new target's own vendor debug-tooling source** for the same register/purpose (its own `DAP_SOCCP.cmm`-equivalent script, its own chip-specific SOCCP-enable block in a `run_fbc.cmm`-equivalent script). If the vendor-tooling tree has more than one flow variant for the same target (e.g. a real-silicon flow and a separate pre-silicon-emulation/RUMI-style flow), only the real-silicon variant is a trustworthy ground truth for a real-hardware port — pre-silicon variants can use deliberately simplified or stale addresses.
3. **Corroboration from other, unrelated targets** that independently arrive at the same value the new target's own sources already point to.
4. **Absence from all of the above is itself a finding** — say so explicitly rather than defaulting to "no evidence against it, so leave it."

`soccp_debug_base` specifically is the highest-historical-risk value: it has been found stale (carrying a donor's value instead of the new target's own) in a case where the target's own `.cfg` file and its own vendor DAP-setup script both already independently agreed on the correct value, while only `chip_config.xml` still had the copy-paste bug.

**A value being identical across every known donor — including one that's "confirmed working on real hardware" — is still not proof of correctness.** The debug-clock addresses in `soccp_dbg_clocks` are the sharpest known example: when an independent register-map/HWIO source is available for the new target, cross-check the clock addresses against it specifically, even when they match every existing donor's `chip_config.xml` exactly. A write to a slightly-wrong-but-inert clock-enable address can go unnoticed indefinitely if nothing downstream strictly depends on that specific bit being set. If the cross-check disagrees with every existing donor at once, that is itself a signal the discrepancy predates this port and belongs to the donor(s) too — don't silently "fix" it as part of a routine port; flag it for hardware/SME review instead, and leave the value as documented-but-unresolved unless you're specifically asked to correct it.

## Dependency closure to confirm on the new target

- `utils.telnet_client.TelnetClient`, `utils.source_map.setSourceMap`, `utils.debug_context.g_dbgContext`/`logger` — shared, chip-agnostic; should already work for any registered target with no changes.
- `image/xbl.py`'s `debugXBL()` — not SOCCP-specific, but load-bearing for STEP 1. Confirm it already exists for the new target (and, if you need to trace further, confirm its own dependencies already exist too) before assuming anything needs to be written beyond `soccp.py` itself.
- **The one chip-specific line to change**: `from lldb_pkg.target.<donor>.image.xbl import debugXBL` → `from lldb_pkg.target.<new_target>.image.xbl import debugXBL`. This is the entire expected code diff beyond the file copy itself — confirmed on more than one port of a sibling RISC-V subsystem's image script between two targets, where the diff was likewise exactly one import line, because chip-name resolution (`g_dbgContext.chipsetName.lower()`) and every XML/ELF-path lookup are already chip-agnostic.
- Confirm the command dispatcher already lists `"SOCCP"` as a valid subsystem and resolves `lldb_pkg.target.{chip}.image.soccp` dynamically by chipset name — in a properly generalized setup this needs no change; verify rather than assume by checking the dispatcher doesn't hardcode any chip name.

## Known limitations to carry forward, not silently fix

- The modern family's STEP 1 continues past its halt-symbol breakpoint with a plain, synchronous `HandleCommand("c")` — no `SetAsync(True)`/`Continue()`/timeout/stop-reason check, unlike this framework's documented best-practice async-continue pattern used one call earlier (inside the delegated `debugXBL()`) and by sibling subsystems' own STEP 1. A missed breakpoint hangs the session indefinitely with no diagnostic. This is a property of the reference implementation itself — port it as-is for fidelity to the proven-working donor; don't fix it mid-port. If asked to harden it, treat that as a separate, explicit follow-up, ideally applied to every target's `soccp.py` at once for consistency.
- `getImageBasePath("soccp")` ultimately resolves through a build-output convention outside this repository. Confirm the new target's build produces a `"soccp"` image bucket with the expected ELF naming, or set a manual binary-path override (`SOCCP_BUILD_PATH`/equivalent) if it doesn't yet.

## chip_config.xml editing convention

When correcting a wrong value, match the donor's plain style by default — don't add an explanatory comment unless the target's own file has an unambiguous, already-established convention for exactly this kind of correction, or you're explicitly asked to document it. Ask if genuinely unclear rather than guessing either way.

## Validation

- `py_compile` the new `soccp.py`; `xml.etree.ElementTree.parse` any edited `chip_config.xml`. Cheap, mandatory, and catch mechanical mistakes immediately — neither proves a value is hardware-correct, only that the file parses.
- A plain `python -c "import ..."` of the new module will typically fail on an unrelated `ModuleNotFoundError: No module named 'lldb'` outside the LLDB runtime — that's expected and not a sign of a real bug; `py_compile` is the right tool for a syntax-only check.
- Before calling the port done, have an independent pass re-derive every `soccp_config` value directly from the new target's own raw sources (not from a summary of earlier findings), and confirm the donor-family choice against the `soccp_dbg_clocks`-presence signal above.
- State plainly which values are independently confirmed versus plausible-but-unverified for the new target. A clean syntax/XML check must not be read as hardware validation.

## Output checklist

- [ ] Every existing `soccp.py` across all targets was found and the donor family (modern vs. legacy — see Donor selection) was matched against the new target's own `chip_config.xml` schema, not assumed from whichever donor was found first.
- [ ] All `soccp_config` elements (`soccp_debug_base`, `dmcontrol`, `soccp_dbg_clocks`) are present in the new target's `chip_config.xml`, each labeled confirmed-with-source or explicitly flagged unverified.
- [ ] `soccp_debug_base` was specifically cross-checked against the new target's own `.cfg` file and its own vendor DAP-setup script (real-silicon flow, not a pre-silicon/RUMI-style variant).
- [ ] `soccp_dbg_clocks` addresses were checked against a raw register-map/HWIO source where one is available, not just against the donor's `chip_config.xml` — even if the donor is "confirmed working."
- [ ] `settings.json`, `launch.json`, the `.cfg` SOCCP target-create line, and the `image/xbl.py` prerequisite were each independently checked for what's already staged, before writing anything.
- [ ] The command dispatcher was confirmed to need no changes (generic dispatch verified, not assumed).
- [ ] The new `soccp.py` is the donor file plus only the one chip-specific import line — no unrelated changes.
- [ ] Syntax check (Python) and well-formedness check (XML) both pass.
- [ ] An independent review pass re-derived the value claims and the donor-family choice from the new target's raw sources.

## Related skills

- `port-tme-image-debug-script` — the same donation/verification pattern for TME instead of SOCCP; useful side-by-side reading since the two subsystems' donor implementations deliberately diverge in STEP 1 bring-up style and STEP 2 PC-setting mechanism.
- `qreset-bring-core-out-of-reset` — the same verification discipline, scoped to a target's reset-helper/core-bring-up script pair rather than a RISC-V subsystem image script.
- `reorganize-image-scripts` — restructuring/consolidating image scripts across targets; a different operation from porting SOCCP to one new target.
- `loadbuild-cmm-to-python`, `convert-cmm-script` — useful when the new target's own vendor-tooling source needs to be read closely to resolve the donor-family or address-verification questions above.
