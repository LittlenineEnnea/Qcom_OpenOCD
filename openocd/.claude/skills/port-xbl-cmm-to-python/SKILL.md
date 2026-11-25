---
name: port-xbl-cmm-to-python
description: Port a Qualcomm Trace32 XBL (eXtensible Boot Loader) debug script from CMM to Python LLDB, using the Maili reference implementation as the template. Use for requests like "port XBL for Mavros", "convert go_xblsc.cmm to Python", or "add XBL debug support for <platform>". Produces a fully documented, config-driven Python script that orchestrates device reset, CPUCP+APSS PBL bring-up, target connection, and XBL entrypoint debugging.
---

# XBL CMM → Python LLDB Conversion

Ports a platform's `go_xblsc.cmm` (Trace32 XBL debug script) into a Python LLDB command that replicates the full boot chain initialization flow: device reset, CPUCP+APSS PBL bring-up via FBC (Full Boot Chain), target connection, breakpoint setup at XBL entry point, and execution to the QSEE handoff point.

**Key insight:** XBL scripts are highly standardized across platforms. The Maili reference implementation (`scripts/lldb_pkg/target/maili/image/xbl.py`) is the canonical template — most new platforms require **zero functional changes**, only documentation enhancement and platform-specific config verification.

## 0. Pre-flight checklist

Before starting the conversion:

- [ ] **Confirm the target platform's CMM sources are accessible:**
  - `run_fbc.cmm` (top-level FBC orchestrator)
  - `go_xblsc.cmm` (XBL-specific debug flow)
  - Ask the user to attach both via `@` file references (e.g. `@path/to/go_xblsc.cmm`)
- [ ] **Confirm the target platform's `chip_config.xml` exists** in the project:
  - `scripts/lldb_pkg/target/<platform>/chip_config.xml`
  - Verify it includes `<sleep_config><pmic_sleep>` and `<reset_scratch_config>` elements
- [ ] **Confirm the Maili reference implementation is available:**
  - `scripts/lldb_pkg/target/maili/image/xbl.py` (the template for structure only)
  - `scripts/lldb_pkg/target/maili/target_utils/pbl_load_fbc.py` (reference for comparison)
- [ ] **Confirm the target platform's directory structure exists:**
  - `scripts/lldb_pkg/target/<platform>/image/` (where `xbl.py` will live)
  - `scripts/lldb_pkg/target/<platform>/target_utils/` (platform-specific utilities)
  - **IMPORTANT:** Each platform has its own `target_utils/pbl_load_fbc.py` module
- [ ] **Verify the target platform's image directory is clean:**
  - Should only contain `__init__.py` (no copied scripts from other platforms)
  - If it contains scripts from other platforms, remove them first

## 1. Analyze the CMM sources

Read both `run_fbc.cmm` and `go_xblsc.cmm` for the target platform. Identify:

### From `go_xblsc.cmm`:
- **ELF path pattern:** Look for `&xblloader_dll="&BOOT_BUILDROOT/xbl_images/Build/<PLATFORM>_Default_LA/Loader/xbl_sc.elf"`
  - Extract the platform-specific build path fragment (e.g., `Mavros_Default_LA` vs. `Maili_Default_LA`)
  - **Note:** This is resolved dynamically in Python via `g_dbgContext.openocdConfig.getBinaryPath('boot', 'xbl_sc.elf')`, so hardcoding is not needed
