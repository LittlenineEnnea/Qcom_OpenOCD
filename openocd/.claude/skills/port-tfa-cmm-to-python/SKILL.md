---
name: port-tfa-cmm-to-python
description: Port a Qualcomm Trace32 TFA (Trusted Firmware-A) debug script from CMM to Python LLDB, using the Maili reference implementation as the template. Use for requests like "port TFA for Mavros", "convert go_tfa_tz.cmm to Python", or "add TFA debug support for <platform>". Produces a fully documented, config-driven Python script that orchestrates XBL initialization, TFA ELF loading, symbol mapping, and execution to the TFA entry point.
---

# TFA CMM → Python LLDB Conversion

Ports a platform's `go_tfa_tz.cmm` (Trace32 TFA debug script) into a Python LLDB command that replicates the complete TFA boot sequence: XBL initialization, TFA ELF symbol loading, debug symbol mapping, source path configuration, breakpoint setup at TFA entry point, and execution to the TFA entry point.

**Key insight:** TFA scripts are highly standardized across platforms. The Maili reference implementation (`scripts/lldb_pkg/target/maili/image/tfa.py`) is the canonical template — most new platforms require **zero functional changes**, only import path updates and documentation enhancement.

## 0. Pre-flight checklist

Before starting the conversion:

- [ ] **Confirm the target platform's CMM sources are accessible:**
  - `run_fbc.cmm` (top-level FBC orchestrator)
  - `go_tfa_tz.cmm` (TFA-specific debug flow)
  - Ask the user to attach both via `@` file references (e.g. `@path/to/go_tfa_tz.cmm`)
- [ ] **Confirm the target platform's `chip_config.xml` exists** in the project:
  - `scripts/lldb_pkg/target/<platform>/chip_config.xml`
  - Verify it includes `<sleep_config><pmic_sleep>` element
- [ ] **Confirm the Maili reference implementation is available:**
  - `scripts/lldb_pkg/target/maili/image/tfa.py` (the template for structure)
  - `scripts/lldb_pkg/target/maili/image/xbl.py` (reference for XBL initialization)
- [ ] **Confirm the target platform's directory structure exists:**
  - `scripts/lldb_pkg/target/<platform>/image/` (where `tfa.py` will live)
  - `scripts/lldb_pkg/target/<platform>/image/xbl.py` (XBL module for this platform)
- [ ] **Verify the target platform's image directory is clean:**
  - Should only contain `__init__.py` and platform-specific image scripts
  - If it contains scripts from other platforms, remove them first

## 1. Analyze the CMM sources

Read both `run_fbc.cmm` and `go_tfa_tz.cmm` for the target platform. Identify:

### From `go_tfa_tz.cmm`:
- **ELF file name:** Look for `&tfa_elf="...bl31.elf"` or similar
  - Extract the ELF file name (typically `bl31.elf` across all platforms)
  - **Note:** This is resolved dynamically in Python via `g_dbgContext.openocdConfig.getBinaryPath('tfa', 'bl31.elf')`, so hardcoding is not needed
- **Load address:** Confirm the load address is `0x0` (base address, standard across all platforms)
- **Debug symbols:** Confirm debug symbols are loaded from `.dSYM` file (standard pattern)
- **Entry point symbol:** Confirm the entry point is `bl31_entrypoint` (standard across all platforms)
- **Breakpoint type:** Confirm breakpoint is hardware type with one-shot flag (standard pattern)
- **Source path mapping:** Note if source path mapping is configured (optional, non-fatal if missing)

### From `run_fbc.cmm`:
- **TFA entry point address:** Look for `&TFAEntryPoint` or similar
  - **Note:** This is resolved via symbol lookup (`bl31_entrypoint`), not hardcoded address
- **Boot flow sequence:** Confirm TFA follows XBL initialization
  - **Note:** XBL initialization is handled by `debugXBL()` function call

## 2. Compare against Maili reference

Create a side-by-side comparison of the target platform's CMM vs. Maili's CMM:

| Aspect | Maili | Target Platform | Impact on Python |
|---|---|---|---|
| ELF file name | `bl31.elf` | `bl31.elf` | None — resolved dynamically |
| Load address | `0x0` | `0x0` | None — standard |
| Debug symbols | `.dSYM` | `.dSYM` | None — standard |
| Entry point | `bl31_entrypoint` | `bl31_entrypoint` | None — standard |
| Breakpoint type | Hardware, one-shot | Hardware, one-shot | None — standard |
| Source path mapping | Optional | Optional | None — graceful failure |
| XBL initialization | `debugXBL()` | `debugXBL()` | None — standard |

**Key finding:** If the target platform's CMM is structurally identical to Maili's (only import path differs), the Python port requires **zero functional changes** — only import path update and documentation enhancement.

## 3. Verify platform configuration

Check the target platform's `chip_config.xml`:

