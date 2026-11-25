#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file tfa.py
#
# @brief TFA (Trusted Firmware-A) image debugging script for Mavros platform.
#
# @author Qualcomm Innovation Center, Inc.
#
# @section Description
# This file contains a script for TFA image debugging on the Mavros platform.
# It orchestrates the complete TFA boot sequence: XBL initialization, TFA ELF
# symbol loading, source path mapping, and execution to the TFA entry point
# (bl31_entrypoint).
#
# The debug flow:
# 1. Initialize XBL (eXtensible Boot Loader) via debugXBL() to reach sbl1_main_ctl
# 2. Load TFA ELF symbols (bl31.elf) with debug symbols (.dSYM)
# 3. Configure source path mapping for TFA source code
# 4. Set one-shot hardware breakpoint at bl31_entrypoint
# 5. Resume execution and wait for breakpoint hit
#
# @note
# - Requires g_dbgContext to be initialized with chipset name and configuration
# - Depends on XBL debug flow to establish target connection and reach XBL entry
# - TFA ELF path is resolved via g_dbgContext.openocdConfig.getBinaryPath()
# - Source path mapping is optional; failures are logged but non-fatal

import os
import sys
import time
import lldb
from utils.debug_context import logger
from utils.source_map import setSourceMap
from utils.debug_context import g_dbgContext
from lldb_pkg.target.mavros.image.xbl import debugXBL


def debugTFA(
    debugger: lldb.SBDebugger,
    command: str,
    exe_ctx: lldb.SBExecutionContext,
    result: lldb.SBCommandReturnObject,
    inputarg: str = None,
    subsys: str = None
) -> None:
    """!
    @brief Debug TFA (Trusted Firmware-A) image on Mavros platform.

    @details
    This function implements the complete TFA debug flow for Mavros:
    1. Call debugXBL() to initialize XBL and reach sbl1_main_ctl entry point
    2. Load TFA ELF (bl31.elf) with associated debug symbols (.dSYM)
    3. Set module load address to 0 (TFA is loaded at base address)
    4. Configure source path mapping for TFA source code (optional)
    5. Set one-shot hardware breakpoint at bl31_entrypoint
    6. Resume execution and wait for breakpoint to be hit

    The function is designed to be called either directly from LLDB (user-facing)
    or from within another script (silent mode). When called from a script,
    inputarg is set to a non-None value to suppress entry logging.

    Usage: debug -d tfa

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

    @exception SystemExit   Raised if TFA ELF file is not found or if XBL debug
                            initialization fails.

    @note
    - Requires g_dbgContext to be initialized with chipset name and configuration
    - Depends on debugXBL() to establish target connection and reach XBL entry
    - TFA ELF path is resolved via g_dbgContext.openocdConfig.getBinaryPath()
    - Source path mapping is optional; failures are logged but non-fatal
    - The breakpoint at bl31_entrypoint is set as one-shot to avoid re-triggering

    """

    # Log entry message only if called directly from LLDB (not from another script)
    if inputarg is None:
        logger.info(" --- Preparing for Debug of TFA Image ---")

    # Step 1: Initialize XBL and reach sbl1_main_ctl entry point
    # Pass "run" as inputarg to suppress XBL's entry logging
    debugXBL(debugger, command, exe_ctx, result, "run")

    # Step 2: Load TFA ELF symbols
    # TFA on Mavros uses bl31.elf (ARM Trusted Firmware-A BL31 stage)
    tfa_elf_filename = 'bl31.elf'
    tfa_elf_path = g_dbgContext.openocdConfig.getBinaryPath('tfa', tfa_elf_filename)

    if os.path.exists(tfa_elf_path):
        # Load ELF with associated debug symbols (.dSYM)
        tfa_dsym_path = "{}.dSYM".format(tfa_elf_path)
        module = g_dbgContext.tgt.AddModule(tfa_elf_path, None, None, tfa_dsym_path)
        # Set module load address to 0 (TFA is loaded at base address)
        g_dbgContext.tgt.SetModuleLoadAddress(module, 0)
    else:
        logger.error(f"Unable to find {tfa_elf_filename} at {tfa_elf_path}")
        sys.exit(1)

    # Step 3: Configure source path mapping for TFA source code
    # This is optional; if not configured, LLDB uses default behavior
    try:
        sourcePathSet = g_dbgContext.sourcePath["tfa"]
        setSourceMap(debugger, "tfa", sourcePathSet)
    except Exception as e:
        logger.warning(f"Unable to set source code map for TFA; using default behavior. "
                      f"Error: {type(e).__name__}: {e}")

    # Step 4: Set one-shot hardware breakpoint at TFA entry point
    # bl31_entrypoint is the ARM Trusted Firmware-A BL31 entry point
    cmd = "br s -n bl31_entrypoint --hardware -o true"
    debugger.HandleCommand(cmd)

    # Step 5: Resume execution and wait for breakpoint hit
    debugger.HandleCommand("c")

    # Step 6: Log completion if called directly from LLDB
    if inputarg is None:
        logger.info(" --- TFA entrypoint reached in debug mode")
