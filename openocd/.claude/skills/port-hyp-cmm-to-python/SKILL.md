---
name: port-hyp-cmm-to-python
description: Port a Qualcomm Trace32 HYP (Hypervisor/Gunyah) debug script from CMM to Python LLDB, using the Maili reference implementation as the template. Use for requests like "port HYP for Mavros", "convert go_hyp10.cmm to Python", or "add HYP debug support for <platform>". Produces a fully documented, config-driven Python script that orchestrates Hypervisor image loading, symbol loading at physical and virtual addresses, KASLR offset calculation, PIL module loading, and execution to the HYP entry point.
---

# HYP CMM → Python LLDB Conversion

Ports a platform's `go_hyp10.cmm` (Trace32 Hypervisor debug script) into a Python LLDB command that replicates the complete Hypervisor boot sequence: TZ initialization, symbol loading at physical addresses (pre-MMU), MMU enablement, KASLR offset calculation, symbol reloading at virtual addresses (post-MMU), PIL module loading, breakpoint setup at HYP entry point, and execution to the HYP entry point.

**Key insight:** HYP scripts follow a standardized two-phase symbol loading pattern with KASLR relocation across platforms. The Maili reference implementation (`scripts/lldb_pkg/target/maili/image/hyp.py`) is the canonical template — most new platforms require **minimal functional changes**, primarily import path updates and PIL path adjustments.

## 0. Pre-flight checklist

Before starting the conversion:

- [ ] **Confirm the target platform's CMM sources are accessible:**
  - `run_fbc.cmm` (top-level FBC orchestrator)
  - `go_hyp10.cmm` (HYP-specific debug flow)
  - Ask the user to attach both via `@` file references (e.g. `@path/to/go_hyp10.cmm`)
- [ ] **Confirm the target platform's `chip_config.xml` exists** in the project:
  - `scripts/lldb_pkg/target/<platform>/chip_config.xml`
  - Verify it includes `<hyp_config>` element with entry vector addresses
- [ ] **Confirm the Maili reference implementation is available:**
  - `scripts/lldb_pkg/target/maili/image/hyp.py` (the template for structure)
  - `scripts/lldb_pkg/target/maili/image/tz.py` (reference for TZ initialization)
- [ ] **Confirm the target platform's directory structure exists:**
  - `scripts/lldb_pkg/target/<platform>/image/` (where `hyp.py` will live)
  - `scripts/lldb_pkg/target/<platform>/image/tz.py` (TZ module for this platform)
  - `scripts/lldb_pkg/target/<platform>/image/debugwithpil.py` (PIL debugging module)
- [ ] **Verify the target platform's image directory is clean:**
  - Should only contain `__init__.py` and platform-specific image scripts
  - If it contains scripts from other platforms, remove them first

## 1. Analyze the CMM sources

Read both `run_fbc.cmm` and `go_hyp10.cmm` for the target platform. Identify:

### From `go_hyp10.cmm`:
- **HYP ELF file name:** Look for `&HYP_ELF="..."` or similar
  - Extract the ELF file name (typically `hypvm.elf` across all platforms)
  - **Note:** This is resolved dynamically in Python via `g_dbgContext.openocdConfig.getBinaryPath('hyp', 'hypvm.elf')`, so hardcoding is not needed
- **PIL module paths:** Identify the build directory structure
  - Look for `data.load.elf &HYP_BUILDROOT\hypervisor_images\out\<PLATFORM>LA\...` patterns
  - Extract the platform-specific build path fragment (e.g., `MAVROSLA` vs. `MAILILA`)
  - Identify PIL module addresses (typically `0x8080d000` for resource-manager, `0x80800000` for runtime)
- **KASLR calculation:** Confirm KASLR offset is calculated from PC
  - Verify the mask used: typically `~0x1fffff` (clears lower 21 bits)
  - Confirm the calculation: `(pc & (~0x1fffff))`
- **Breakpoint symbols:** Identify all breakpoint symbols:
  - `boot_cold_init` - Halt at boot cold init
  - `init_qheebsp` - Halt at QHEE BSP initialization (for PIL debugging)
- **Symbol loading phases:** Confirm two-phase symbol loading:
  - Phase 1: Load symbols at physical address 0x80000000 (pre-MMU)
  - Phase 2: Reload symbols with KASLR slide offset (post-MMU)
