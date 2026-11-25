#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file tz.py
#
# @brief Mavros Trusted Zone image loading implementation.
#
# @author Qualcomm Innovation Center, Inc.
#
# @section Description
# This file contains a script for TZ (Trusted Zone) image debugging on the Mavros
# platform. It orchestrates the complete TZ boot sequence: TFA initialization,
# QSEE ELF symbol loading, ASLR relocation, source path mapping, and execution
# to the TZ entry point (tzbsp_boot).
#
# The debug flow:
# 1. Initialize TFA (Trusted Firmware-A) via debugTFA() to reach bl31_entrypoint
# 2. Load QSEE ELF symbols (qsee.elf) with debug symbols (.dSYM)
# 3. Set breakpoint at tzbsp_mmu_enable to halt before MMU initialization
# 4. Resume and halt at tzbsp_cold_and_warm_boot_cmn
# 5. Read ASLR relocation offset from memory
# 6. Relocate QSEE symbols using the ASLR offset
# 7. Configure source path mapping for TZ source code
# 8. Set one-shot hardware breakpoint at tzbsp_boot (TZ entry point)
# 9. Resume execution and wait for breakpoint hit
#
# @note
# - Requires g_dbgContext to be initialized with chipset name and configuration
# - Depends on TFA debug flow to establish target connection and reach TFA entry
# - QSEE ELF path is resolved via g_dbgContext.openocdConfig.getBinaryPath()
# - ASLR relocation is required for proper symbol resolution in TZ
# - Source path mapping is optional; failures are logged but non-fatal

import os
import sys
import time
import lldb
from utils.debug_context import logger
from utils.source_map import setSourceMap
from utils.debug_context import g_dbgContext
from lldb_pkg.target.mavros.image.tfa import debugTFA


