---
name: port-uefi-cmm-to-python
description: Port a Qualcomm Trace32 UEFI debug script (debug_uefi.cmm + dxe_dbg.cmm) from CMM to Python LLDB, using the Maili reference implementation as the template. Use for requests like "port UEFI for Mavros", "convert debug_uefi.cmm to Python", or "add UEFI debug support for <platform>". Produces a fully documented, config-driven Python script that orchestrates SEC module loading, DXE core/driver discovery via memory-dump parsing, and breakpoint-driven stepping through the DXE dispatch sequence.
---

# UEFI CMM → Python LLDB Conversion

Ports a platform's `debug_uefi.cmm` (Trace32 UEFI debug script, which in turn calls the shared `dxe_dbg.cmm`) into a Python LLDB command pair — `debugUEFI()` and `runtimedxeUEFI()` — that replicates the complete UEFI boot debug flow: booting through HYP to the UEFI entrypoint, loading SEC module symbols, locating `LoadDxeCoreFromFfsFile`, discovering DXE driver load offsets via a memory-dump-and-parse trick, loading DXE core/driver debug symbols, and stepping through the DXE dispatch sequence one driver at a time.

**Key insight #1:** Unlike XBL/TFA/TZ/HYP (each a single continuous run-to-breakpoint flow), UEFI debugging is a **multi-call state machine**. The user runs `debug -d uefi` once to reach the entrypoint, then `debug -d uefi -i <ModuleName>` repeatedly — once per module they want to stop at. State persists *between* these separate command invocations via `g_dbgContext.uefiDbgContext['UEFI_Sec']` / `['UEFI_Dxe']`. A porter unfamiliar with this will mistakenly treat each call as stateless.

**Key insight #2:** `debug_uefi.cmm` and `dxe_dbg.cmm` are typically **shared verbatim across an entire chip family**, not authored per-platform. When investigating Mavros's port, its own `run_fbc.cmm` pointed at the literal `HawiFamily\HawiDefaultPkg\Tools\debug_uefi.cmm` — the same file Hawi and Maili use, byte-for-byte, including hardcoded DVFS workaround addresses. Don't assume you need platform-specific CMM sources; check whether the platform's FBC script actually references its own UEFI package's `Tools/debug_uefi.cmm` or reuses an ancestor chipset's.

**Key insight #3:** The Maili reference implementation (`scripts/lldb_pkg/target/maili/image/uefi.py`) is the canonical template — most new platforms require **zero functional changes**, only the `debugHYP` import path and any stale ancestor-chipset naming in comments/log strings.

## 0. Pre-flight checklist

Before starting the conversion:

- [ ] **Confirm the target platform's CMM sources are accessible:**
  - `debug_uefi.cmm` (per-platform `.../Platform/Qualcomm/<Family>/<Platform>DefaultPkg/Tools/debug_uefi.cmm`, in the UEFI build tree — often literally shared with an ancestor chipset)
  - `dxe_dbg.cmm` (shared, generic — lives in `.../Silicon/Qualcomm/Tools/dxe_dbg.cmm`, one level up from the family tree)
  - `run_fbc.cmm` (confirms which `debug_uefi.cmm` path is actually wired up, and the `&UEFIEntryPoint` address)
  - Ask the user to attach these via `@` file references, or via UNC path if reachable — Read can sometimes resolve UNC paths (`\\server\share\...`) directly even when shell `ls`/`find` cannot; try Read before concluding a source is unreachable