- **Entry point addresses:** Look for entry vector addresses
  - GUNYAH entry: typically 0x80000000
  - QHEE entry: typically 0x80080000
- **Debug symbols:** Confirm debug symbols are loaded from `.dSYM` file (standard pattern)
- **Breakpoint type:** Confirm breakpoints are hardware type (standard pattern)

### From `run_fbc.cmm`:
- **HYP entry point address:** Look for `&HYPEntryPoint` or similar
  - **Note:** This is resolved via `chip_config.xml`, not hardcoded address
- **Boot flow sequence:** Confirm HYP follows TZ initialization
  - **Note:** TZ initialization is handled by `debugTZ()` function call

## 2. Compare against Maili reference

Create a side-by-side comparison of the target platform's CMM vs. Maili's CMM:

| Aspect | Maili | Target Platform | Impact on Python |
|---|---|---|---|
| HYP ELF file name | `hypvm.elf` | `hypvm.elf` | None — resolved dynamically |
| GUNYAH entry vector | 0x80000000 | 0x80000000 | None — from chip_config.xml |
| QHEE entry vector | 0x80080000 | 0x80080000 | None — from chip_config.xml |
| PIL build path | `MAILILA` | `<PLATFORM>LA` | ✓ Update constants |
| PIL RM address | 0x8080d000 | 0x8080d000 | None — standard |
| PIL runtime address | 0x80800000 | 0x80800000 | None — standard |
| KASLR mask | `~0x1fffff` | `~0x1fffff` | None — standard |
| Boot cold init BP | `boot_cold_init` | `boot_cold_init` | None — standard |
| Init QHEEBSP BP | `init_qheebsp` | `init_qheebsp` | None — standard |
| Symbol loading phases | 2 (physical + virtual) | 2 (physical + virtual) | None — standard |
| Debug symbols | `.dSYM` | `.dSYM` | None — standard |
| Breakpoint type | Hardware | Hardware | None — standard |
| TZ initialization | `debugTZ()` | `debugTZ()` | None — standard |

**Key finding:** If the target platform's CMM is structurally identical to Maili's (only PIL build path differs), the Python port requires **minimal functional changes** — primarily PIL path updates and import path updates.

## 3. Verify platform configuration

Check the target platform's `chip_config.xml`:

```bash
grep -A 5 "<hyp_config>" scripts/lldb_pkg/target/<platform>/chip_config.xml
```

Confirm:
- ✅ `<GUNY_ENTRY_VECTOR_ADDR address="...">` element exists (typically 0x80000000)
- ✅ `<QHEE_ENTRY_VECTOR_ADDR address="...">` element exists (typically 0x80080000)

If either element is missing, flag it to the user — the HYP initialization depends on these for runtime configuration.

## 4. Decide: new file or enhance existing?

Check if `scripts/lldb_pkg/target/<platform>/image/hyp.py` already exists:

- **If it exists and is identical to Maili's:** Enhance documentation and update PIL paths (Section 5A)
- **If it exists but differs:** Analyze the differences; ask the user if they're intentional
- **If it doesn't exist:** Create a new file by copying Maili's template (Section 5B)

## 5A. Documentation and PIL path enhancement (most common case)

If the target platform's `hyp.py` is already functionally correct (structurally equivalent to Maili's):

1. **Update PIL path constants** (lines 92–93):
   - Change `PIL_RESOURCE_MANAGER_PATH` from `MAILILA` to `<PLATFORM>LA`
   - Change `PIL_RUNTIME_PATH` from `MAILILA` to `<PLATFORM>LA`
   - Example for Mavros: `MAILILA` → `MAVROSLA`

2. **Enhance the file header** (lines 1–59):
   - Add `@file` tag with filename
   - Add `@brief` tag: "Hypervisor image loading support for <PLATFORM> platform."
   - Expand `@section Description` with full HYP boot sequence summary including KASLR relocation
   - Add `@section Execution Flow` with 10-step numbered flow
   - Add `@section Configuration` documenting entry vector addresses
   - Add `@section LLDB Integration` documenting API usage
   - Add `@section Dependencies` listing all required modules

3. **Enhance the function docstrings:**
   - `get_pc_address_type()`: Add `@brief`, `@param`, `@return`, `@exception` tags
   - `debugHYP()`: Add `@brief`, `@param`, `@return`, `@exception` tags with detailed descriptions

