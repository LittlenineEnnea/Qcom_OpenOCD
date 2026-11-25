---
name: port-tz-cmm-to-python
description: Port a Qualcomm Trace32 TZ (Trusted Zone) debug script from CMM to Python LLDB, using the Maili reference implementation as the template. Use for requests like "port TZ for Mavros", "convert go_tfa_tz.cmm to Python for TZ", or "add TZ debug support for <platform>". Produces a fully documented, config-driven Python script that orchestrates TFA initialization, QSEE ELF loading, ASLR relocation, symbol mapping, and execution to the TZ entry point.
---

# TZ CMM → Python LLDB Conversion

Ports a platform's `go_tfa_tz.cmm` (Trace32 TZ debug script) into a Python LLDB command that replicates the complete TZ boot sequence: TFA initialization, QSEE ELF symbol loading, ASLR relocation, debug symbol mapping, source path configuration, breakpoint setup at TZ entry point, and execution to the TZ entry point.

**Key insight:** TZ scripts follow a standardized ASLR relocation pattern across platforms. The Maili reference implementation (`scripts/lldb_pkg/target/maili/image/tz.py`) is the canonical template — most new platforms require **minimal functional changes**, primarily import path updates and documentation enhancement.

## 0. Pre-flight checklist

Before starting the conversion:

- [ ] **Confirm the target platform's CMM sources are accessible:**
  - `run_fbc.cmm` (top-level FBC orchestrator)
  - `go_tfa_tz.cmm` (TZ-specific debug flow)
  - Ask the user to attach both via `@` file references (e.g. `@path/to/go_tfa_tz.cmm`)
- [ ] **Confirm the target platform's `chip_config.xml` exists** in the project:
  - `scripts/lldb_pkg/target/<platform>/chip_config.xml`
  - Verify it includes `<sleep_config><pmic_sleep>` element
- [ ] **Confirm the Maili reference implementation is available:**
  - `scripts/lldb_pkg/target/maili/image/tz.py` (the template for structure)
  - `scripts/lldb_pkg/target/maili/image/tfa.py` (reference for TFA initialization)
- [ ] **Confirm the target platform's directory structure exists:**
  - `scripts/lldb_pkg/target/<platform>/image/` (where `tz.py` will live)
  - `scripts/lldb_pkg/target/<platform>/image/tfa.py` (TFA module for this platform)
- [ ] **Verify the target platform's image directory is clean:**
  - Should only contain `__init__.py` and platform-specific image scripts
  - If it contains scripts from other platforms, remove them first

## 1. Analyze the CMM sources

Read both `run_fbc.cmm` and `go_tfa_tz.cmm` for the target platform. Identify:

### From `go_tfa_tz.cmm`:
- **QSEE ELF file name:** Look for `&qsee_elf="...qsee.elf"` or similar
  - Extract the ELF file name (typically `qsee.elf` across all platforms)
  - **Note:** This is resolved dynamically in Python via `g_dbgContext.openocdConfig.getBinaryPath('tz', 'qsee.elf')`, so hardcoding is not needed
- **ASLR relocation:** Confirm ASLR offset is read from memory at `aslr_reloc_offset` symbol
  - Verify it's an 8-byte little-endian value (standard pattern)
  - Confirm the relocation is applied via `SetModuleLoadAddress()`
- **Breakpoint symbols:** Identify all breakpoint symbols:
  - `tzbsp_mmu_enable` - Halt before MMU initialization
  - `tzbsp_cold_and_warm_boot_cmn` - Halt at common boot code
  - `aslr_reloc_offset` - Symbol containing ASLR offset
  - `tzbsp_boot` - TZ entry point (final breakpoint)
- **Debug symbols:** Confirm debug symbols are loaded from `.dSYM` file (standard pattern)
- **Breakpoint type:** Confirm breakpoints are hardware type with one-shot flag for entry point (standard pattern)
- **Source path mapping:** Note if source path mapping is configured (optional, non-fatal if missing)

