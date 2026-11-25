#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file aop.py
#
# @section Description
# This file contains a script for AOP image debugging.

import os
import time
import lldb
import traceback
from utils.source_map import setSourceMap
from lldb_pkg.target.mavros.image.xbl import debugXBL
from utils.debug_context import g_dbgContext, logger
from utils.stop_wait import wait_for_breakpoint_hit
# Constants
DEBUGGER_TIMEOUT = 10000000  # Timeout for GDB remote connection in milliseconds
CONNECTION_DELAY = 0.2  # Delay in seconds between connection operations

def get_aop_elf_name(chip_name: str):
    """!
    @brief Look up the AOP ELF filename for the active chipset from
    chip_config.xml.

    @param chip_name  Chip name to match against chip_config.xml's
                       <chipset name="..."> attribute (case-insensitive).

    @return The ELF filename as a string, or None if the active chipset has
            no such configuration.
    """
    for chip in g_dbgContext.chipInfo.findall("chipset"):
        if chip.get("name", "").lower() != chip_name.lower():
            continue

        riscv_config = chip.find("riscv_config")
        if riscv_config is None:
            return None

        aoss_config = riscv_config.find("aoss_config")
        if aoss_config is None:
            return None

        aop_elf_elem = aoss_config.find("aop_elf")
        if aop_elf_elem is None:
            return None

        return aop_elf_elem.attrib.get("name")

    return None