4. **Verify no logic changes:**
   - All control flow, LLDB API calls, imports, and breakpoint names remain identical to Maili's
   - Only PIL paths, docstrings, and comments are modified

## 5B. New file creation (if needed)

If the target platform's `hyp.py` doesn't exist or requires functional changes:

1. **Create a new file from scratch** (do NOT copy from Maili):
   - Use Maili's `hyp.py` as a **structural reference only** (function signatures, flow logic)
   - Do NOT copy Maili's file directly — create a new file with platform-specific imports and paths
   - This ensures each platform has its own clean implementation

2. **Update the import statements** (lines 71–73):
   - **CRITICAL:** Always use platform-specific import paths for TZ and PIL modules
   - Pattern: `from lldb_pkg.target.<PLATFORM>.image.tz import debugTZ`
   - Pattern: `from lldb_pkg.target.<PLATFORM>.image.debugwithpil import pil_debugger`
   - Example for Mavros: `from lldb_pkg.target.mavros.image.tz import debugTZ`
   - Each platform has its own `image/tz.py` and `image/debugwithpil.py` modules
   - Do NOT reuse another platform's modules

3. **Update PIL path constants** (lines 92–93):
   - Set `PIL_RESOURCE_MANAGER_PATH` to platform-specific build path
   - Set `PIL_RUNTIME_PATH` to platform-specific build path
   - Example for Mavros: `hypervisor_images/out/MAVROSLA/core/bsp/gunyah-hypervisor/build/MAVROSLA/resource-manager_debug`

4. **Apply documentation enhancements** (Section 5A):
   - File header: Add `@file`, `@brief`, expand `@section` tags
   - Function docstrings: Add `@brief`, `@param`, `@return`, `@exception` tags
   - Inline comments: Clarify each step

5. **Verify platform-specific adaptations:**
   - Confirm `g_dbgContext.openocdConfig.getBinaryPath('hyp', 'hypvm.elf')` resolves correctly
   - Confirm platform's own `tz.py` module is available and correct
   - Confirm platform's own `debugwithpil.py` module is available and correct
   - Verify no Maili-specific references remain in the code
   - Confirm all PIL paths match the target platform's build directory structure

## 6. Two-phase symbol loading verification

The HYP script's core complexity is two-phase symbol loading with KASLR relocation. Verify the implementation:

1. **Phase 1 (Physical Address):**
   - Load HYP ELF file (hypvm.elf)
   - Add module to target
   - Set module load address at 0x80000000 (physical address)
   - Verify module index is validated before access

2. **Phase 2 (Virtual Address with KASLR):**
   - Enable MMU and continue execution
   - Read PC in virtual address space
   - Calculate KASLR offset: `(pc & 0xFFFFFFFFFFE00000)`
   - Reload symbols with KASLR slide offset
   - Verify KASLR mask is correct (clears lower 21 bits)

3. **PIL Loading:**
   - Load resource-manager_debug at 0x8080d000
   - Load runtime at 0x80800000
   - Verify PIL paths match platform's build directory

4. **Breakpoint sequence:**
   - Set hardware breakpoints at GUNYAH and QHEE entry vectors
   - Detect which entry vector was hit
   - Load symbols at physical address
   - Enable MMU and calculate KASLR
   - Reload symbols with KASLR slide
   - Set breakpoint at boot_cold_init
   - Optionally set breakpoint at init_qheebsp for PIL debugging

## 7. LLDB API validation

Verify all LLDB methods used in the script exist in the target LLDB version:

```python
# Methods to validate:
- SBDebugger.HandleCommand()
- SBDebugger.GetCommandInterpreter()
- SBDebugger.GetSelectedTarget()
- SBCommandInterpreter.HandleCommand()
- SBCommandReturnObject.Succeeded()
- SBCommandReturnObject.GetOutput()
- SBTarget.AddModule()
- SBTarget.SetModuleLoadAddress()
- SBTarget.modules
- SBModule (container access)
```

Check against the LLDB Python bindings:
```bash
grep "def (HandleCommand|GetCommandInterpreter|GetSelectedTarget|AddModule|SetModuleLoadAddress)" <lldb-python-path>/lldb/__init__.py
```

All methods should be present with matching names and signatures.

## 8. Verification workflow

Before declaring the port complete:

1. **Syntax check:**
   ```bash
   python -m py_compile scripts/lldb_pkg/target/<platform>/image/hyp.py
   ```