```bash
grep -A 5 "<sleep_config>" scripts/lldb_pkg/target/<platform>/chip_config.xml
```

Confirm:
- ✅ `<pmic_sleep sleep_time="...">` element exists (value can be 0 or > 0)

If the element is missing, flag it to the user — the XBL initialization (called by TFA) depends on this for runtime configuration.

## 4. Decide: new file or enhance existing?

Check if `scripts/lldb_pkg/target/<platform>/image/tfa.py` already exists:

- **If it exists and is identical to Maili's:** Enhance documentation only (Section 5A)
- **If it exists but differs:** Analyze the differences; ask the user if they're intentional
- **If it doesn't exist:** Create a new file by copying Maili's template (Section 5B)

## 5A. Documentation-only enhancement (most common case)

If the target platform's `tfa.py` is already functionally correct (byte-for-byte equivalent to Maili's):

1. **Enhance the file header** (lines 1–15):
   - Add `@brief` tag: "TFA (Trusted Firmware-A) image debugging script for <PLATFORM> platform."
   - Add `@author` tag: "Qualcomm Innovation Center, Inc."
   - Expand `@section Description` with full TFA boot sequence summary

2. **Enhance the function docstring** (lines 30–74):
   - Add `@brief` tag with concise summary
   - Add `@details` block with 6-step numbered flow:
     1. Call debugXBL() to initialize XBL and reach sbl1_main_ctl entry point
     2. Load TFA ELF (bl31.elf) with associated debug symbols (.dSYM)
     3. Set module load address to 0 (TFA is loaded at base address)
     4. Configure source path mapping for TFA source code (optional)
     5. Set one-shot hardware breakpoint at bl31_entrypoint
     6. Resume execution and wait for breakpoint to be hit
   - Keep all `@param` tags; clarify that `command`, `exe_ctx`, `result` are unused
   - Add `@return` tag documenting return behavior and exit conditions
   - Add `@exception` tag documenting SystemExit conditions
   - Add `@note` block (5 bullet points):
     - Requires g_dbgContext to be initialized with chipset name and configuration
     - Depends on debugXBL() to establish target connection and reach XBL entry
     - TFA ELF path is resolved via g_dbgContext.openocdConfig.getBinaryPath()
     - Source path mapping is optional; failures are logged but non-fatal
     - The breakpoint at bl31_entrypoint is set as one-shot to avoid re-triggering

3. **Enhance inline comments** (lines 95–137):
   - Clarify each step with descriptive comments
   - Explain why each operation is performed

4. **Verify no logic changes:**
   - All control flow, LLDB API calls, imports, and breakpoint names remain identical to Maili's
   - Only docstrings and comments are modified

## 5B. New file creation (if needed)

If the target platform's `tfa.py` doesn't exist or requires functional changes:

1. **Create a new file from scratch** (do NOT copy from Maili):
   - Use Maili's `tfa.py` as a **structural reference only** (function signatures, flow logic)
   - Do NOT copy Maili's file directly — create a new file with platform-specific imports
   - This ensures each platform has its own clean implementation

2. **Update the import statement** (line 37):
   - **CRITICAL:** Always use platform-specific import path for XBL module
   - Pattern: `from lldb_pkg.target.<PLATFORM>.image.xbl import debugXBL`
   - Example for Mavros: `from lldb_pkg.target.mavros.image.xbl import debugXBL`
   - Each platform has its own `image/xbl.py` module
   - Do NOT reuse another platform's module

3. **Apply documentation enhancements** (Section 5A):
   - File header: Add `@brief`, `@author`, expand `@section Description`
   - Function docstring: Add `@brief`, `@details` (6-step flow), `@param`, `@return`, `@exception`, `@note`
   - Inline comments: Clarify each step

4. **Verify platform-specific adaptations:**
   - Confirm `g_dbgContext.openocdConfig.getBinaryPath('tfa', 'bl31.elf')` resolves correctly
   - Confirm platform's own `xbl.py` module is available and correct
   - Verify no Maili-specific references remain in the code

## 6. LLDB API validation

Verify all LLDB methods used in the script exist in the target LLDB version:

```python
# Methods to validate:
- SBDebugger.HandleCommand()
- SBTarget.AddModule()
- SBTarget.SetModuleLoadAddress()
```

Check against the LLDB Python bindings:
```bash
grep "def (HandleCommand|AddModule|SetModuleLoadAddress)" <lldb-python-path>/lldb/__init__.py
```

All methods should be present with matching names and signatures.

## 7. Verification workflow

Before declaring the port complete:

1. **Syntax check:**
   ```bash
   python -m py_compile scripts/lldb_pkg/target/<platform>/image/tfa.py
   ```