### From `run_fbc.cmm`:
- **TZ entry point address:** Look for `&TZEntryPoint` or similar
  - **Note:** This is resolved via symbol lookup (`tzbsp_boot`), not hardcoded address
- **Boot flow sequence:** Confirm TZ follows TFA initialization
  - **Note:** TFA initialization is handled by `debugTFA()` function call

## 2. Compare against Maili reference

Create a side-by-side comparison of the target platform's CMM vs. Maili's CMM:

| Aspect | Maili | Target Platform | Impact on Python |
|---|---|---|---|
| QSEE ELF file name | `qsee.elf` | `qsee.elf` | None — resolved dynamically |
| ASLR offset symbol | `aslr_reloc_offset` | `aslr_reloc_offset` | None — standard |
| ASLR offset size | 8 bytes, little-endian | 8 bytes, little-endian | None — standard |
| MMU breakpoint | `tzbsp_mmu_enable` | `tzbsp_mmu_enable` | None — standard |
| Boot breakpoint | `tzbsp_cold_and_warm_boot_cmn` | `tzbsp_cold_and_warm_boot_cmn` | None — standard |
| Entry point | `tzbsp_boot` | `tzbsp_boot` | None — standard |
| Debug symbols | `.dSYM` | `.dSYM` | None — standard |
| Breakpoint type | Hardware, one-shot | Hardware, one-shot | None — standard |
| Source path mapping | Optional | Optional | None — graceful failure |
| TFA initialization | `debugTFA()` | `debugTFA()` | None — standard |

**Key finding:** If the target platform's CMM is structurally identical to Maili's (only import path differs), the Python port requires **zero functional changes** — only import path update and documentation enhancement.

## 3. Verify platform configuration

Check the target platform's `chip_config.xml`:

```bash
grep -A 5 "<sleep_config>" scripts/lldb_pkg/target/<platform>/chip_config.xml
```

Confirm:
- ✅ `<pmic_sleep sleep_time="...">` element exists (value can be 0 or > 0)

If the element is missing, flag it to the user — the TFA initialization (called by TZ) depends on this for runtime configuration.

## 4. Decide: new file or enhance existing?

Check if `scripts/lldb_pkg/target/<platform>/image/tz.py` already exists:

- **If it exists and is identical to Maili's:** Enhance documentation only (Section 5A)
- **If it exists but differs:** Analyze the differences; ask the user if they're intentional
- **If it doesn't exist:** Create a new file by copying Maili's template (Section 5B)

## 5A. Documentation-only enhancement (most common case)

If the target platform's `tz.py` is already functionally correct (byte-for-byte equivalent to Maili's):

1. **Enhance the file header** (lines 1–15):
   - Add `@brief` tag: "TZ (Trusted Zone) image debugging script for <PLATFORM> platform."
   - Add `@author` tag: "Qualcomm Innovation Center, Inc."
   - Expand `@section Description` with full TZ boot sequence summary including ASLR relocation

2. **Enhance the function docstring** (lines 30–100):
   - Add `@brief` tag with concise summary
   - Add `@details` block with 10-step numbered flow:
     1. Call debugTFA() to initialize TFA and reach bl31_entrypoint
     2. Load QSEE ELF (qsee.elf) with associated debug symbols (.dSYM)
     3. Find key symbols for breakpoints and ASLR relocation
     4. Set hardware breakpoint at tzbsp_mmu_enable
     5. Resume and halt at tzbsp_mmu_enable
     6. Set hardware breakpoint at tzbsp_cold_and_warm_boot_cmn
     7. Resume and halt at tzbsp_cold_and_warm_boot_cmn
     8. Read ASLR relocation offset from memory (8-byte little-endian)
     9. Relocate QSEE module using ASLR offset
     10. Set one-shot hardware breakpoint at tzbsp_boot and resume
   - Keep all `@param` tags; clarify that `command`, `exe_ctx`, `result` are unused
   - Add `@return` tag documenting return behavior and exit conditions
   - Add `@exception` tag documenting SystemExit conditions
   - Add `@note` block (6 bullet points):
     - Requires g_dbgContext to be initialized with chipset name and configuration
     - Depends on debugTFA() to establish target connection and reach TFA entry
     - QSEE ELF path is resolved via g_dbgContext.openocdConfig.getBinaryPath()
     - ASLR relocation is required for proper symbol resolution in TZ
     - Source path mapping is optional; failures are logged but non-fatal
     - The breakpoint at tzbsp_boot is set as one-shot to avoid re-triggering

