#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file hyp.py
#
# @brief Mavros Hypervisor image loading support.
#
# This module provides LLDB-based debugging support for the Mavros Hypervisor
# (Gunyah) image loading and initialization. It orchestrates the complete
# Hypervisor boot sequence including:
#
# - Entrypoint validation and verification
# - Symbol loading at physical addresses (pre-MMU)
# - MMU enablement and KASLR offset calculation
# - Symbol reloading at virtual addresses (post-MMU)
# - PIL (Peripheral Image Loader) module loading
# - Breakpoint setup for debugging at key execution points
#
# The implementation follows the Mavros boot architecture where the Hypervisor
# is loaded at physical address 0x80000000 and transitions to virtual addressing
# after MMU enablement. KASLR (Kernel Address Space Layout Randomization) is
# supported through dynamic symbol reloading with calculated slide offsets.
#
# @section Execution Flow
#
# 1. **TZ Debugging:** Invoke TZ debugger to reach HYP entrypoint
# 2. **Breakpoint Setup:** Set hardware breakpoints at GUNYAH and QHEE entry vectors
# 3. **Entrypoint Detection:** Continue execution and detect which entry vector was hit
# 4. **Symbol Loading (Phase 1):** Load HYP symbols at physical address 0x80000000
# 5. **MMU Transition:** Enable MMU and wait for execution to stabilize
# 6. **KASLR Calculation:** Read TTBR0_EL2 and calculate KASLR offset
# 7. **Symbol Loading (Phase 2):** Reload symbols with KASLR slide offset
# 8. **PIL Loading:** Load PIL modules (resource-manager and runtime)
# 9. **Boot Cold Init:** Set breakpoint at boot_cold_init and continue
# 10. **PIL Debugging:** Invoke PIL debugger if subsystem debugging is enabled
#
# @section Configuration
#
# Entry vector addresses are loaded from chip_config.xml:
# - GUNY_ENTRY_VECTOR_ADDR: Gunyah primary entry point (typically 0x80000000)
# - QHEE_ENTRY_VECTOR_ADDR: QHEE alternative entry point (typically 0x80080000)
#
# @section LLDB Integration
#
# This module uses the LLDB Python API for:
# - Command execution via debugger.HandleCommand()
# - Module loading via target.AddModule() and SetModuleLoadAddress()
# - Register reading for KASLR offset calculation
# - Breakpoint management
#
# @section Dependencies
#
# - lldb: LLDB Python bindings
# - utils.debug_context: Global debug context and logging
# - utils.source_map: Source path mapping utilities
# - lldb_pkg.target.mavros.image.tz: Mavros TZ debugger
# - lldb_pkg.target.mavros.image.debugwithpil: PIL debugging support
# - lldb_pkg.arch.arm.coprocessor_reg: ARM coprocessor register utilities

import os
import re
import sys
import time
import lldb
from typing import Optional, Tuple, Any
from utils.debug_context import logger
from utils.source_map import setSourceMap
from utils.dap_access import dapAccessGDB
from utils.debug_context import g_dbgContext
from lldb_pkg.target.mavros.image.tz import debugTZ
from lldb_pkg.arch.arm.coprocessor_reg import registerRead
from lldb_pkg.target.mavros.image.debugwithpil import pil_debugger


# KASLR offset calculation constants
KASLR_MASK: int = 0xFFFFFFFFFFE00000  # Mask for lower 21 bits (0x1fffff)
KASLR_SHIFT: int = 21  # Number of bits to shift for KASLR alignment
SPR_TTBR0_EL2: int = 0x34c00  # System register for TTBR0_EL2

# HYP image constants
HYP_ELF_FILENAME: str = 'hypvm.elf'
HYP_BINARY_TYPE: str = 'hyp'
HYP_ENTRYPOINT_ADDR: int = 0x80000000
HYP_STRIP_PART: str = 'hypervisor_images'

# PIL module addresses (from Mavros go_hyp10.cmm)
PIL_RESOURCE_MANAGER_ADDR: int = 0x8080d000
PIL_RUNTIME_ADDR: int = 0x80800000

# PIL module paths (relative to HYP_BUILDROOT)
PIL_RESOURCE_MANAGER_PATH: str = 'hypervisor_images/out/MAVROSLA/core/bsp/gunyah-hypervisor/build/MAVROSLA/resource-manager_debug'
PIL_RUNTIME_PATH: str = 'hypervisor_images/out/MAVROSLA/core/bsp/gunyah-hypervisor/build/MAVROSLA/runtime'