2. **Doxygen compliance:**
   - ✅ File header includes `@file`, `@brief`, `@author`, `@section Description`
   - ✅ Function docstring includes `@brief`, `@details`, `@param`, `@return`, `@exception`, `@note`
   - ✅ Inline comments clarify each step

3. **Logic equivalence:**
   - ✅ Control flow matches Maili's `tfa.py` exactly
   - ✅ LLDB API calls are validated against `lldb/__init__.py`
   - ✅ Breakpoint setup, module loading, and execution logic are identical to Maili

4. **Platform correctness:**
   - ✅ Platform's `chip_config.xml` includes `<sleep_config>` element
   - ✅ Platform's `xbl.py` module is confirmed to be available
   - ✅ No hardcoded platform-specific values remain (except standard bl31_entrypoint)

5. **Import verification:**
   - ✅ All imports are present and match the framework's module structure
   - ✅ `debugXBL` import path is correct (platform-specific)

## 8. Output checklist

Before calling the port done, confirm:

- [ ] File header matches repo convention: SPDX line, `@file`, `@brief`, `@author`, `@section Description`
- [ ] No leftover T32 syntax or comments — all `;` comments are `#`, no `ENTRY`/`GOSUB`/`&var` left
- [ ] Function docstring includes `@brief`, `@details` (6-step flow), `@param`, `@return`, `@exception`, `@note`
- [ ] Inline comments clarify each step and explain why each operation is performed
- [ ] All LLDB API methods are validated against `lldb/__init__.py`
- [ ] Platform's `chip_config.xml` includes `<sleep_config>` element
- [ ] Platform's `xbl.py` module is confirmed to be available
- [ ] No hardcoded platform-specific values (except standard bl31_entrypoint)
- [ ] Syntax check passes: `python -m py_compile <file>`
- [ ] Logic is identical to Maili's proven implementation (no functional changes)
- [ ] Import path is correct: `from lldb_pkg.target.<platform>.image.xbl import debugXBL`

## 9. Common pitfalls

- **Copying scripts from other platforms:** Do NOT copy Maili's tfa.py directly to the new platform. Create a new file from scratch using Maili's structure as a reference only. This ensures clean, platform-specific implementations.
- **Hardcoded ELF path:** Don't hardcode `bl31.elf` path — use `g_dbgContext.openocdConfig.getBinaryPath('tfa', 'bl31.elf')`
- **Wrong XBL import:** **CRITICAL** — Always use the platform-specific module path. Each platform has its own `image/xbl.py`. Do NOT import from another platform's module.
- **Missing `chip_config.xml` elements:** Verify `<sleep_config>` exists before declaring the port complete
- **Incomplete documentation:** Don't skip the `@details` block or `@note` section — these are required for Doxygen compliance
- **Leftover CMM syntax:** Ensure all `;` comments are converted to `#`, no `ENTRY`/`GOSUB`/`&var` remain
- **Maili references in code:** Search for any remaining references to "maili" in the platform-specific file and replace with the actual platform name

## 10. Assumptions and limitations

### Assumptions
- The target platform's `chip_config.xml` includes `<sleep_config>` element
- The target platform has its own `image/xbl.py` module (platform-specific implementation)
- The target platform uses the standard `bl31_entrypoint` entry point symbol
- The target platform's TFA ELF file is named `bl31.elf` (standard across all platforms)
- The target platform's image directory is clean (no scripts copied from other platforms)

### Limitations
- T32-specific constructs (AREA/window management, interactive disassembly) are not ported
- The script depends on XBL initialization via `debugXBL()` — if XBL debug is not available, TFA debug will fail
- Breakpoint symbol names are hardcoded (`bl31_entrypoint`); if a future platform uses a different entry point, the script must be updated

### Critical Implementation Notes
- **Each platform must have its own `tfa.py` file** — do NOT copy from other platforms
- **Each platform must use its own `xbl.py` module** — do NOT import from another platform's module
- **Use Maili's `tfa.py` as a structural reference only** — for understanding the flow and function signatures
- **Create new files from scratch** — this ensures clean, platform-specific implementations without accidental cross-platform dependencies

## 11. Deliverables

Upon completion, provide:

1. **Enhanced/created `tfa.py`** at `scripts/lldb_pkg/target/<platform>/image/tfa.py`
   - Full Doxygen documentation
   - Config-driven for all platform-specific values
   - All LLDB API calls validated
   - Platform-specific XBL import

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

- **Maili reference implementation:** `scripts/lldb_pkg/target/maili/image/tfa.py`
- **Maili XBL module:** `scripts/lldb_pkg/target/maili/image/xbl.py`
- **LLDB API reference:** `lldb/__init__.py` (from VSCode LLDB extension or system LLDB installation)
- **T32 → LLDB mapping:** See `convert-cmm-script` skill for comprehensive mapping table
- **XBL porting skill:** See `port-xbl-cmm-to-python` skill for XBL-specific porting guidance