3. **Enhance inline comments** (lines 95–210):
   - Clarify each step with descriptive comments
   - Explain why each operation is performed
   - Document the ASLR relocation mechanism

4. **Verify no logic changes:**
   - All control flow, LLDB API calls, imports, and breakpoint names remain identical to Maili's
   - Only docstrings and comments are modified

## 5B. New file creation (if needed)

If the target platform's `tz.py` doesn't exist or requires functional changes:

1. **Create a new file from scratch** (do NOT copy from Maili):
   - Use Maili's `tz.py` as a **structural reference only** (function signatures, flow logic)
   - Do NOT copy Maili's file directly — create a new file with platform-specific imports
   - This ensures each platform has its own clean implementation

2. **Update the import statement** (line 42):
   - **CRITICAL:** Always use platform-specific import path for TFA module
   - Pattern: `from lldb_pkg.target.<PLATFORM>.image.tfa import debugTFA`
   - Example for Mavros: `from lldb_pkg.target.mavros.image.tfa import debugTFA`
   - Each platform has its own `image/tfa.py` module
   - Do NOT reuse another platform's module

3. **Apply documentation enhancements** (Section 5A):
   - File header: Add `@brief`, `@author`, expand `@section Description`
   - Function docstring: Add `@brief`, `@details` (10-step flow), `@param`, `@return`, `@exception`, `@note`
   - Inline comments: Clarify each step

4. **Verify platform-specific adaptations:**
   - Confirm `g_dbgContext.openocdConfig.getBinaryPath('tz', 'qsee.elf')` resolves correctly
   - Confirm platform's own `tfa.py` module is available and correct
   - Verify no Maili-specific references remain in the code
   - Confirm all symbol names match the target platform's QSEE ELF

## 6. ASLR relocation verification

The TZ script's core complexity is ASLR relocation. Verify the implementation:

1. **Symbol resolution:**
   - All four symbols must be found: `tzbsp_mmu_enable`, `tzbsp_cold_and_warm_boot_cmn`, `aslr_reloc_offset`, `tzbsp_boot`
   - If any symbol is missing, the script will fail with a clear error message

2. **Memory read:**
   - ASLR offset is read as 8 bytes from `aslr_reloc_offset_addr`
   - Bytes are converted to little-endian integer
   - Integer is converted to hex string for `SetModuleLoadAddress()`

3. **Module relocation:**
   - `SetModuleLoadAddress()` is called with the ASLR offset
   - This adjusts all symbol addresses in the QSEE module

4. **Breakpoint sequence:**
   - First breakpoint at `tzbsp_mmu_enable` halts before MMU setup
   - Second breakpoint at `tzbsp_cold_and_warm_boot_cmn` halts at common boot code
   - At this point, ASLR offset is read and module is relocated
   - Final breakpoint at `tzbsp_boot` uses the relocated symbols

## 7. LLDB API validation

Verify all LLDB methods used in the script exist in the target LLDB version:

```python
# Methods to validate:
- SBDebugger.HandleCommand()
- SBDebugger.GetSelectedTarget()
- SBTarget.AddModule()
- SBTarget.SetModuleLoadAddress()
- SBTarget.GetProcess()
- SBModule.FindSymbols()
- SBSymbol.GetValue()
- SBProcess.ReadMemory()
- SBProcess.Continue()
- SBError.Fail()
- SBError.GetCString()
```