def get_pc_address_type(
    debugger: lldb.SBDebugger,
    command: str,
    exe_ctx: lldb.SBExecutionContext,
    result: lldb.SBCommandReturnObject,
    commandInterpreter: lldb.SBCommandInterpreter,
    returnObj: lldb.SBCommandReturnObject
) -> bool:
    """
    @brief Determine if the current PC is in physical or virtual address space.

    This function reads the PC register and SCTLR_EL1 to determine the current
    addressing mode. If the MMU is disabled, it continues execution and retries.
    If the MMU is enabled, it classifies the PC as either virtual (kernel space)
    or physical (lower address space).

    @param debugger         LLDB debugger instance
    @param command          Command string (unused)
    @param exe_ctx          Execution context
    @param result           Command result object (unused)
    @param commandInterpreter LLDB command interpreter
    @param returnObj        Return object for command output

    @return True if PC is in kernel virtual address space (>= 0xFFFF000000000000),
            False if PC is in physical address space (<= 0x0000FFFFFFFFFFFF)

    @exception SystemExit   If PC cannot be read or SCTLR_EL1 cannot be accessed
    """

    # Read PC register
    cmd: str = "register read pc"
    commandInterpreter.HandleCommand(cmd, returnObj)
    if not returnObj.Succeeded():
        return False
    pc_output: str = str(returnObj.GetOutput())
    pc_match: Optional[Any] = re.search(r'0x[0-9a-fA-F]+', pc_output)
    if not pc_match:
        return False

    pc: str = pc_match.group(0)
    pc_int: int = int(pc, 16)

    # Read SCTLR_EL1 to check if MMU is enabled
    sctlr_output: str = registerRead(debugger, command, exe_ctx, result, "sctlr_el1", "yes")
    sctlr_match: Optional[Any] = re.search(r'0x[0-9a-fA-F]+', sctlr_output)
    if not sctlr_match:
        return False

    sctlr_val: int = int(sctlr_match.group(0), 16)
    mmu_enabled: bool = (sctlr_val & 0x1) != 0

    # Determine address type
    if not mmu_enabled:
        # MMU not enabled yet, continue execution and retry
        debugger.HandleCommand("c")
        time.sleep(0.1)
        return get_pc_address_type(debugger, command, exe_ctx, result, commandInterpreter, returnObj)
    elif pc_int >= 0xFFFF000000000000:
        # Kernel virtual address space
        return True
    elif pc_int <= 0x0000FFFFFFFFFFFF:
        # Physical address space
        return False

    return False