- **Breakpoint symbols:** Confirm the entry point is `sbl1_main_ctl` (standard across all platforms)
- **PBL patch-version check:** Note if present (lines ~44–79 in Maili's CMM)
  - **Decision:** Not ported in Python; the explicit breakpoint/connect flow is more robust
- **TCSR BOOT MISC dload-cookie clear:** Note if present (line ~85 in Maili's CMM)
  - **Decision:** Handled generically by `pbl_load_fbc()` → `clear_dload_cookie()`, config-driven via `chip_config.xml`
- **Workarounds:** Note any commented-out or platform-specific workarounds
  - **Decision:** Only port if explicitly required; most are platform-specific and intentionally omitted

### From `run_fbc.cmm`:
- **Entry point addresses:** Look for `&TZEntryPoint`, `&HYPEntryPoint`, `&UEFIEntryPoint`
  - **Note:** These belong to `tz.py`, `hyp.py`, `uefi.py`, not `xbl.py` — only document if they differ from Maili
- **AOP boot cookie address:** Look for `&AOPBOOTCOOKIE_ADDR` (e.g., `0xC30F040`)
  - **Note:** Used by `pbl_load_fbc()`, not directly in `xbl.py`
- **Subsystem debug branches:** Note any `IF ("&DEBUG_SUBSYSTEM"=="...")` blocks
  - **Note:** Not relevant to `xbl.py`; these are handled by other image scripts

## 2. Compare against Maili reference

Create a side-by-side comparison of the target platform's CMM vs. Maili's CMM:

| Aspect | Maili | Target Platform | Impact on Python |
|---|---|---|---|
| ELF path fragment | `Build/Maili_Default_LA/...` | `Build/<PLATFORM>_Default_LA/...` | None — resolved dynamically |
| PBL patch-version check | Present (lines 44–79) | Commented out / Present / Absent | None — not ported |
| TCSR dload-cookie clear | Present (line 85) | Commented out / Present / Absent | None — handled by `clear_dload_cookie()` |
| Breakpoint symbols | `sbl1_main_ctl` | `sbl1_main_ctl` | None — standard |
| APSS core target | `apss3` | `apss3` | None — standard |
| Workarounds | Commented-out DCP PIL reset | Commented-out / Present / Absent | None — only port if required |

**Key finding:** If the target platform's CMM is structurally identical to Maili's (only ELF path and commented-out workarounds differ), the Python port requires **zero functional changes** — only documentation enhancement.

## 3. Verify platform configuration

Check the target platform's `chip_config.xml`:

```bash
grep -A 5 "<sleep_config>" scripts/lldb_pkg/target/<platform>/chip_config.xml
grep -A 10 "<reset_scratch_config>" scripts/lldb_pkg/target/<platform>/chip_config.xml
```

Confirm:
- ✅ `<pmic_sleep sleep_time="...">` element exists (value can be 0 or > 0)
- ✅ `<reset_scratch_config>` element exists with `<tcsr_boot_misc_detect addr="..." clear_mask="...">` child
- ✅ `<riscv_config><cpucp_config>` element exists (for CPUCP PBL bring-up)

If any element is missing, flag it to the user — the Python script depends on these for runtime configuration.

## 4. Decide: new file or enhance existing?

Check if `scripts/lldb_pkg/target/<platform>/image/xbl.py` already exists:

- **If it exists and is identical to Maili's:** Enhance documentation only (Section 5A)
- **If it exists but differs:** Analyze the differences; ask the user if they're intentional
- **If it doesn't exist:** Create a new file by copying Maili's template (Section 5B)

## 5A. Documentation-only enhancement (most common case)

If the target platform's `xbl.py` is already functionally correct (byte-for-byte equivalent to Maili's):

1. **Enhance the file header** (lines 1–15):
   - Add `@brief` tag: "XBL (eXtensible Boot Loader) image debugging script for <PLATFORM> platform."
   - Add `@author` tag: "Qualcomm Innovation Center, Inc."
   - Expand `@section Description` with full boot chain flow summary

2. **Enhance the function docstring** (lines 30–74):
   - Add `@brief` tag with concise summary
   - Add `@details` block with 8-step numbered flow:
     1. Load XBL ELF symbols from the boot build directory
     2. Configure LLDB settings (packet timeout, platform, auto-confirm)
     3. Read platform-specific PMIC sleep configuration from chip_config.xml
     4. Perform device reset (qreset) and CPUCP+APSS PBL bring-up via pbl_load_fbc()
     5. Connect to the APSS core3 target via gdb-remote
     6. Set a one-shot breakpoint at sbl1_main_ctl (XBL entry point)
     7. Resume execution and wait for the breakpoint to be hit
     8. Optionally stop for interactive debugging or continue to QSEE handoff
   - Keep all `@param` tags; clarify that `command`, `exe_ctx`, `result` are unused
   - Add `@return` tag documenting return behavior and exit conditions
   - Add `@note` block (5 bullet points):
     - Requires g_dbgContext to be initialized with chipset name and configuration
     - Depends on chip_config.xml for pmic_sleep and reset_scratch_config settings
     - Uses shared pbl_load_fbc() from lldb_pkg.target.maili.target_utils for CPUCP bring-up
     - Source path mapping is optional; if not configured in settings.json, defaults to LLDB's built-in behavior
     - The breakpoint at sbl1_main_ctl is set as one-shot to avoid re-triggering

3. **Enhance inline comments** (lines 109–121):
   - Clarify `pmic_sleep` configuration read (3-line comment)
   - Clarify branching logic on `pmic_sleep` value (2-line comment)

4. **Verify no logic changes:**
   - All control flow, LLDB API calls, imports, and breakpoint names remain identical to Maili's
   - Only docstrings and comments are modified

## 5B. New file creation (if needed)

If the target platform's `xbl.py` doesn't exist or requires functional changes:

1. **Create a new file from scratch** (do NOT copy from Maili):
   - Use Maili's `xbl.py` as a **structural reference only** (function signatures, flow logic)
   - Do NOT copy Maili's file directly — create a new file with platform-specific imports
   - This ensures each platform has its own clean implementation

2. **Update the import statement** (line 26):
   - **CRITICAL:** Always use platform-specific import path
   - Pattern: `from lldb_pkg.target.<PLATFORM>.target_utils.pbl_load_fbc import pbl_load_fbc`
   - Example for Mavros: `from lldb_pkg.target.mavros.target_utils.pbl_load_fbc import pbl_load_fbc`
   - Each platform has its own `target_utils/pbl_load_fbc.py` module
   - Do NOT reuse another platform's module

3. **Apply documentation enhancements** (Section 5A):
   - File header: Add `@brief`, `@author`, expand `@section Description`
   - Function docstring: Add `@brief`, `@details` (8-step flow), `@param`, `@return`, `@note`
   - Inline comments: Clarify `pmic_sleep` configuration and branching logic

4. **Verify platform-specific adaptations:**
   - Confirm `g_dbgContext.openocdConfig.getBinaryPath('boot', 'xbl_sc.elf')` resolves correctly
   - Confirm `chip_config.xml` includes all required configuration elements
   - Confirm platform's own `pbl_load_fbc` module is available and correct
   - Verify no Maili-specific references remain in the code

## 6. LLDB API validation

Verify all LLDB methods used in the script exist in the target LLDB version:

```python
# Methods to validate:
- SBDebugger.CreateTarget()
- SBDebugger.HandleCommand()
- SBDebugger.SetAsync()
- SBTarget.BreakpointCreateByName()
- SBBreakpoint.SetOneShot()
- SBTarget.GetProcess()
- SBProcess.Continue()
- SBDebugger.GetSelectedTarget()
- SBProcess.GetSelectedThread()
- SBThread.GetSelectedFrame()
- SBFrame.GetPC()
```

Check against the LLDB Python bindings:
```bash
grep "def (CreateTarget|HandleCommand|SetAsync|BreakpointCreateByName|SetOneShot|GetProcess|Continue|GetSelectedTarget|GetSelectedThread|GetSelectedFrame|GetPC)" <lldb-python-path>/lldb/__init__.py
```

All methods should be present with matching names and signatures.

## 7. Verification workflow

Before declaring the port complete:

1. **Syntax check:**
   ```bash
   python -m py_compile scripts/lldb_pkg/target/<platform>/image/xbl.py
   ```

2. **Doxygen compliance:**
   - ✅ File header includes `@file`, `@brief`, `@author`, `@section Description`
   - ✅ Function docstring includes `@brief`, `@details`, `@param`, `@return`, `@note`
   - ✅ Inline comments clarify non-obvious logic blocks

3. **Logic equivalence:**
   - ✅ Control flow matches Maili's `xbl.py` exactly
   - ✅ LLDB API calls are validated against `lldb/__init__.py`
   - ✅ Breakpoint setup, process continuation, and wait logic are identical to Maili

4. **Platform correctness:**
   - ✅ Platform's `chip_config.xml` includes all required configuration elements
   - ✅ Shared `pbl_load_fbc` module is confirmed to be used by all platform image scripts
   - ✅ No hardcoded platform-specific values remain (except standard APSS core3 target)

5. **Import verification:**
   - ✅ All imports are present and match the framework's module structure
   - ✅ `pbl_load_fbc` import path is correct (either platform-specific or shared from Maili)

## 8. Output checklist

Before calling the port done, confirm:

- [ ] File header matches repo convention: SPDX line, `@file`, `@brief`, `@author`, `@section Description`
- [ ] No leftover T32 syntax or comments — all `;` comments are `#`, no `ENTRY`/`GOSUB`/`&var` left
- [ ] Function docstring includes `@brief`, `@details` (8-step flow), `@param`, `@return`, `@note`
- [ ] Inline comments clarify `pmic_sleep` configuration read and branching logic
- [ ] All LLDB API methods are validated against `lldb/__init__.py`
- [ ] Platform's `chip_config.xml` includes `<sleep_config>` and `<reset_scratch_config>` elements
- [ ] Shared `pbl_load_fbc` module import is correct and consistent with other platform image scripts
- [ ] No hardcoded platform-specific values (except standard APSS core3 target)
- [ ] Syntax check passes: `python -m py_compile <file>`
- [ ] Logic is identical to Maili's proven implementation (no functional changes)

## 9. Common pitfalls

- **Copying scripts from other platforms:** Do NOT copy Maili's xbl.py directly to the new platform. Create a new file from scratch using Maili's structure as a reference only. This ensures clean, platform-specific implementations.
- **Hardcoded ELF path:** Don't hardcode `Build/<PLATFORM>_Default_LA/...` — use `g_dbgContext.openocdConfig.getBinaryPath('boot', 'xbl_sc.elf')`
- **Wrong `pbl_load_fbc` import:** **CRITICAL** — Always use the platform-specific module path. Each platform has its own `target_utils/pbl_load_fbc.py`. Do NOT import from another platform's module.
- **Missing `chip_config.xml` elements:** Verify `<sleep_config>` and `<reset_scratch_config>` exist before declaring the port complete
- **Incomplete documentation:** Don't skip the `@details` block or `@note` section — these are required for Doxygen compliance
- **Leftover CMM syntax:** Ensure all `;` comments are converted to `#`, no `ENTRY`/`GOSUB`/`&var` remain
- **Undeclared variables:** Confirm `pmic_sleep` is always initialized before use (it's read from `chip_config.xml` in the loop)
- **Maili references in code:** Search for any remaining references to "maili" in the platform-specific file and replace with the actual platform name

## 10. Assumptions and limitations

### Assumptions
- The target platform's `chip_config.xml` includes `<sleep_config>` and `<reset_scratch_config>` elements
- The target platform has its own `target_utils/pbl_load_fbc.py` module (platform-specific implementation)
- The target platform uses the standard `sbl1_main_ctl` entry point symbol
- The target platform connects to APSS core3 (standard across all platforms)
- The target platform's image directory is clean (no scripts copied from other platforms)

### Limitations
- T32-specific constructs (AREA/window management, PBL patch-version check, interactive disassembly) are not ported
- The script connects to a hardcoded APSS core3 target; this is not configurable per platform
- Breakpoint symbol names are hardcoded (`sbl1_main_ctl`); if a future platform uses a different entry point, the script must be updated

### Critical Implementation Notes
- **Each platform must have its own `xbl.py` file** — do NOT copy from other platforms
- **Each platform must use its own `pbl_load_fbc` module** — do NOT import from another platform's module
- **Use Maili's `xbl.py` as a structural reference only** — for understanding the flow and function signatures
- **Create new files from scratch** — this ensures clean, platform-specific implementations without accidental cross-platform dependencies

### Unresolved CMM constructs
- **PBL patch-version check:** Not ported; the explicit breakpoint/connect flow is more robust
- **TCSR dload-cookie clear:** Handled generically by `pbl_load_fbc()` → `clear_dload_cookie()`, config-driven via `chip_config.xml`
- **Workarounds:** Only ported if explicitly required; most are platform-specific and intentionally omitted

## 11. Deliverables

Upon completion, provide:

1. **Enhanced/created `xbl.py`** at `scripts/lldb_pkg/target/<platform>/image/xbl.py`
   - Full Doxygen documentation
   - Config-driven for all platform-specific values
   - Reuses shared `pbl_load_fbc` module (consistent with all other platform image scripts)
   - All LLDB API calls validated

2. **Analysis document** (if significant differences found):
   - CMM → Python mapping table
   - Platform-specific differences vs. Maili
   - Assumptions and limitations
   - Verification checklist results

3. **Before/after comparison** (if documentation enhanced):
   - Show file header and function docstring changes
   - Highlight inline comment enhancements
   - Confirm no logic changes

## References

- **Maili reference implementation:** `scripts/lldb_pkg/target/maili/image/xbl.py`
- **Shared PBL bring-up module:** `scripts/lldb_pkg/target/maili/target_utils/pbl_load_fbc.py`
- **LLDB API reference:** `lldb/__init__.py` (from VSCode LLDB extension or system LLDB installation)
- **T32 → LLDB mapping:** See `convert-cmm-script` skill for comprehensive mapping table