Check against the LLDB Python bindings:
```bash
grep "def (HandleCommand|AddModule|SetModuleLoadAddress|FindSymbols|ReadMemory)" <lldb-python-path>/lldb/__init__.py
```

All methods should be present with matching names and signatures.

## 8. Verification workflow

Before declaring the port complete:

1. **Syntax check:**
   ```bash
   python -m py_compile scripts/lldb_pkg/target/<platform>/image/tz.py
   ```

2. **Doxygen compliance:**
   - ✅ File header includes `@file`, `@brief`, `@author`, `@section Description`
   - ✅ Function docstring includes `@brief`, `@details`, `@param`, `@return`, `@exception`, `@note`
   - ✅ Inline comments clarify each step

3. **Logic equivalence:**
   - ✅ Control flow matches Maili's `tz.py` exactly
   - ✅ LLDB API calls are validated against `lldb/__init__.py`
   - ✅ Symbol resolution, ASLR relocation, and breakpoint setup are identical to Maili

4. **Platform correctness:**
   - ✅ Platform's `chip_config.xml` includes `<sleep_config>` element
   - ✅ Platform's `tfa.py` module is confirmed to be available
   - ✅ No hardcoded platform-specific values remain (except standard symbol names)
   - ✅ All symbol names match the target platform's QSEE ELF

5. **Import verification:**
   - ✅ All imports are present and match the framework's module structure
   - ✅ `debugTFA` import path is correct (platform-specific)

6. **ASLR relocation verification:**
   - ✅ All four required symbols are resolved
   - ✅ Memory read error handling is in place
   - ✅ Module relocation error handling is in place
   - ✅ ASLR offset conversion (little-endian) is correct

## 9. Output checklist

Before calling the port done, confirm:

- [ ] File header matches repo convention: SPDX line, `@file`, `@brief`, `@author`, `@section Description`
- [ ] No leftover T32 syntax or comments — all `;` comments are `#`, no `ENTRY`/`GOSUB`/`&var` left
- [ ] Function docstring includes `@brief`, `@details` (10-step flow), `@param`, `@return`, `@exception`, `@note`
- [ ] Inline comments clarify each step and explain why each operation is performed
- [ ] All LLDB API methods are validated against `lldb/__init__.py`
- [ ] Platform's `chip_config.xml` includes `<sleep_config>` element
- [ ] Platform's `tfa.py` module is confirmed to be available
- [ ] No hardcoded platform-specific values (except standard symbol names)
- [ ] Syntax check passes: `python -m py_compile <file>`
- [ ] Logic is identical to Maili's proven implementation (no functional changes)
- [ ] Import path is correct: `from lldb_pkg.target.<platform>.image.tfa import debugTFA`
- [ ] All four required symbols are documented: `tzbsp_mmu_enable`, `tzbsp_cold_and_warm_boot_cmn`, `aslr_reloc_offset`, `tzbsp_boot`
- [ ] ASLR relocation logic is correct: 8-byte little-endian read, hex conversion, module relocation
- [ ] Module exports updated: `__init__.py` includes "tz" in `__all__`

## 10. Common pitfalls