- [ ] **Confirm the target platform's `chip_config.xml` exists** with a `<chipset name="<platform>">` entry containing:
  - `<configure_target>`: `UefiRamEntryAddr`, `RamFvAddr`, `InfoBlkPtrOffset`, `SharedImemAddress`, `Arch`, and `CoreObj` (path must point at `uefi_images/Build/<Platform>DefaultPkg/DEBUG_CLANGDWARF/AARCH64` — the platform's own package name, even if `debug_uefi.cmm` itself still says an ancestor family name)
  - `<InitOffsets>`: at minimum `SecLoadAddrOffset`, `IBlkDbgStopDrvrStr`, `IBlkBdsHotkey`, `IBlkForceDisableHWWdog`, `IBlkModuleName`, `IBlkTotalCmdSize`, `IBlkSymbolCmdString`, `IBlkCmdStartOffset` (the full offset set is much larger — copy it wholesale from Maili/Hawi's `chip_config.xml` and diff, don't hand-transcribe from the CMM)
- [ ] **Confirm the settings/launch wiring exists:**
  - `target/<platform>/settings.json` has `UEFI_BUILD_PATH`, `UEFI_SRC_PATH`, `UEFI_BIN_PATH`, `UEFI_DXE_NAME` keys
  - `target/<platform>/launch.json` has a `"UEFI Debug"` block invoking `debug -d uefi` / `debug -d uefi -i ${config:UEFI_DXE_NAME} ${config:UEFI_BIN_PATH}`
  - These are usually already present (copied along with the rest of the platform's `settings.json`/`launch.json` scaffold) — confirm rather than assume
- [ ] **Confirm the full boot-chain dependency exists for this platform** (UEFI is the last stage — it transitively needs everything before it):
  - `target/<platform>/image/hyp.py` (UEFI calls `debugHYP(debugger, command, exe_ctx, result, "run")` to reach the UEFI entrypoint)
  - `target/<platform>/image/tz.py`, `target/<platform>/image/tfa.py` (hyp.py's own dependencies)
  - `target/<platform>/image/debugwithpil.py` (hyp.py imports `pil_debugger` from this at module load time — **if missing, importing uefi.py will fail with `ModuleNotFoundError` even though the failure has nothing to do with UEFI**; it's chipset-generic and can be copied verbatim from Maili/Hawi if not yet present)
  - If any of these are missing, either port them first (see `port-hyp-cmm-to-python`, `port-tz-cmm-to-python`, `port-tfa-cmm-to-python`, `port-xbl-cmm-to-python`) or flag the gap explicitly to the user before proceeding — don't silently create stub files unless asked
- [ ] **Verify the target platform's image directory is clean:**
  - Should only contain `__init__.py` and platform-specific image scripts
  - If it contains scripts from other platforms, remove them first

## 1. Analyze the CMM sources

Read `debug_uefi.cmm`, `dxe_dbg.cmm`, and `run_fbc.cmm` for the target platform. Identify:

### From `debug_uefi.cmm`:
- **`UpdateCorePowerState` subroutine** — a silicon workaround (forces DVFS pState to avoid bus errors) with hardcoded register addresses (`DVFS_CTL0/1/2`, `DVFS_EPP_CTL0/1/2`). These are IP-catalog addresses for a specific chip die, cited via an `ipcatalog.qualcomm.com/swi/chip/<id>/...` comment in the CMM. **Check whether the platform's own `debug_uefi.cmm` is the literal same file as the ancestor chipset's** (see Key Insight #2) — if so, port the addresses verbatim; if the platform has its own distinct `debug_uefi.cmm` with different addresses, use those instead. Don't guess.
- **`&Target`/`&TargetPkg` variables** — note these are often still hardcoded to an ancestor family name (e.g. `&Target="Hawi"`) even in a newer platform's build tree; this is a T32 tooling quirk, not something to fix in the Python port.
- **`SetArguments`/`TargetSetup`** subroutines — set `UefiRamEntryAddr`/`RamFvAddr`/`RamFvSize` defaults and drive the initial run-to-entrypoint (`b.set &UefiRamEntryAddr /o /disablehit; go`). This maps to the Python's `debugHYP(...)` + `br s -a <UEFI_baseAddr> --hardware` + `c` sequence.

### From `dxe_dbg.cmm` (shared, generic):
- **SEC entry flow**: breaks at `SecMain` after loading `Sec.debug` symbols at `UefiRamEntryAddr + SecLoadAddrOffset`.
- **`LoadDxeCoreFromFfsFile` breakpoint**: `go LoadDxeCoreFromFfsFile\38` — an offset into the function (`\38` = 0x38 bytes past entry) used to land past the function prologue; the Python port instead evaluates `&LoadDxeCoreFromFfsFile` symbolically and subtracts a fixed offset (`0x40`) — functionally equivalent, don't "fix" the discrepancy between `0x38` (CMM) and `0x40` (Python) without checking both actually land at the same instruction on the target's compiled binary.
- **Info Block writes**: `&DrvrAddr`/`&BdsHotkeyAddr`/`&ForceDisableHWWdogAddr` — writes the requested module-name substring, BDS hotkey flag, and watchdog-disable flag into the firmware's UEFI Info Block so it knows what to stop at.
- **`sym.cmm` invocation via breakpoint command**: `B.S BreakPointFunction /P /disablehit /CMD "do &CwDir/sym.cmm ..."` — this is the CMM-side origin of the memory-dump-and-parse trick (see Section 6).

### From `run_fbc.cmm`:
- **`&UEFIEntryPoint`** address — should match `chip_config.xml`'s `UefiRamEntryAddr` exactly; if the FBC script's `debug_uefi.cmm` path points at a different platform/family folder than expected, that's a strong signal the UEFI CMM tooling is shared (see Key Insight #2), not a bug.

## 2. Compare against Maili reference

Create a side-by-side comparison of the target platform's CMM/config vs. Maili's:

| Aspect | Maili | Target Platform | Impact on Python |
|---|---|---|---|
| `debug_uefi.cmm` path/content | `HawiFamily/HawiDefaultPkg/Tools/debug_uefi.cmm` | (check — may be the literal same file) | None if shared verbatim |
| `UpdateCorePowerState` addresses | `0x198B0000` / `0x198B5000` / `0x198BA000` etc. | (verify) | Port verbatim if CMM confirms same addresses |
| `UefiRamEntryAddr` | `0xC6800000` | (check chip_config.xml) | None — resolved from XML at runtime |
| `InfoBlkPtrOffset` | `0x00601000` | (check chip_config.xml) | None — resolved from XML at runtime |
| `CoreObj` path | `uefi_images/Build/HawiDefaultPkg/...` | `uefi_images/Build/<Platform>DefaultPkg/...` | ✓ Must use platform's own package name |
| All `IBlk*`/`Rva*` offsets | (Hawi-family values) | (check chip_config.xml) | None if family-standard, else must differ |
| `debugHYP` import | `lldb_pkg.target.maili.image.hyp` | `lldb_pkg.target.<platform>.image.hyp` | ✓ Must update import path |
| Boot-chain dependency (`hyp.py`→`tz.py`→`tfa.py`→`debugwithpil.py`) | present | (verify each exists) | Blocking if any missing |

**Key finding:** If the target platform's `chip_config.xml` UEFI section is byte-identical to Maili's (common — Hawi-family platforms tend to share this section wholesale) and its `debug_uefi.cmm` is the same shared file, the Python port requires **zero functional changes** — only the import path and any stale chipset-name strings in comments/log messages.

## 3. Verify platform configuration

```bash
grep -A 8 '<configure_target>' scripts/lldb_pkg/target/<platform>/chip_config.xml
grep -A 40 '<InitOffsets>' scripts/lldb_pkg/target/<platform>/chip_config.xml
grep -i 'UEFI_' scripts/lldb_pkg/target/<platform>/settings.json
grep -A 5 '"UEFI Debug"' scripts/lldb_pkg/target/<platform>/launch.json
```

Confirm every offset that `debugUEFI`/`runtimedxeUEFI`/`memoryDump` reads is present (see Section 0's list). If any element or settings key is missing, flag it to the user rather than inventing a value — these are firmware ABI offsets, not tunable parameters.

## 4. Decide: new file or enhance existing?

Check if `scripts/lldb_pkg/target/<platform>/image/uefi.py` already exists:

- **If it exists and is identical to Maili's:** Enhance documentation only (Section 5A)
- **If it exists but differs:** Analyze the differences; ask the user if they're intentional
- **If it doesn't exist:** Create a new file using Maili's `uefi.py` as the structural reference (Section 5B)

## 5A. Documentation-only enhancement (most common case)

If the target platform's `uefi.py` is already functionally correct:

1. **Enhance the file header**: `@file`, `@brief` ("Mavros UEFI image loading and debugging implementation."), `@author`, `@section Description` with the 7-step debug flow (boot via HYP → load SEC → halt at SecMain/DXE-core-load → write Info Block cookies → memory-dump-and-parse → load DXE symbols → breakpoint + chain to `runtimedxeUEFI`), `@note` bullets.
2. **Enhance `debugUEFI`'s docstring**: `@brief`, `@details` with the numbered flow, `@param` for each of `debugger`/`command`/`exe_ctx`/`result`/`inputarg`/`ModuleName`, `@return`, `@exception SystemExit` conditions, `@note` covering the `uefiDbgContext` state-machine guard and the `Uefi_memory_dump.txt` scratch-file mechanism.
3. **Enhance `runtimedxeUEFI`'s docstring** similarly — note it's chained automatically via the `runtimedxe` breakpoint command, not called directly by the user.
4. **Add type hints** to all function signatures (`debugUEFI`, `runtimedxeUEFI`, `update_core_power_state`, `validateFvHeader`, `verifyInfoBlk`, `memoryDump`) matching the platform's existing `tz.py`/`hyp.py` style.
5. **Verify no logic changes**: diff functional lines (strip comments/docstrings) against Maili's original — the only differences should be the import path and any stale chipset-name log/comment text.

## 5B. New file creation (if needed)

1. **Use Maili's `uefi.py` as a structural reference only** — don't blindly copy; write platform-specific imports.
2. **Update the import**: `from lldb_pkg.target.<platform>.image.hyp import debugHYP`. Confirm the target's `hyp.py` exposes `debugHYP(debugger, command, exe_ctx, result, inputarg=None, subsys=None)` with the same 5-positional-arg calling convention (`debugHYP(debugger, command, exe_ctx, result, "run")`) and that it halts at a stable breakpoint (e.g. `boot_cold_init`) before returning when called this way — `debugUEFI` immediately deletes breakpoints and sets its own at `UefiRamEntryAddr` right after this call, so the calling convention must match exactly.
3. **Do not rename or remove chipset-name tags inconsistently** — check the log line `" --- Preparing for Debug of UEFI Image ---"` across `hawi`/`maili`/other existing targets first. As of this writing, only `hawi` and `maili` had erroneously appended a `(Hawi)` suffix; every other target (`alana`, `aldabra`, `amboseli`, ... 25+ others) already uses the plain generic form with no chipset tag. **Match the generic form — do not add a `(<Platform>)` suffix.**
4. **Apply documentation enhancements** per Section 5A.
5. **Verify platform-specific adaptations**: confirm `g_dbgContext.openocdConfig.getImageBasePath("uefi")` and the `chip_config.xml` lookups resolve correctly for this chipset name; confirm no leftover references to "maili" (or another ancestor platform name) remain in code, only where accurately describing shared/ancestor CMM tooling.

## 6. Core complexity: the memory-dump-and-parse mechanism

This is UEFI's equivalent of TZ's ASLR relocation or HYP's KASLR calculation — the one genuinely tricky piece, unique to this image type:

1. **Why it exists**: The firmware's UEFI Info Block contains a region populated by the build's own tooling with literal Trace32 command text (`translate ...`, `&Sf="..."`, `data.load.elf "&Sf" ...`) describing where each DXE driver's debug ELF lives and what offset to load it at. `dxe_dbg.cmm`'s `sym.cmm` helper is what originally *emits* this text on the CMM side; the Python port instead treats it as opaque data and scrapes it directly.
2. **`memoryDump()`** computes the command region's start/end addresses from three Info Block offsets (`IBlkSymbolCmdString`, `IBlkTotalCmdSize`, `IBlkCmdStartOffset`) and does `memory read --outfile Uefi_memory_dump.txt --force --binary <start> <end>` to dump it to a scratch file.
3. **Parsing** (`debugUEFI`/`runtimedxeUEFI`, inline): reads the dump line by line looking for three markers:
   - `"translate"` → extract the source-path prefix (for `target.source-map`)
   - `'&Sf="'` → extract the DXE driver's `.debug` file path and derive its uppercased name
   - `'data.load.elf "&Sf"'` → extract the hex load offset that follows
   - Stops early once the requested module name is found (unless `debug_dxe_core`, which always breaks after the first entry).
4. **State carried forward**: `Dxe_Name_List`/`Dxe_dll_Path_List`/`offset_List` are parallel lists; the matched index is used to build the full symbol path (`CoreObj + Dxe_dll_Path`) and call `SetModuleLoadAddress()`.
5. **Scratch file lifecycle**: opened via a path computed two *different* ways in the two functions in Maili's original — `debugUEFI` uses `g_dbgContext.openocdDir.replace("\\bin","\\scripts")`, while `runtimedxeUEFI` uses `os.path.dirname()` three levels up from `__file__`. **This inconsistency is pre-existing in the reference implementation — preserve it as-is during a port rather than "fixing" it to look consistent**, unless the user explicitly asks you to address it; silently changing it changes which directory gets cleaned up and could mask a real divergence you haven't verified. Likewise, `runtimedxeUEFI`'s final `os.remove("Uefi_memory_dump.txt")` uses a bare relative path rather than the computed `file_path` — also preserve verbatim.

## 7. Core complexity: the multi-call SEC/DXE state machine

Do not treat `debugUEFI` as a single linear script — it's re-entered from scratch on every `debug -d uefi -i <ModuleName>` invocation, and branches on state saved in `g_dbgContext.uefiDbgContext`:

1. **Call 1** (`ModuleName=None`): drives through `debugHYP(...)` to the UEFI entrypoint, sets a breakpoint at `UefiRamEntryAddr`, continues, and returns. No SEC/DXE state touched yet.
2. **Call 2** (`ModuleName="Sec"` or any driver name, first time): guarded by `g_dbgContext.uefiDbgContext['UEFI_Sec'] is None`. Runs `update_core_power_state`, `validateFvHeader`, loads `Sec.debug`, evaluates `&LoadDxeCoreFromFfsFile`, halts at `SecMain`, then sets `uefiDbgContext['UEFI_Sec'] = 1`. If `ModuleName.upper() != "SEC"`, execution falls through into the DXE-loading block in the *same* call.
3. **Subsequent calls** (module name is a DXE driver or `"Dxe"`): guarded by `g_dbgContext.uefiDbgContext['UEFI_Dxe'] is None` for the one-time `LoadDxeCore_addr` breakpoint; then unconditionally does the Info Block writes + memory-dump-and-parse + symbol loading for the *newly requested* module every time.
4. **`runtimedxeUEFI`** is a separate, third entry point — chained automatically via `bkpt.SetCommandLineCommands(["runtimedxe"])` on the `BreakPointFunction` breakpoint, invoked by LLDB itself (not by the user) each time a new DXE driver is about to dispatch. It repeats the memory-dump-and-parse for whatever module name is currently in the Info Block and sets a fresh `ProcessModuleEntryPointList` breakpoint.

When porting, verify the guard conditions (`is None` checks against the shared context dict) are preserved exactly — breaking them causes either duplicate SEC loads or a `KeyError`/silent no-op on the second call.

## 8. LLDB API validation

```python
# Methods to validate:
- SBDebugger.HandleCommand()
- SBDebugger.GetCommandInterpreter()
- SBDebugger.GetSelectedTarget()
- SBCommandInterpreter.HandleCommand()
- SBCommandReturnObject.GetOutput()
- SBTarget.AddModule()
- SBTarget.SetModuleLoadAddress()
- SBTarget.EvaluateExpression()
- SBTarget.modules / GetNumModules()
- SBTarget.BreakpointCreateByName()
- SBBreakpoint.SetCommandLineCommands()
- SBStringList.AppendString()
```

Check against the LLDB Python bindings:
```bash
grep -E "def (HandleCommand|EvaluateExpression|SetModuleLoadAddress|BreakpointCreateByName|SetCommandLineCommands)" <lldb-python-path>/lldb/__init__.py
```

## 9. Verification workflow

1. **Syntax check:** `python -m py_compile scripts/lldb_pkg/target/<platform>/image/uefi.py` (or `python -c "import ast; ast.parse(open(path).read())"` if no Python interpreter is on PATH — this environment often lacks one; use whichever succeeds)
2. **Functional diff:** `diff <(grep -v '^\s*#\|^\s*"""\|^\s*@\|^\s*$' maili/image/uefi.py) <(grep -v '^\s*#\|^\s*"""\|^\s*@\|^\s*$' <platform>/image/uefi.py)` — every remaining difference should be explainable (import path, type hints, whitespace) with **no logic drift**.
3. **Import chain check:** confirm `hyp.py`, `tz.py`, `tfa.py`, `debugwithpil.py` all exist for the target platform (statically, via `ls`/`Glob` — don't just assume).
4. **Leftover-reference check:** `grep -ni "maili\|<ancestor-chipset>" <platform>/image/uefi.py` — should return nothing unless accurately describing shared CMM tooling in a comment.
5. **Chipset-tag check:** confirm the entry log line matches the generic form used by the majority of targets (no `(<Platform>)` suffix) — see Section 5B point 3.
6. Note for the user: real validation (`debug -d uefi`, then `debug -d uefi -i Sec`, then `debug -d uefi -i <Driver>`) requires a live OpenOCD+LLDB session against actual hardware/pre-silicon, which most conversion environments cannot provide — say so explicitly rather than claiming the port is hardware-verified.

## 10. Output checklist

- [ ] File header matches repo convention: SPDX line, `@file`, `@brief`, `@author`, `@section Description`
- [ ] `debugUEFI`/`runtimedxeUEFI` docstrings include `@brief`, `@details`, `@param`, `@return`, `@exception`, `@note`
- [ ] `debugHYP` import path is platform-specific and calling convention verified against the platform's `hyp.py`
- [ ] All `chip_config.xml` lookups (`UefiRamEntryAddr`, `InfoBlkPtrOffset`, `CoreObj`, all `IBlk*` offsets) resolve for `<chipset name="<platform>">`
- [ ] `CoreObj` path uses the platform's own `<Platform>DefaultPkg`, not an ancestor family's package name
- [ ] `update_core_power_state` DVFS addresses verified against the platform's actual `debug_uefi.cmm` (ported verbatim if shared, updated if platform has its own distinct addresses)
- [ ] No chipset-name suffix added to the generic entry log line
- [ ] `uefiDbgContext['UEFI_Sec']`/`['UEFI_Dxe']` guard conditions preserved exactly
- [ ] `memoryDump()`/scratch-file-path inconsistencies preserved as-is (not silently "fixed") unless the user asked for a cleanup pass
- [ ] Syntax check passes
- [ ] Functional diff against Maili shows no unintended logic changes
- [ ] `settings.json` has `UEFI_BUILD_PATH`/`UEFI_SRC_PATH`/`UEFI_BIN_PATH`/`UEFI_DXE_NAME`; `launch.json` has a `"UEFI Debug"` block
- [ ] Boot-chain dependencies (`hyp.py`→`tz.py`→`tfa.py`→`debugwithpil.py`) all confirmed present

## 11. Common pitfalls

- **Assuming `debug_uefi.cmm` is platform-authored:** It's frequently shared verbatim across a chip family. Check `run_fbc.cmm`'s `&UEFISCRIPT` path before assuming you need a platform-unique CMM source.
- **"Fixing" pre-existing inconsistencies during a port:** The reference implementation has at least two known internal inconsistencies (scratch-file path computation differs between `debugUEFI`/`runtimedxeUEFI`; `runtimedxeUEFI`'s final cleanup uses a bare relative filename). Porting is not the time to normalize these — preserve them, and only change them if the user explicitly asks.
- **Adding a chipset-name tag to log messages:** Most targets use a plain generic entry message with no `(<Chipset>)` suffix; don't reintroduce the pattern that only `hawi`/`maili` had.
- **Missing `debugwithpil.py`:** `hyp.py` imports it at module load time; if it doesn't exist for the target platform, importing the newly-ported `uefi.py` fails with an error that looks unrelated to UEFI. Check this explicitly, don't debug it after the fact.
- **Treating each `debug -d uefi -i X` call as stateless:** It isn't — see Section 7. Breaking the `uefiDbgContext` guards causes duplicate SEC loads or silent no-ops.
- **Hand-transcribing `chip_config.xml` offsets from the CMM:** These are firmware ABI offsets (`IBlkModuleName`, `IBlkCmdStartOffset`, etc.) — copy the whole `InitOffsets` block from Maili/Hawi and diff against the platform's actual CMM rather than retyping individual hex values.
- **Wrong `CoreObj` package name:** The CMM's `&Target`/`&TargetPkg` may still say an ancestor family name — don't propagate that into `chip_config.xml`'s `CoreObj` path, which must use the platform's actual build output folder.
- **Reachability of UNC-path CMM sources:** Shell tools (`ls`, `find`) frequently cannot resolve `\\server\share\...` paths in sandboxed environments even when the Read tool can. Try Read directly before telling the user the source is unreachable.

## 12. Assumptions and limitations

### Assumptions
- The target platform's `chip_config.xml` already has a UEFI `configure_target`/`InitOffsets` section (usually inherited wholesale from the chip family)
- The target platform has its own working `image/hyp.py` (and its dependents `tz.py`, `tfa.py`, `debugwithpil.py`)
- The target platform's `settings.json`/`launch.json` UEFI scaffolding is already present (typically copied when the platform's target directory was first created)
- SEC/DXE debug symbol file naming (`Sec.debug`, `<Driver>.debug`) and Info Block signatures (`0x4856465F` FV header, `0x6B6C4249` UefiInfoBlk) are standard across Hawi-family platforms

### Limitations
- T32-specific constructs (AREA/window management, `t32_uefi_menu.cmm`, interactive BDS menu flow) are not ported
- The script depends on `debugHYP()` to reach the UEFI entrypoint — if HYP debug is not available/working for the platform, UEFI debug will fail before it starts
- The memory-dump-and-parse mechanism assumes the Info Block's command-string region layout is unchanged from the Hawi-family format; a platform with a materially different UEFI firmware core would need this logic revisited, not just the offsets
- `update_core_power_state`'s DVFS addresses are hardcoded; if a platform uses a genuinely different DVFS controller IP block, these must be updated, not just carried over

## 13. Deliverables

1. **Enhanced/created `uefi.py`** at `scripts/lldb_pkg/target/<platform>/image/uefi.py` — full Doxygen documentation, platform-specific `debugHYP` import, all `chip_config.xml`-driven values, functionally identical to Maili's proven implementation.
2. **Analysis document** (if significant differences found): CMM → Python mapping table, platform-specific differences vs. Maili, `chip_config.xml`/`settings.json`/`launch.json` verification results, boot-chain dependency check results.
3. **Before/after comparison** (if documentation enhanced): file header and docstring changes, confirmation of no logic changes (functional diff output).

## References

- **Maili reference implementation:** `scripts/lldb_pkg/target/maili/image/uefi.py`
- **Hawi reference implementation (ancestor):** `scripts/lldb_pkg/target/hawi/image/uefi.py`
- **Maili HYP module (dependency):** `scripts/lldb_pkg/target/maili/image/hyp.py`
- **LLDB API reference:** `lldb/__init__.py` (from VSCode LLDB extension or system LLDB installation)
- **T32 → LLDB mapping:** See `convert-cmm-script` skill for comprehensive mapping table
- **HYP porting skill:** See `port-hyp-cmm-to-python` skill — UEFI's immediate boot-chain predecessor
- **TZ porting skill:** See `port-tz-cmm-to-python` skill
- **TFA porting skill:** See `port-tfa-cmm-to-python` skill
- **XBL porting skill:** See `port-xbl-cmm-to-python` skill