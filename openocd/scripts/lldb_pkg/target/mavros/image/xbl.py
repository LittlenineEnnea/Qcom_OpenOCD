#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file xbl.py
#
# @brief XBL (eXtensible Boot Loader) image debugging script for Mavros platform.
#
# @section Description
# This file contains a script for XBL image debugging.

import os
import sys
import time
import lldb
from utils.dap_access import *
from utils.debug_context import logger
from utils.source_map import setSourceMap
from utils.debug_context import g_dbgContext
from utils.stop_wait import wait_for_breakpoint_hit
from lldb_pkg.target.mavros.image.pbl import pbl_workaround


def debugXBL(debugger: lldb.SBDebugger, command, exe_ctx, result, inputarg = None):
    """!
    This function is used to define a command to debug xbl, XBLloader.dll is
    loaded and breakpoints are set at "sbl1_main_ctl" and "boot_do_procedures".

    Usage : debug -d xbl

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

    @param inputarg         -   a variable used to denote whether the command
                                is being used from inside the script or being
                                used from lldb (by the user).

    """

    # Get APSS port information and frame connect command
    connect_cmd = "connect_remote -s apss0"

    # If this script is called from another script, avoid this print
    if inputarg == None:
        logger.info("--- Preparing for Debug of XBL Image ---")

    # LLDB configuration setup
    chipset_name = g_dbgContext.chipsetName.lower()
    debugger.HandleCommand("settings set plugin.process.gdb-remote.packet-timeout 10000000")
    debugger.HandleCommand("platform select remote-linux")
    debugger.HandleCommand("settings set auto-confirm on")

    elf_name = 'xbl_sc.elf'

    # Get path to XBL Loader ELF from boot build
    xbl_elf_path = g_dbgContext.openocdConfig.getBinaryPath('boot', elf_name)

    # Create target
    if os.path.exists(xbl_elf_path):
        g_dbgContext.tgt = debugger.CreateTarget(xbl_elf_path)
    else:
        logger.error(f"Unable to find {elf_name}")
        sys.exit(1)

    # Set source path if provided in settings.json
    try:
        sourcePathSet = g_dbgContext.sourcePath["boot"]
        setSourceMap(debugger, "boot", sourcePathSet)
    except Exception as e:
        logger.warning(f"Unable to set the source code map; using default behavior.")

    # Reading the wait time from chip_config.xml to wait for re-attach after pmic hard reset
    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chip_name = chip.get('name')
        if chip_name.upper() == chipset_name.upper():
            sleep_config = chip.find('sleep_config')
            for sleep in sleep_config.iter('pmic_sleep'):
                pmic_sleep = int(sleep.attrib['sleep_time'])

            pbl_apss_config = chip.find('pbl_apss_config')
            if pbl_apss_config is None:
                logger.error(f"Could not find pbl_apss_config for chipset {chipset_name}")
                sys.exit(1)
            wfi_non_ncc_elem = pbl_apss_config.find('wfi_non_ncc')
            if wfi_non_ncc_elem is None:
                logger.error(f"Could not find wfi_non_ncc for chipset {chipset_name}")
                sys.exit(1)
            wfi_non_ncc_addr = wfi_non_ncc_elem.attrib['addr']
            break
    else:
        logger.error(f"Could not find sleep_config/pmic_sleep for chipset {chipset_name}")
        sys.exit(1)

    # If pmic sleep time is read as 0, skip pmic reset (for targets without pmic reset support)
    # Note: Core reset via pbl_workaround() is still performed regardless of pmic_sleep value.
    if pmic_sleep != 0:
        # Perform pmic hard reset. This will reset device
        debugger.HandleCommand("pmic_hardreset")
        time.sleep(pmic_sleep)

    # PBL workaround to stop at pbl entry
    pbl_workaround(debugger)
    # Try re-Connection after pmic hard reset
    logger.info("Trying to reconnect to target")
    debugger.HandleCommand(connect_cmd)
    logger.info("Connected to target")
    debugger.HandleCommand(f"br s -a {wfi_non_ncc_addr} -o true")
    debugger.HandleCommand("register write x8 0x0")
    debugger.HandleCommand("c")

    bkpt_fun = 'sbl1_main_ctl'
    bp_obj = g_dbgContext.tgt.BreakpointCreateByName(bkpt_fun)
    bp_obj.SetOneShot(True)

    # get the running process info
    process = g_dbgContext.tgt.GetProcess()
    debugger.SetAsync(True)
    process.Continue()
    debugger.SetAsync(False)
    if not wait_for_breakpoint_hit(process):
        logger.info("--- FBC: failed to reach XBL entrypoint")
        sys.exit(1)

    if inputarg:
        # if called from inside a script/another function return
        return
    else:
        logger.info("--- FBC: XBL entrypoint reached in debug mode")