def debugHYP(
    debugger: lldb.SBDebugger,
    command: str,
    exe_ctx: lldb.SBExecutionContext,
    result: lldb.SBCommandReturnObject,
    inputarg: Optional[str] = None,
    subsys: Optional[str] = None
) -> Optional[bool]:
    """
    @brief Debug the Mavros Hypervisor image loading and initialization.

    This function orchestrates the complete Hypervisor debugging workflow:

    1. Invokes TZ debugger to reach HYP entrypoint
    2. Sets hardware breakpoints at GUNYAH and QHEE entry vectors
    3. Detects which entry vector was hit
    4. Loads HYP symbols at physical address (pre-MMU)
    5. Enables MMU and calculates KASLR offset
    6. Reloads symbols with KASLR slide offset (post-MMU)
    7. Loads PIL modules (resource-manager and runtime)
    8. Sets breakpoint at boot_cold_init
    9. Optionally invokes PIL debugger for subsystem debugging

    The function supports two entry vectors:
    - GUNYAH (primary): Standard Hypervisor entry at 0x80000000
    - QHEE (alternative): QHEE-specific entry at 0x80080000

    @param debugger         LLDB debugger instance providing access to targets
    @param command          Command string containing input arguments
    @param exe_ctx          Execution context with process information
    @param result           Return object for command output
    @param inputarg         Optional flag indicating internal vs. user invocation
    @param subsys           Optional subsystem name for PIL debugging

    @return None if called from user context or inputarg is set
            True if PIL debugging is enabled and PIL modules are loaded
            False if PIL debugging is disabled
            None if QHEE entry vector is hit (not yet implemented)

    @exception SystemExit   If HYP ELF file cannot be found or entry point is invalid
    """

    if inputarg is None:
        logger.info(" --- Preparing for Debug of HYP Image ---")

    chipset_name: str = g_dbgContext.chipsetName.lower()
    returnObj: lldb.SBCommandReturnObject = lldb.SBCommandReturnObject()
    commandInterpreter: lldb.SBCommandInterpreter = debugger.GetCommandInterpreter()

    # Load HYP configuration from chip_config.xml
    hyp_guny_entry_vector_addr: Optional[str] = None
    hyp_qhee_entry_vector_addr: Optional[str] = None

    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname: Optional[str] = chip.get("name")
        if chipname and chipname.upper() == chipset_name.upper():
            hyp_config = chip.find("hyp_config")
            if hyp_config is not None:
                guny_elem = hyp_config.find('GUNY_ENTRY_VECTOR_ADDR')
                qhee_elem = hyp_config.find('QHEE_ENTRY_VECTOR_ADDR')
                if guny_elem is not None:
                    hyp_guny_entry_vector_addr = guny_elem.attrib.get('address')
                if qhee_elem is not None:
                    hyp_qhee_entry_vector_addr = qhee_elem.attrib.get('address')
            break

    # Invoke TZ debugger to reach HYP entrypoint
    wait_pil_tz: Optional[bool] = None
    if subsys:
        wait_pil_tz = debugTZ(debugger, command, exe_ctx, result, subsys=subsys)
    else:
        debugTZ(debugger, command, exe_ctx, result, "run")

    # Set hardware breakpoints at entry vectors
    if hyp_guny_entry_vector_addr is not None:
        cmd: str = "br s -a {} --hardware".format(hyp_guny_entry_vector_addr)
        debugger.HandleCommand(cmd)

    if hyp_qhee_entry_vector_addr is not None:
        cmd = "br s -a {} --hardware".format(hyp_qhee_entry_vector_addr)
        debugger.HandleCommand(cmd)

    # Continue execution and detect which entry vector was hit
    debugger.HandleCommand("c")
    cmd = "register read pc"
    commandInterpreter.HandleCommand(cmd, returnObj)
    pc: str = "0x0"
    if returnObj.Succeeded():
        res: str = str(returnObj.GetOutput())
        match: Optional[Any] = re.search(r'0x[0-9a-fA-F]+', res)
        if match:
            pc = match.group(0)

    # Handle GUNYAH entry vector
    if hyp_guny_entry_vector_addr and int(pc, 16) == int(hyp_guny_entry_vector_addr, 16):
        # Load HYP ELF file
        hyp_elf_path: str = g_dbgContext.openocdConfig.getBinaryPath(HYP_BINARY_TYPE, HYP_ELF_FILENAME)
        if not os.path.exists(hyp_elf_path):
            logger.error("Unable to find HYP ELF file: {}".format(hyp_elf_path))
            sys.exit(1)

        # Add module to target
        hyp_dsym_cmd: str = "{}.dSYM".format(hyp_elf_path)
        g_dbgContext.tgt.AddModule(hyp_elf_path, None, None, hyp_dsym_cmd)

        time.sleep(4)

        # Set module load address at physical entrypoint
        selTarget: lldb.SBTarget = debugger.GetSelectedTarget()
        if len(selTarget.modules) < 4:
            logger.error("Insufficient modules loaded. Expected at least 4 modules.")
            sys.exit(1)

        module4: lldb.SBModule = selTarget.modules[3]
        g_dbgContext.tgt.SetModuleLoadAddress(module4, int(hyp_guny_entry_vector_addr, 16))

        time.sleep(4)

        # Delete all breakpoints
        debugger.HandleCommand("br del")

        # Enable exception catching to get ASLR base
        catch_exc_on_cmd: str = "{} {}.apss.core0 catch_exc nsec_el2".format(
            g_dbgContext.monitorPrefix,
            g_dbgContext.chipsetName.upper()
        )
        debugger.HandleCommand(catch_exc_on_cmd)
        debugger.HandleCommand("c")

        # Determine if PC is in virtual address space
        res_addr_type: bool = get_pc_address_type(debugger, command, exe_ctx, result, commandInterpreter, returnObj)
        if res_addr_type:
            # Read PC in virtual address space
            cmd = "register read pc"
            commandInterpreter.HandleCommand(cmd, returnObj)
            if returnObj.Succeeded():
                res = str(returnObj.GetOutput())
                match = re.search(r'0x[0-9a-fA-F]+', res)
                if match:
                    pc = match.group(0)

        # Calculate KASLR offset from PC
        pc_int = int(pc, 16)
        kaslr_offset: int = pc_int & KASLR_MASK
        kaslr_hex: str = hex(kaslr_offset)

        # Reload symbols with KASLR slide
        cmd = "target modules load --file {} --slide {}".format(hyp_elf_path, kaslr_hex)
        debugger.HandleCommand(cmd)

    # Handle QHEE entry vector (not yet implemented)
    elif hyp_qhee_entry_vector_addr and int(pc, 16) == int(hyp_qhee_entry_vector_addr, 16):
        logger.info("QHEE Hypervisor entry detected (not yet implemented)")

    else:
        logger.error("Unable to reach HYP entry point. Quitting...")
        sys.exit(1)

    # Delete all breakpoints
    debugger.HandleCommand("br del")

    # Set source path mapping if available
    try:
        sourcePathSet: Optional[Any] = g_dbgContext.sourcePath.get("hyp")
        if sourcePathSet:
            setSourceMap(debugger, "hyp", sourcePathSet)
    except Exception as e:
        logger.debug("Source path mapping not available: {}".format(str(e)))

    # Set breakpoint at boot_cold_init
    debugger.HandleCommand("br s -n boot_cold_init --hardware")
    debugger.HandleCommand("c")

    # Disable exception catching
    catch_exc_off_cmd: str = catch_exc_on_cmd.replace("nsec_el2", "off")
    debugger.HandleCommand(catch_exc_off_cmd)

    # Handle return based on invocation context
    if inputarg:
        return None

    if subsys:
        # Set breakpoint at init_qheebsp for PIL debugging
        debugger.HandleCommand("br s -n init_qheebsp")
        debugger.HandleCommand("c")

        # Invoke PIL debugger
        wait_pil_hyp: Optional[bool] = pil_debugger(debugger, command, result, subsystem=subsys)

        # Return PIL status
        if (wait_pil_tz is True) or (wait_pil_hyp is True):
            return True
        else:
            return False
    else:
        logger.info(" --- FBC: HYP entrypoint reached in debug mode")
        return None
