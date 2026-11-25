---
name: port-tme-image-debug-script
description: Add TME (Trust Management Engine) debug support to a Qualcomm chip target that doesn't have it yet, by donating the image/tme.py implementation from a target that already has one and verifying every config value against the new target's own sources. Use for requests like "add TME support to <target>", "port TME debug to <target>", "why doesn't <target> have a tme.py yet", or "create the TME image script for <target>". Also use proactively when a target directory under scripts/lldb_pkg/target/ is missing image/tme.py while its settings.json/launch.json/chip_config.xml already have TME entries staged.
---

# Porting TME debug support to a new target

TME (Trust Management Engine) is a small RISC-V security core present on modern Qualcomm Snapdragon SoCs. Its debug script (`image/tme.py`, implementing `debug -d tme -s 1` / `debug -d tme -i <tme_fmw|mrom> -s 2`) follows the same shape across every target that already has it, and the moment a new target needs it, the correct move is almost never a from-scratch implementation — it's donating an existing, working implementation and verifying every value it depends on against the new target's own sources.

**There is no single fixed reference implementation — identify the right donor fresh every time.** Grep every target under `scripts/lldb_pkg/target/*/image/tme.py`, pick the one whose schema and flow best match the new target using the criteria in "Donor selection" below, and always re-derive facts directly from that donor's own files rather than trusting a summary from an earlier port — the best donor to copy from can change as more targets get ported; a later copy is sometimes cleaner than the one it was copied from.

## What TME debug support consists of, end to end

For any target `<t>`, complete TME support means all of the following exist and are internally consistent:

1. `scripts/lldb_pkg/target/<t>/image/tme.py` — the `debugTME()` command itself.
2. `scripts/lldb_pkg/target/<t>/chip_config.xml` — a `<chipset name="<t>"><source_config><tme func="tmeVerifySignature" end="/tme_proc/">`, an `<arch><tme arch="riscv" gdbPort="3102">`, and a `<riscv_config><tme_config>` block matching one of (at least) two known schema variants — see "Donor selection" below to tell which applies. One variant (Family A) has exactly these seven elements:
   - `tme_debug_base` (`addr`) — the TME core's APB debug-window base address.
   - `dmcontrol` (`offset_addr`, `dmcontrol_data_1`, `dmcontrol_data_2`) — the RISC-V debug-module arm sequence, applied at `tme_debug_base + offset_addr`.
   - `apss_pbl_1st_break_point` / `apss_pbl_2nd_break_point` (`addr`) — addresses *inside the APSS PBL ROM*, not TME's own address space; TME's debug-module arm is synchronized against APSS reaching these two points.
   - `tme_qtmr_qtmr_v1_cntp_ctl_f0` (`addr`, `val`) — disables TME's PBL-exit/SEQ timer once TME has been examined.
   - `tme_mrom_entry_point` / `tme_fw_entry_point` (`addr`) — entry-point addresses for the two loadable images (mission-ROM and firmware).