2. **Doxygen compliance:**
   - ✅ File header includes `@file`, `@brief`, `@section` tags
   - ✅ Function docstrings include `@brief`, `@param`, `@return`, `@exception` tags
   - ✅ Inline comments clarify each step

3. **Logic equivalence:**
   - ✅ Control flow matches Maili's `hyp.py` exactly
   - ✅ LLDB API calls are validated against `lldb/__init__.py`
   - ✅ Symbol loading phases, KASLR calculation, and breakpoint setup are identical to Maili

4. **Platform correctness:**
   - ✅ Platform's `chip_config.xml` includes `<hyp_config>` element
   - ✅ Platform's `tz.py` module is confirmed to be available
   - ✅ Platform's `debugwithpil.py` module is confirmed to be available
   - ✅ No hardcoded platform-specific values remain (except standard entry addresses)
   - ✅ PIL paths match the target platform's build directory structure

5. **Import verification:**
   - ✅ All imports are present and match the framework's module structure
   - ✅ `debugTZ` import path is correct (platform-specific)
   - ✅ `pil_debugger` import path is correct (platform-specific)

6. **PIL path verification:**
   - ✅ `PIL_RESOURCE_MANAGER_PATH` uses correct platform build directory
   - ✅ `PIL_RUNTIME_PATH` uses correct platform build directory
   - ✅ PIL addresses (0x8080d000, 0x80800000) are correct

7. **KASLR verification:**
   - ✅ KASLR mask is correct: 0xFFFFFFFFFFE00000 (clears lower 21 bits)
   - ✅ KASLR calculation is correct: `(pc & KASLR_MASK)`
   - ✅ KASLR slide is applied via `target modules load --slide`

## 9. Output checklist

Before calling the port done, confirm:

- [ ] File header matches repo convention: SPDX line, `@file`, `@brief`, `@section` tags
- [ ] No leftover T32 syntax or comments — all `;` comments are `#`, no `ENTRY`/`GOSUB`/`&var` left
- [ ] Function docstrings include `@brief`, `@param`, `@return`, `@exception` tags
- [ ] Inline comments clarify each step and explain why each operation is performed
- [ ] All LLDB API methods are validated against `lldb/__init__.py`
- [ ] Platform's `chip_config.xml` includes `<hyp_config>` element with entry vector addresses
- [ ] Platform's `tz.py` module is confirmed to be available
- [ ] Platform's `debugwithpil.py` module is confirmed to be available
- [ ] No hardcoded platform-specific values (except standard entry addresses)
- [ ] Syntax check passes: `python -m py_compile <file>`
- [ ] Logic is identical to Maili's proven implementation (no functional changes)
- [ ] Import paths are correct: `from lldb_pkg.target.<platform>.image.tz import debugTZ`
- [ ] PIL paths are correct: `PIL_RESOURCE_MANAGER_PATH` and `PIL_RUNTIME_PATH` use platform build directory
- [ ] KASLR mask is correct: 0xFFFFFFFFFFE00000
- [ ] Entry vector addresses are loaded from `chip_config.xml` (not hardcoded)
- [ ] Module index safety check is in place (validates module count before access)
- [ ] Module exports updated: `__init__.py` includes "hyp" in `__all__`

## 10. Common pitfalls

- **Copying scripts from other platforms:** Do NOT copy Maili's hyp.py directly to the new platform. Create a new file from scratch using Maili's structure as a reference only. This ensures clean, platform-specific implementations.
- **Hardcoded HYP path:** Don't hardcode `hypvm.elf` path — use `g_dbgContext.openocdConfig.getBinaryPath('hyp', 'hypvm.elf')`
- **Wrong TZ import:** **CRITICAL** — Always use the platform-specific module path. Each platform has its own `image/tz.py`. Do NOT import from another platform's module.
- **Wrong PIL import:** **CRITICAL** — Always use the platform-specific module path. Each platform has its own `image/debugwithpil.py`. Do NOT import from another platform's module.
- **Incorrect PIL paths:** Verify PIL paths match the target platform's build directory structure (e.g., `MAVROSLA` vs. `MAILILA`)
- **Wrong KASLR mask:** Confirm KASLR mask is 0xFFFFFFFFFFE00000 (clears lower 21 bits), not 0x1fffff
- **Missing module index check:** Validate module count before accessing `selTarget.modules[3]`
- **Missing `chip_config.xml` elements:** Verify `<hyp_config>` exists before declaring the port complete
- **Incomplete documentation:** Don't skip the `@section` tags or function docstrings — these are required for Doxygen compliance
- **Leftover CMM syntax:** Ensure all `;` comments are converted to `#`, no `ENTRY`/`GOSUB`/`&var` remain
- **Maili references in code:** Search for any remaining references to "maili" in the platform-specific file and replace with the actual platform name
- **Missing module export:** Update `__init__.py` to include "hyp" in `__all__`