- **Copying scripts from other platforms:** Do NOT copy Maili's tz.py directly to the new platform. Create a new file from scratch using Maili's structure as a reference only. This ensures clean, platform-specific implementations.
- **Hardcoded QSEE path:** Don't hardcode `qsee.elf` path — use `g_dbgContext.openocdConfig.getBinaryPath('tz', 'qsee.elf')`
- **Wrong TFA import:** **CRITICAL** — Always use the platform-specific module path. Each platform has its own `image/tfa.py`. Do NOT import from another platform's module.
- **Missing symbol names:** Verify all four symbols exist in the target platform's QSEE ELF: `tzbsp_mmu_enable`, `tzbsp_cold_and_warm_boot_cmn`, `aslr_reloc_offset`, `tzbsp_boot`
- **ASLR offset size:** Confirm ASLR offset is 8 bytes (64-bit), not 4 bytes (32-bit)
- **Endianness:** Confirm ASLR offset is little-endian (standard for ARM platforms)
- **Missing `chip_config.xml` elements:** Verify `<sleep_config>` exists before declaring the port complete
- **Incomplete documentation:** Don't skip the `@details` block or `@note` section — these are required for Doxygen compliance
- **Leftover CMM syntax:** Ensure all `;` comments are converted to `#`, no `ENTRY`/`GOSUB`/`&var` remain
- **Maili references in code:** Search for any remaining references to "maili" in the platform-specific file and replace with the actual platform name
- **Missing module export:** Update `__init__.py` to include "tz" in `__all__`

## 11. Assumptions and limitations

### Assumptions
- The target platform's `chip_config.xml` includes `<sleep_config>` element
- The target platform has its own `image/tfa.py` module (platform-specific implementation)
- The target platform uses the standard TZ symbol names: `tzbsp_mmu_enable`, `tzbsp_cold_and_warm_boot_cmn`, `aslr_reloc_offset`, `tzbsp_boot`
- The target platform's QSEE ELF file is named `qsee.elf` (standard across all platforms)
- The target platform's ASLR offset is 8 bytes, little-endian (standard across all platforms)
- The target platform's image directory is clean (no scripts copied from other platforms)

### Limitations
- T32-specific constructs (AREA/window management, interactive disassembly) are not ported
- The script depends on TFA initialization via `debugTFA()` — if TFA debug is not available, TZ debug will fail
- Breakpoint symbol names are hardcoded; if a future platform uses different symbol names, the script must be updated
- ASLR relocation assumes 8-byte little-endian offset; if a platform uses a different format, the script must be updated
- PIL integration is not included; if needed for a platform, it must be added separately

### Critical Implementation Notes
- **Each platform must have its own `tz.py` file** — do NOT copy from other platforms
- **Each platform must use its own `tfa.py` module** — do NOT import from another platform's module
- **Use Maili's `tz.py` as a structural reference only** — for understanding the flow and function signatures
- **Create new files from scratch** — this ensures clean, platform-specific implementations without accidental cross-platform dependencies
- **Verify all symbol names** — do NOT assume symbol names are identical across platforms; verify against the target platform's QSEE ELF

## 12. Deliverables

Upon completion, provide:

1. **Enhanced/created `tz.py`** at `scripts/lldb_pkg/target/<platform>/image/tz.py`
   - Full Doxygen documentation
   - Config-driven for all platform-specific values
   - All LLDB API calls validated
   - Platform-specific TFA import
   - Comprehensive ASLR relocation logic

2. **Updated `__init__.py`** at `scripts/lldb_pkg/target/<platform>/image/__init__.py`
   - Added "tz" to `__all__` exports

3. **Analysis document** (if significant differences found):
   - CMM → Python mapping table
   - Platform-specific differences vs. Maili
   - Symbol name verification
   - ASLR relocation mechanism verification
   - Assumptions and limitations
   - Verification checklist results

4. **Before/after comparison** (if documentation enhanced):
   - Show file header and function docstring changes
   - Highlight inline comment enhancements
   - Confirm no logic changes

## References

- **Maili reference implementation:** `scripts/lldb_pkg/target/maili/image/tz.py`
- **Maili TFA module:** `scripts/lldb_pkg/target/maili/image/tfa.py`
- **LLDB API reference:** `lldb/__init__.py` (from VSCode LLDB extension or system LLDB installation)
- **T32 → LLDB mapping:** See `convert-cmm-script` skill for comprehensive mapping table
- **TFA porting skill:** See `port-tfa-cmm-to-python` skill for TFA-specific porting guidance
- **XBL porting skill:** See `port-xbl-cmm-to-python` skill for XBL-specific porting guidance