3. `scripts/lldb_pkg/target/<t>/<t>.cfg` — a `target create $_CHIPNAME.tme riscv -dap $_CHIPNAME.dap -dbgbase $DBG_BASE_TME_HART0 -ap-num $AP_NUM_APB -gdb-port $GDB_PORT_TME -defer-examine` line plus an `examine-end` event calling `arp_halt`, using the shared `DAP.cxapb`/`DAP.cxaxi` mem_ap targets already created for other RISC-V subsystems (CPUCP, SOCCP) on the same target.
4. `scripts/lldb_pkg/target/<t>/settings.json` — `TME_BUILD_PATH`/`TME_SRC_PATH`/`TME_BIN_PATH`/`TME_ELF_TO_DEBUG` keys.
5. `scripts/lldb_pkg/target/<t>/launch.json` — a "TME Debug - STEP 1" and "TME Debug - STEP 2" launch config pair, mirroring the SOCCP two-step pattern.
6. `scripts/lldb_pkg/target/<t>/target_utils/pbl_load_fbc.py` and `.../qreset.py` — not TME-specific, but load-bearing dependencies of `tme.py`; check whether they already exist before assuming they need to be created (they're usually already staged if `<t>` already has any other RISC-V-subsystem or XBL support).

**Audit all six independently before writing any code.** In practice, several of these are frequently already staged — sometimes even already correct — well before `image/tme.py` itself exists. Don't assume "no tme.py" means nothing else exists either.

## The two-step TME debug flow

**STEP 1** (`debug -d tme -s 1`) synchronizes TME's RISC-V debug-module arm against APSS's own PBL execution, before OpenOCD's `riscv` target driver has examined TME at all:

1. `ResetCore(debugger)`, then `pbl_load_fbc()` — generic, shared bring-up helpers; brings CPUCP out of PBL ROM reset and releases APSS's PBL-exit breakpoint.
2. `connect_remote -s apss3`, then a one-shot breakpoint at `apss_pbl_1st_break_point`, `Continue()`, wait for hit.
3. Two raw `DAP.cxapb mww {tme_debug_base + dmcontrol.offset_addr} {value}` writes — `dmcontrol_data_1` then `dmcontrol_data_2` — arming TME's debug module for halt-on-reset via the generic APB mem_ap target, independent of the not-yet-examined `$_CHIPNAME.tme` object.
4. A second one-shot breakpoint at `apss_pbl_2nd_break_point`, `Continue()`, wait for hit.
5. `{CHIP}.tme arp_examine` (OpenOCD now takes over via the standard RISC-V DM protocol), then a `write_memory` disabling the QTMR/SEQ-exit timer.

**STEP 2** (`debug -d tme -i <tme_fmw|mrom> -s 2`) picks the ELF (`firmware.elf` for `tme_fmw`; a dynamically-named `tme{Chip}MissionRom.elf` for `mrom` — built as `f"tme{chip_name.capitalize()}MissionRom.elf"`, not read from XML), resolves its path via `g_dbgContext.openocdConfig.getBinaryPath("tme", elf_name)`, creates an LLDB target, sets the source map, `connect_remote -s tme`, and plants a one-shot breakpoint at the image's entry point.

## Donor selection — never assume the first implementation you find is the right one

Grep every target under `scripts/lldb_pkg/target/*/image/tme.py` before starting. More than one incompatible flow can exist simultaneously in this codebase — at least two families are known:

- **Family A** (the seven-element schema described above): `ResetCore()`+`pbl_load_fbc()` bring-up, APSS subsystem name `"apss3"`, monitor prefix `"DAP.cxapb"`, ELF resolved via `getBinaryPath("tme", ...)`.
- **Family B**: direct `qreset`/`dap_access` commands instead of `ResetCore()`+`pbl_load_fbc()`, APSS subsystem name `"apss"` instead of `"apss3"`, monitor prefix `"DAP.cxapb_tme"` instead of `"DAP.cxapb"`, three extra required `chip_config.xml` elements not in Family A's schema (`tme_seq_init_post_ram_load_apps`, `pbl_auth_hash_seg`, `pause_point_reg`), and ELF resolved via `getBinaryPath("boot", ...)` instead.

**Check the new target's existing `chip_config.xml` schema and `<arch>` naming first** — if it already has an `apss3` arch entry and (if a `tme_config` block already exists) exactly the seven-element schema above, it belongs to Family A. If it uses `apss` and/or already has the three extra elements, it belongs to Family B, and copying a Family A donor's `tme.py` would fail immediately (missing elements it doesn't need, `connect_remote -s apss3` against a target with no `apss3` entry, etc.).

**If the new target's own real TME debug-base address happens to match a different family's address value, that is not sufficient evidence to switch donors.** SoC-wide debug-fabric placement addresses (this one included) are often shared by convention across many chips regardless of which boot/PBL flow they actually use — this was confirmed exactly once already (a target's real address matched one family while its actual working flow, per its own vendor debug-tooling source, matched the other). **The decisive evidence is the new target's own vendor debug-tooling script** (a T32 `.cmm` equivalent, e.g. a chip-specific branch in a `go_tme.cmm`-style script) if one already exists for it — match its breakpoint offsets and register sequence against each donor candidate; whichever it mirrors is the correct family, regardless of address-family coincidence.

## Verifying config values against the new target's own sources

Never trust a value just because it matches the donor's `chip_config.xml` — that file is copy-pasted across targets more often than any other config surface and is the **least** trustworthy source for a per-target value. Ground-truth priority order, highest confidence first:

1. **The new target's own already-written `.cfg` file** — if it independently defines `DBG_BASE_TME_HART0` (or equivalent), trust it over `chip_config.xml` when they disagree. `.cfg` files are typically hand-edited per target, not blindly copied.
2. **The new target's own vendor debug-tooling source** for the same register/purpose (its own `DAP_TME.cmm`-equivalent script, its own chip-specific branch in `go_tme.cmm`/`run_fbc.cmm`-equivalent scripts) — not the donor's copy.
3. **Corroboration from other, unrelated targets** that independently arrive at the same value the new target's own sources already point to.
4. **Absence from all of the above is itself a finding** — say so explicitly rather than defaulting to "no evidence against it, so leave it."

`tme_debug_base` specifically is the highest-historical-risk value: it has been found stale (carrying a donor's value instead of the new target's own) in a case where the target's own `.cfg` file and its own vendor DAP-setup script both already independently agreed on the correct value, while only `chip_config.xml` still had the copy-paste bug. Always cross-check `tme_debug_base` against both of those sources specifically, even when the rest of the `tme_config` block looks plausible.

`apss_pbl_1st_break_point`/`apss_pbl_2nd_break_point` are addresses inside APSS's PBL ROM, which is typically a *different build* per target — identical values across two independently-built targets are plausible (shared PBL lineage) but not provable from config files alone, and this class of address has a documented history of drifting across firmware revisions even for a single target. Treat this as a standing, not one-time, risk: confirm on real hardware before trusting Step 1 to complete.

`tme_mrom_entry_point`/`tme_fw_entry_point` are firmware ELF entry points — verify against the new target's actual built ELF once available; a byte-identical value to the donor's is not evidence of correctness on its own.

## Dependency closure to confirm on the new target

- `utils.reset.ResetCore`, `utils.stop_wait.wait_for_breakpoint_hit`, `utils.source_map.setSourceMap`, `utils.debug_context.g_dbgContext`/`logger` — shared, chip-agnostic; should already work for any registered target with no changes.
- `target_utils/pbl_load_fbc.py`, `target_utils/qreset.py` — per-target files, but not TME-specific; confirm they already exist for the new target (they usually do if it already has `xbl.py` or any RISC-V-subsystem support) before assuming they need to be written.
- **The one chip-specific line to change**: `from lldb_pkg.target.<donor>.target_utils.pbl_load_fbc import pbl_load_fbc` → `from lldb_pkg.target.<new_target>.target_utils.pbl_load_fbc import pbl_load_fbc`. This is the entire expected code diff beyond the file copy itself.
- Confirm the command dispatcher already lists `"TME"` as a valid subsystem and resolves `lldb_pkg.target.{chip}.image.tme` dynamically by chipset name — in a properly generalized setup this needs no change; verify rather than assume by checking the dispatcher doesn't hardcode any chip name.

## Known limitations to carry forward, not silently fix

- Step 1's two `apss_process.Continue()` calls are not wrapped in `debugger.SetAsync(True)`/`SetAsync(False)`, unlike this framework's documented best-practice pattern elsewhere (e.g. `xbl.py`). A missed breakpoint hangs the session indefinitely with no timeout. This is a property of the reference implementation itself — port it as-is for fidelity to the proven-working donor; don't fix it mid-port. If asked to harden it, treat that as a separate, explicit follow-up, ideally applied to every target's `tme.py` at once for consistency.
- A "TME SEQ" (sequencer) sub-block is visible in some targets' vendor DAP-setup scripts (a second `COREDEBUG.Base`) but is not modeled by any target's `tme.py`/`chip_config.xml` today. If the new target's build tooling declares a mandatory sequencer firmware artifact, that's a real, named limitation of this port (main-TME-core debug only), not a regression — call it out rather than silently scoping around it.
- `getBinaryPath("tme", ...)` ultimately resolves through a build-output artifact (e.g. a `contents.xml`-style manifest) generated by the target's own firmware/meta build, outside this repository. Confirm the new target's build declares a `"tme"` image bucket with the expected ELF filenames, or set a manual binary-path override (e.g. a `TME_BIN_PATH`-style setting) if it doesn't yet.
- `tmeVerifySignature` (or equivalent) in `<source_config>` implies TME images are signature-verified as part of boot. No debug-mode bypass mechanism should be assumed without finding the specific script/mechanism that provides it for the new target — treat this as an open question per target, not a solved problem.

## chip_config.xml editing convention

When correcting a wrong value, match the donor's plain style by default — don't add an explanatory comment unless the target's own file has an unambiguous, already-established convention for exactly this kind of correction, or you're explicitly asked to document it. Ask if genuinely unclear rather than guessing either way.

## Validation

- `py_compile` the new `tme.py`; `xml.etree.ElementTree.parse` any edited `chip_config.xml`. Cheap, mandatory, and catch mechanical mistakes immediately — neither proves a value is hardware-correct, only that the file parses.
- A plain `python -c "import ..."` of the new module will typically fail on an unrelated `ModuleNotFoundError: No module named 'lldb'` outside the LLDB runtime — that's expected and not a sign of a real bug; `py_compile` is the right tool for a syntax-only check.
- Before calling the port done, have an independent pass re-derive every `tme_config` value directly from the new target's own raw sources (not from a summary of earlier findings), and confirm the donor-family choice against the flow-signature evidence above.
- State plainly which values are independently confirmed versus plausible-but-unverified for the new target. A clean syntax/XML check must not be read as hardware validation.

## Output checklist

- [ ] Every existing `tme.py` across all targets was found and the donor family (Family A vs. Family B — see Donor selection) was matched against the new target's own `chip_config.xml` schema and, decisively, its own vendor debug-tooling flow signature — not address-family similarity alone.
- [ ] All seven `tme_config` elements (or ten, if Family B applies) are present in the new target's `chip_config.xml`, each labeled confirmed-with-source or explicitly flagged unverified.
- [ ] `tme_debug_base` was specifically cross-checked against the new target's own `.cfg` file and its own vendor DAP-setup script.
- [ ] `settings.json`, `launch.json`, the `.cfg` TME target-create line, and `target_utils/pbl_load_fbc.py`/`qreset.py` were each independently checked for what's already staged, before writing anything.
- [ ] The command dispatcher was confirmed to need no changes (generic dispatch verified, not assumed).
- [ ] The new `tme.py` is the donor file plus only the one chip-specific import line — no unrelated changes.
- [ ] Syntax check (Python) and well-formedness check (XML) both pass.
- [ ] An independent review pass re-derived the value claims and the donor-family choice from the new target's raw sources.

## Related skills

- `port-soccp-image-debug-script` — the same donation/verification pattern for SOCCP instead of TME; useful side-by-side reading since the two subsystems' donor implementations deliberately diverge in STEP 1 bring-up style and STEP 2 PC-setting mechanism.
- `qreset-bring-core-out-of-reset` — the same verification discipline, scoped to a target's reset-helper/core-bring-up script pair rather than TME specifically.
- `reorganize-image-scripts` — restructuring/consolidating image scripts across targets; a different operation from porting TME to one new target.
- `loadbuild-cmm-to-python`, `convert-cmm-script` — useful when the new target's own vendor-tooling source needs to be read closely to resolve the donor-family question above.