## 11. Assumptions and limitations

### Assumptions
- The target platform's `chip_config.xml` includes `<hyp_config>` element with entry vector addresses
- The target platform has its own `image/tz.py` module (platform-specific implementation)
- The target platform has its own `image/debugwithpil.py` module (platform-specific implementation)
- The target platform uses the standard HYP entry addresses: 0x80000000 (GUNYAH), 0x80080000 (QHEE)
- The target platform uses the standard breakpoint symbols: `boot_cold_init`, `init_qheebsp`
- The target platform's HYP ELF file is named `hypvm.elf` (standard across all platforms)
- The target platform's PIL addresses are 0x8080d000 (resource-manager) and 0x80800000 (runtime)
- The target platform's KASLR mask is 0xFFFFFFFFFFE00000 (clears lower 21 bits)
- The target platform's image directory is clean (no scripts copied from other platforms)

### Limitations
- T32-specific constructs (AREA/window management, interactive disassembly) are not ported
- The script depends on TZ initialization via `debugTZ()` — if TZ debug is not available, HYP debug will fail
- QHEE entry vector is not yet implemented (placeholder only)
- Module index is hardcoded (module4) — if module count differs, the script must be updated
- Breakpoint symbol names are hardcoded; if a future platform uses different symbol names, the script must be updated
- KASLR relocation assumes 0xFFFFFFFFFFE00000 mask; if a platform uses a different mask, the script must be updated
- PIL integration depends on `debugwithpil.py` module; if not available, PIL debugging will fail

### Critical Implementation Notes
- **Each platform must have its own `hyp.py` file** — do NOT copy from other platforms
- **Each platform must use its own `tz.py` module** — do NOT import from another platform's module
- **Each platform must use its own `debugwithpil.py` module** — do NOT import from another platform's module
- **Use Maili's `hyp.py` as a structural reference only** — for understanding the flow and function signatures
- **Create new files from scratch** — this ensures clean, platform-specific implementations without accidental cross-platform dependencies
- **Verify all PIL paths** — do NOT assume PIL paths are identical across platforms; verify against the target platform's build directory

## 12. Deliverables

Upon completion, provide:

1. **Enhanced/created `hyp.py`** at `scripts/lldb_pkg/target/<platform>/image/hyp.py`
   - Full Doxygen documentation
   - Config-driven for all platform-specific values
   - All LLDB API calls validated
   - Platform-specific TZ and PIL imports
   - Comprehensive two-phase symbol loading logic
   - Correct PIL paths for target platform

2. **Updated `__init__.py`** at `scripts/lldb_pkg/target/<platform>/image/__init__.py`
   - Added "hyp" to `__all__` exports

3. **Analysis document** (if significant differences found):
   - CMM → Python mapping table
   - Platform-specific differences vs. Maili
   - PIL path verification
   - KASLR calculation verification
   - Assumptions and limitations
   - Verification checklist results

4. **Before/after comparison** (if documentation enhanced):
   - Show file header and function docstring changes
   - Highlight PIL path updates
   - Confirm no logic changes

## References

- **Maili reference implementation:** `scripts/lldb_pkg/target/maili/image/hyp.py`
- **Maili TZ module:** `scripts/lldb_pkg/target/maili/image/tz.py`
- **Maili PIL module:** `scripts/lldb_pkg/target/maili/image/debugwithpil.py`
- **LLDB API reference:** `lldb/__init__.py` (from VSCode LLDB extension or system LLDB installation)
- **T32 → LLDB mapping:** See `convert-cmm-script` skill for comprehensive mapping table
- **TZ porting skill:** See `port-tz-cmm-to-python` skill for TZ-specific porting guidance
- **TFA porting skill:** See `port-tfa-cmm-to-python` skill for TFA-specific porting guidance
- **XBL porting skill:** See `port-xbl-cmm-to-python` skill for XBL-specific porting guidance