def debugAOSS(debugger: lldb.SBDebugger, command, exe_ctx, result, inputarg=None, step_arg=None):
    """!
    This function is used to define a command to debug AOP over two steps.

    In STEP 1, we do hard-reset and then do "pmic reset halt" to stop at
    PBL entry, arm the AOSS RISC-V debug module, set breakpoint at
    qsee_jump_func and say continue. AOP comes out of reset in between the
    device boot from PBL entry to the qsee_jump_func, and the debug module
    is examined once AOSS is reachable.
    In STEP 2, load the AOP ELF (name read from chip_config.xml's
    <aoss_config><aop_elf name="..."/>) and connect to AOP which got up
    in STEP 1 (came out of reset).

    Usage:
        Step 1: debug -d aoss -s 1
        Step 2: debug -d aoss -s 2

    @param debugger         -   An instance of the SBDebugger Object that
                                provides access to SBTargets used for
                                debugging.
    @param command          -   String containing all the input arguments
                                for the used command.
    @param exe_ctx          -   An execution context object carrying
                                information of the process
                                context on which the command is used.
    @param result           -   A return object which encapsulates
                                success/failure information for the command
                                and output text that needs to be printed
                                as a result of the command.
    @param inputarg         -   A variable used to denote whether the command
                                is being used from inside the script or being
                                used from lldb (by the user).
    @param step_arg         -   Step number to debug: 1 or 2.

    @return                 -   None. Results are communicated through the result parameter
                                and through logger messages.
    """
    # Parse command arguments: "-d aoss -s 1"
    try:
        args = command.split()
        if "-s" not in args:
            logger.error("Missing '-s' flag for step number")
            logger.error("Usage: -d aoss -s 1  OR  -d aoss -s 2")
            return

        step_index = args.index("-s")
        if step_index + 1 >= len(args):
            logger.error("Missing step number after '-s' flag")
            return

        step = int(args[step_index + 1])
    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse command arguments: {e}")
        return

    subsys: str = "aoss"
    apss_connect_cmd = "connect_remote -s apss0"
    aoss_gdb_remote_connection = f"connect_remote -s {subsys}"
    chip_name = g_dbgContext.chipsetName.lower()

    if step == 1:
        chip = None

        # Configure debugger settings
        debugger.HandleCommand(f"settings set plugin.process.gdb-remote.packet-timeout {DEBUGGER_TIMEOUT}")
        debugger.HandleCommand("platform select remote-linux")
        debugger.HandleCommand("settings set auto-confirm on")

        # Find chip configuration
        for chip_elem in g_dbgContext.chipInfo.findall('chipset'):
            if chip_elem.get('name').lower() == chip_name.lower():
                chip = chip_elem
                break

        if chip is None:
            logger.error(f"Could not find chip configuration for {chip_name}")
            return False

        # Run XBL debugging
        debugXBL(debugger, command, exe_ctx, result, "run")

        # Helper function to validate and get configuration element
        def get_config_element(parent, element_name):
            element = parent.find(element_name)
            if element is None:
                logger.error(f"RISC-V: Missing {element_name} configuration")
                return None
            return element

        # Helper function to validate and get attribute
        def get_attribute(element, attr_name):
            if element.attrib is None or attr_name not in element.attrib:
                logger.error(f"RISC-V: Missing {attr_name} attribute")
                return None
            return element.attrib.get(attr_name)

        # Get RISC-V configuration
        riscv_config = get_config_element(chip, "riscv_config")
        if riscv_config is None:
            return False

        # Get AOSS configuration
        aoss_config = get_config_element(riscv_config, "aoss_config")
        if aoss_config is None:
            return False

        # Get AOSS debug base configuration
        aoss_debug_base = get_config_element(aoss_config, "aoss_debug_base")
        if aoss_debug_base is None:
            return False

        # Get AOSS base address
        aoss_base_addr = get_attribute(aoss_debug_base, "addr")
        if aoss_base_addr is None:
            return False

        try:
            # Get dmcontrol configuration
            dmcontrol_elem = get_config_element(aoss_config, "dmcontrol")
            if dmcontrol_elem is None:
                return False

            # Get dmcontrol attributes
            dmcontrol_offset_addr = get_attribute(dmcontrol_elem, "offset_addr")
            dmcontrol_data_1_str = get_attribute(dmcontrol_elem, "dmcontrol_data_1")
            dmcontrol_data_2_str = get_attribute(dmcontrol_elem, "dmcontrol_data_2")

            if not all([dmcontrol_offset_addr, dmcontrol_data_1_str, dmcontrol_data_2_str]):
                return False

            dmcontrol_data_1 = int(dmcontrol_data_1_str, 16)
            dmcontrol_data_2 = int(dmcontrol_data_2_str, 16)

            dmcontrol_reg = int(aoss_base_addr, 16) + int(dmcontrol_offset_addr, 16)

            # Write to dmcontrol register
            cmd1 = f"{g_dbgContext.monitorPrefix} DAP.cxapb_{subsys} mww \
                {hex(dmcontrol_reg)} {hex(dmcontrol_data_1)}"
            debugger.HandleCommand(cmd1)
            time.sleep(CONNECTION_DELAY)
            cmd2 = f"{g_dbgContext.monitorPrefix} DAP.cxapb_{subsys} mww \
                {hex(dmcontrol_reg)} {hex(dmcontrol_data_2)}"
            debugger.HandleCommand(cmd2)

        except (KeyError, ValueError) as e:
            logger.error(f"Failed to parse AOSS config: {e}")
            logger.error(traceback.format_exc())
            return False

        # Connect to APSS and set breakpoint at qsee_jump_func
        try:
            debugger.HandleCommand(apss_connect_cmd)

            apss_target: lldb.SBTarget = debugger.GetSelectedTarget()
            if not apss_target.IsValid():
                logger.error("Failed to get APSS target")
                return False

            apss_process: lldb.SBProcess = apss_target.GetProcess()
            if not apss_process.IsValid():
                logger.error("Failed to get APSS process")
                return False

            brk_pnt: lldb.SBBreakpoint = apss_target.BreakpointCreateByName("qsee_jump_func")
            if not brk_pnt.IsValid():
                logger.error("Failed to create breakpoint at qsee_jump_func")
                return False
            brk_pnt.SetIsHardware(True)
            brk_pnt.SetOneShot(True)

            debugger.SetAsync(True)
            apss_process.Continue()
            debugger.SetAsync(False)

            if not wait_for_breakpoint_hit(apss_process):
                logger.error("Timeout waiting for qsee_jump_func")
                return False

            # Examine AOSS
            debugger.HandleCommand(f"{g_dbgContext.monitorPrefix} \
                {chip_name.upper()}.{subsys} arp_examine")
            time.sleep(CONNECTION_DELAY)

        except Exception as e:
            logger.error(f"Failed during APSS connection and breakpoint setup: {str(e)}")
            logger.error(traceback.format_exc())
            return False

        logger.info("---- AOSS examined; ready for STEP 2 ----")

    elif step == 2:
        # Get AOP ELF name from chip_config.xml and load it
        aop_elf_name = get_aop_elf_name(chip_name)
        if not aop_elf_name:
            logger.error("Missing aop_elf configuration for AOSS in chip_config.xml")
            return False

        try:
            aop_elf_path = g_dbgContext.openocdConfig.getBinaryPath('aop', aop_elf_name)
            if not aop_elf_path:
                logger.error("Failed to find AOP binary path")
                return False
        except Exception as e:
            logger.error(f"Failed to find binary path: {str(e)}")
            return False

        # Create target from AOP ELF file
        if not os.path.exists(aop_elf_path):
            logger.error(f"{aop_elf_path} - File not found.")
            return False

        try:
            target: lldb.SBTarget = debugger.CreateTarget(aop_elf_path)
            if not target.IsValid():
                logger.error(f"Failed to create target for ELF: {aop_elf_path}")
                return False
            logger.info(f"Successfully loaded ELF: {aop_elf_path}")
        except Exception as e:
            logger.error(f"Failed to create target for ELF: {aop_elf_path}")
            logger.error(f"Error type: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            return False

        # Set Source code path
        try:
            source_path = g_dbgContext.sourcePath.get("aop")
            if source_path and os.path.exists(source_path):
                setSourceMap(debugger, subsys, source_path)
                logger.info(f"Source mapping set for {subsys}: {source_path}")
            else:
                logger.warning(f"Source path not found or invalid: {source_path}")
        except Exception as e:
            logger.warning(f"Unable to set the source code map; using default behavior. "
                          f"Error: {type(e).__name__}: {e}")

        # Establish GDB connection with LLDB
        try:
            logger.info("Establishing gdb connection to AOSS target...")
            debugger.HandleCommand(aoss_gdb_remote_connection)
            logger.info("GDB remote connection established successfully.")
            logger.info("---- AOP entry point reached ----")
        except Exception as e:
            logger.error(f"Unable to establish GDB connection: {str(e)}")
            logger.error(traceback.format_exc())

    else:
        logger.error(f"Invalid step: {step}. Expected 1 or 2")
        return