def debugTZ(
    debugger: lldb.SBDebugger,
    command: str,
    exe_ctx: lldb.SBExecutionContext,
    result: lldb.SBCommandReturnObject,
    inputarg: str = None,
    subsys: str = None
) -> None:
    """!
    @brief Debug TZ (Trusted Zone) image on Mavros platform.

    @details
    This function implements the complete TZ debug flow for Mavros:
    1. Call debugTFA() to initialize TFA and reach bl31_entrypoint
    2. Load QSEE ELF (qsee.elf) with associated debug symbols (.dSYM)
    3. Set hardware breakpoint at tzbsp_mmu_enable to halt before MMU setup
    4. Resume execution and halt at tzbsp_cold_and_warm_boot_cmn
    5. Read ASLR relocation offset from memory at the symbol address
    6. Relocate QSEE module using the ASLR offset for proper symbol resolution
    7. Configure source path mapping for TZ source code (optional)
    8. Set one-shot hardware breakpoint at tzbsp_boot (TZ entry point)
    9. Resume execution and wait for breakpoint to be hit

    The function is designed to be called either directly from LLDB (user-facing)
    or from within another script (silent mode). When called from a script,
    inputarg is set to a non-None value to suppress entry logging.

    Usage: debug -d tz

    @param debugger         An instance of the SBDebugger object providing access
                            to SBTargets used for debugging.
    @param command          String containing all input arguments for the command
                            (unused in this implementation).
    @param exe_ctx          An execution context object carrying information about
                            the process context (unused in this implementation).
    @param result           A return object encapsulating success/failure information
                            and output text (unused in this implementation).
    @param inputarg         Optional flag: if None, the command is invoked directly
                            from LLDB (user-facing); if set, the command is called
                            from within another script (silent mode, no entry message).
    @param subsys           Optional subsystem parameter for future extensibility
                            (currently unused).

    @return None. On success, the function returns (or logs completion). On failure,
            calls sys.exit(1) with an error message.

    @exception SystemExit   Raised if QSEE ELF file is not found or if TFA debug
                            initialization fails.

    @note
    - Requires g_dbgContext to be initialized with chipset name and configuration
    - Depends on debugTFA() to establish target connection and reach TFA entry
    - QSEE ELF path is resolved via g_dbgContext.openocdConfig.getBinaryPath()
    - ASLR relocation is required for proper symbol resolution in TZ
    - Source path mapping is optional; failures are logged but non-fatal
    - The breakpoint at tzbsp_boot is set as one-shot to avoid re-triggering

    """

    # Log entry message only if called directly from LLDB (not from another script)
    if inputarg is None:
        logger.info(" --- Preparing for Debug of TZ Image ---")

    # Step 1: Initialize TFA and reach bl31_entrypoint
    # Pass "run" as inputarg to suppress TFA's entry logging
    debugTFA(debugger, command, exe_ctx, result, "run")

    # Step 2: Load QSEE ELF symbols
    # QSEE (Qualcomm Secure Execution Environment) is the TZ image on Mavros
    qsee_elf_filename = 'qsee.elf'
    qsee_elf_path = g_dbgContext.openocdConfig.getBinaryPath('tz', qsee_elf_filename)

    if not os.path.exists(qsee_elf_path):
        logger.error(f"Unable to find {qsee_elf_filename} at {qsee_elf_path}")
        sys.exit(1)

    # Load ELF with associated debug symbols (.dSYM)
    qsee_dsym_path = "{}.dSYM".format(qsee_elf_path)
    module = g_dbgContext.tgt.AddModule(qsee_elf_path, None, None, qsee_dsym_path)
    logger.info(f"Loaded QSEE module: {module}")

    # Step 3: Find key symbols for ASLR relocation
    # These symbols are used to determine the ASLR offset and set breakpoints
    try:
        sym_mmu_enable = module.FindSymbols("tzbsp_mmu_enable")
        sym_cold_boot = module.FindSymbols("tzbsp_cold_and_warm_boot_cmn")
        sym_aslr_offset = module.FindSymbols("aslr_reloc_offset")

        if not sym_mmu_enable or not sym_cold_boot or not sym_aslr_offset:
            logger.error("Unable to find required TZ symbols")
            sys.exit(1)

        tzbsp_mmu_enable_addr = sym_mmu_enable[0].symbol.GetValue()
        tzbsp_cold_and_warm_boot_cmn_addr = sym_cold_boot[0].symbol.GetValue()
        aslr_reloc_offset_addr = sym_aslr_offset[0].symbol.GetValue()

    except Exception as e:
        logger.error(f"Error resolving TZ symbols: {type(e).__name__}: {e}")
        sys.exit(1)

    # Step 4: Set breakpoint at tzbsp_mmu_enable
    # This halts execution before MMU initialization, allowing us to read ASLR offset
    cmd = "br s -a {} --hardware".format(hex(tzbsp_mmu_enable_addr))
    debugger.HandleCommand(cmd)
    debugger.HandleCommand('c')
    debugger.HandleCommand('up')
    debugger.HandleCommand('br del')

    # Step 5: Set breakpoint at tzbsp_cold_and_warm_boot_cmn
    # This is where we read the ASLR relocation offset
    cmd = "br s -a {} --hardware".format(hex(tzbsp_cold_and_warm_boot_cmn_addr))
    debugger.HandleCommand(cmd)
    debugger.HandleCommand('c')
    debugger.HandleCommand('br del')

    # Step 6: Read ASLR relocation offset from memory
    # The ASLR offset is stored at aslr_reloc_offset_addr and is 8 bytes (64-bit)
    process = debugger.GetSelectedTarget().GetProcess()
    error_ref = lldb.SBError()

    try:
        aslr_reloc_offset_bytes = process.ReadMemory(aslr_reloc_offset_addr, 8, error_ref)
        if error_ref.Fail():
            logger.error(f"Failed to read ASLR offset from memory: {error_ref.GetCString()}")
            sys.exit(1)

        # Convert bytes to integer (little-endian) and then to hex string
        aslr_reloc_offset = hex(int.from_bytes(aslr_reloc_offset_bytes, byteorder='little'))
        logger.info(f"ASLR relocation offset: {aslr_reloc_offset}")

    except Exception as e:
        logger.error(f"Error reading ASLR offset: {type(e).__name__}: {e}")
        sys.exit(1)

    # Step 7: Relocate QSEE module using ASLR offset
    # This adjusts all symbol addresses to account for runtime relocation
    try:
        g_dbgContext.tgt.SetModuleLoadAddress(module, int(aslr_reloc_offset, 16))
        logger.info(f"Relocated QSEE module to address: {aslr_reloc_offset}")
    except Exception as e:
        logger.error(f"Error relocating QSEE module: {type(e).__name__}: {e}")
        sys.exit(1)

    # Step 8: Configure source path mapping for TZ source code
    # This is optional; if not configured, LLDB uses default behavior
    try:
        sourcePathSet = g_dbgContext.sourcePath["tz"]
        setSourceMap(debugger, "tz", sourcePathSet)
    except Exception as e:
        logger.warning(f"Unable to set source code map for TZ; using default behavior. "
                      f"Error: {type(e).__name__}: {e}")

    # Step 9: Set one-shot hardware breakpoint at TZ entry point
    # tzbsp_boot is the TZ entry point where we want to stop for debugging
    cmd = 'br s -n tzbsp_boot --hardware -o true'
    debugger.HandleCommand(cmd)

    # Step 10: Resume execution and wait for breakpoint hit
    debugger.HandleCommand("c")

    # Step 11: Log completion if called directly from LLDB
    if inputarg is None:
        logger.info(" --- FBC: TZ entrypoint reached in debug mode")
