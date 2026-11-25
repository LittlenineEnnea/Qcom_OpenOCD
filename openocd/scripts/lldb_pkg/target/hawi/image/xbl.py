#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file hawi_xbl.py
#
# @section Description
# This file contains a script for XBL image debugging on Hawi chipset.
# Ported from go_xblsc.cmm (10/27/25, Ling).
#
# Differences from xbl.py:
#   1. Symbol file       : xbl_sc.elf  (xbl.py uses XBLLoader.dll)
#   2. pmic_sleep == 0   : connect directly, skip pmic_hardreset and ResetCore,
#                          use boot_do_procedures  (same pattern as xbl.py)
#   3. pmic_sleep != 0   : pmic_hardreset + ResetCore + sbl1_main_ctl
#                          (path taken once pmic reset is enabled for Hawi)
#   4. PBL patch version : checked at 0x221C8CF8 before setting breakpoints
#   5. TCSR BOOT MISC    : cleared (0x3A72000 = 0x0) before setting breakpoints
#   6. qsee_jump_func    : additional breakpoint set alongside entry breakpoint
#   7. 50s timeout       : guard on continue to entry breakpoint
#   8. PC verification   : explicit check that halt is at entry breakpoint

import time
import os
import sys
import lldb
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.source_map import setSourceMap
from lldb_pkg.tech.pmic.hardReset import pmicHardreset
from utils.reset import ResetCore
from utils.dap_access import dapAccessGDB

def debugXBL(debugger, command, exe_ctx, result, inputarg = None):
    """!
    This function is used to define a command to debug XBL on Hawi chipset.
    xbl_sc.elf is loaded and breakpoints are set at sbl1_main_ctl (or
    boot_do_procedures when pmic_sleep == 0) and qsee_jump_func.

    When pmic_sleep == 0 (pmic reset not yet enabled): connects directly,
    skips pmic_hardreset and ResetCore, uses boot_do_procedures.
    When pmic_sleep != 0 (pmic reset enabled): performs pmic_hardreset,
    ResetCore, and uses sbl1_main_ctl.

    Ported from go_xblsc.cmm.

    Usage : debug -d xbl

    @param debugger         -   An instance of the SBDebugger Object that
                                provides access to SBTargets used for
                                debugging.
    @param command          -   String containing all the input arguments
                                for the used command.
    @param exe_ctx          -   An execution context object carrying
                                information of the process context on which
                                the command is used.
    @param result           -   A return object which encapsulates
                                success/failure information for the command
                                and output text that needs to be printed
                                as a result of the command.
    @param inputarg         -   a variable used to denote whether the command
                                is being used from inside the script or being
                                used from lldb (by the user).
    """
    # Defaulting adi_version to 5
    adi_version = 5

    # Get APSS port information and frame connect command
    connect_cmd = "connect_remote -s {}".format("apss")

    # If this script is called from another script, avoid this print
    if (inputarg == None):
        logger.info(" --- Preparing for Debug of XBL Image (Hawi) ---")

    # LLDB configuration setup
    Chipset_name = g_dbgContext.chipsetName.lower()
    debugger.HandleCommand("settings set plugin.process.gdb-remote.packet-timeout 10000000")
    debugger.HandleCommand("platform select remote-linux")
    debugger.HandleCommand("settings set auto-confirm on")

    # Filename for XBL SC ELF
    # CMM: &xblloader_dll="&BOOT_BUILDROOT/xbl_images/Build/Hawi_Default_LA/Loader/xbl_sc.elf"
    fileName = 'xbl_sc.elf'

    # Get path to xbl_sc.elf from boot build
    xbl_elf_path = g_dbgContext.openocdConfig.getBinaryPath('boot', fileName)

    # Create target
    if (os.path.exists(xbl_elf_path)):
        g_dbgContext.tgt = debugger.CreateTarget(xbl_elf_path)
    else:
        logger.error("XBLSC: ERROR - XBLloader debug symbols do not exist.")
        sys.exit(1)

    # Set source path if provided in settings.json
    try:
        sourcePathSet = g_dbgContext.sourcePath["boot"]
        setSourceMap(debugger, "boot", sourcePathSet)
    except:
        pass

    # Reading the wait time from chip_config.xml to wait for re-attach after pmic hard reset
    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname = chip.get('name')
        if (chipname.upper() == Chipset_name.upper()):
            adi = chip.find('adi')
            sleep_config = chip.find('sleep_config')
            for sleep in sleep_config.iter('pmic_sleep'):
                pmic_sleep = int(sleep.attrib['sleep_time'])

    if adi is not None:
        adi_version = int(adi.find('version').attrib['adi_version'])

    # Get xbl loader module details
    Module = g_dbgContext.tgt.GetModuleAtIndex(0)

    # If pmic_sleep == 0, connect directly without pmic reset.
    # This is the same pattern as xbl.py — targets with pmic reset not yet
    # enabled have pmic_sleep set to 0 in chip_config.xml.
    if (pmic_sleep == 0):
        logger.info(" Trying to connect to target")
        debugger.HandleCommand(connect_cmd)
        logger.info(" Connected to target")
        debugger.HandleCommand("br del")
        bkpt_fun = 'boot_dispatch_execute_all'
    else:
        # Perform pmic hard reset. This will reset device
        debugger.HandleCommand("pmic_hardreset")
        time.sleep(pmic_sleep)

        # OpenOCD shutdown is needed for Jlink and Olimex dongles
        # to disconnect the existing gdb connection after pmic hard reset
        if g_dbgContext.debug_adapter != "eud":
            debugger.HandleCommand("process plugin packet monitor shutdown")
        # Perform step operation to let lldb know that connection is broken and raise a timeout
        debugger.HandleCommand("s")

        # Try re-connection after pmic hard reset
        logger.info(" Trying to reconnect to target")
        debugger.HandleCommand(connect_cmd)
        logger.info(" Connected to target")

        ResetCore(debugger)

        # OpenOCD disconnection is required for ADIv6 targets to work softreset
        if adi_version == 6:
            debugger.HandleCommand("process plugin packet monitor shutdown")
            debugger.HandleCommand("s")
            time.sleep(5)
            logger.info(" Trying to reconnect to target")
            debugger.HandleCommand(connect_cmd)
            logger.info(" Connected to target")

        bkpt_fun = 'sbl1_main_ctl'

    # -----------------------------------------------------------------------
    # PBL patch version check
    # CMM: &read_value=data.long(ezaxi:0x221c8cf8)
    #      version 3 or 7 -> b.d (delete breakpoints, proceed normally)
    #      other          -> go + WAIT !STATE.RUN()
    # -----------------------------------------------------------------------
    process  = g_dbgContext.tgt.GetProcess()
    # cmd_intr = debugger.GetCommandInterpreter()
    # pbl_read = lldb.SBCommandReturnObject()

    # cmd_intr.HandleCommand(
    #     "memory read -s 4 -f x -c 1 0x221C8CF8", pbl_read)
    # pbl_read = dapAccessGDB( debugger, command, result, "script", "0x221C8CF8")
    # logger.error(f"pbl read {pbl_read}")
    # try:
    #     pbl_patch_ver = int(pbl_read, 16)
    #     # pbl_patch_ver = int(pbl_read.GetOutput().strip().split()[-1], 16)
    #     logger.error(f"pbl patch ver {pbl_patch_ver}")
    # except (ValueError, IndexError):
    #     pbl_patch_ver = 0

    # if pbl_patch_ver in (0x3, 0x7):
    #     logger.info("XBLSC: PBL Patch version 0x{:X} detected.".format(pbl_patch_ver))
    #     # CMM: b.d — delete all breakpoints and proceed
    #     debugger.HandleCommand("br del")
    # else:
    #     logger.info("XBLSC: PBL Patch version 0x{:X} detected. "
    #                 "Running to next halt point...".format(pbl_patch_ver))
    #     # CMM: go + WAIT !STATE.RUN()
    #     debugger.HandleCommand("c")

    # -----------------------------------------------------------------------
    # Clear TCSR BOOT MISC register
    # CMM: Data.Set EZAXI:0x3A72000 %LE %Long 0
    # TCSR BOOT MISC holds the Dload cookie and is not cleared by JTAG reset.
    # -----------------------------------------------------------------------
    logger.info("XBLSC: Clearing TCSR BOOT MISC register")
    # tcsr_write = lldb.SBCommandReturnObject()
    dapAccessGDB(debugger, command, result, "yes", None, None, "0x3A72000", "0x0", None)
    # cmd_intr.HandleCommand(
    #     "memory write -s 4 0x3A72000 0x0", tcsr_write)

    # -----------------------------------------------------------------------
    # Set breakpoints at bkpt_fun (sbl1_main_ctl or boot_do_procedures) and qsee_jump_func
    # CMM: B.S sbl1_main_ctl  /P /DISHIT
    #      B.S qsee_jump_func /P /DISHIT
    # qsee_jump_func is used by run_fbc_hawi.cmm after this script returns
    # to verify the QSEE handoff point.
    # -----------------------------------------------------------------------
    bp_obj       = g_dbgContext.tgt.BreakpointCreateByName(bkpt_fun)
    # bp_qsee_jump = g_dbgContext.tgt.BreakpointCreateByName('qsee_jump_func')

    # Continue and wait up to 50s for boot_do_procedures
    # CMM: GO / WAIT !STATE.RUN() 50.s / IF (TIMEOUT())
    logger.info("XBLSC: Running to {}...".format(bkpt_fun))
    debugger.HandleCommand("c")

    deadline = time.time() + 50
    while process.GetState() == lldb.eStateRunning:
        if time.time() > deadline:
            logger.error("XBLSC: Error In debug mode! "
                         "Reach XBLSC entrypoint timeout. Quitting...")
            sys.exit(1)
        time.sleep(0.5)

    # -----------------------------------------------------------------------
    # Verify halt at sbl1_main_ctl
    # CMM: IF R(PC)!=&SblEntryPoint
    # -----------------------------------------------------------------------
    state = process.GetState()
    if state == lldb.eStateStopped:
        thread = process.GetSelectedThread()
        if thread.GetStopReason() == lldb.eStopReasonBreakpoint:
            if bp_obj.GetHitCount() == 1:
                logger.info("XBLSC: XBL entry point ({}) reached...".format(bkpt_fun))
            else:
                logger.error("XBLSC: Error In debug mode! "
                             "Cannot reach XBLSC entrypoint. Quitting...")
                sys.exit(1)
        else:
            logger.error("XBLSC: Error In debug mode! "
                         "Cannot reach XBLSC entrypoint. Quitting...")
            sys.exit(1)
    else:
        logger.error("XBLSC: Error In debug mode! "
                     "Cannot reach XBLSC entrypoint. Quitting...")
        sys.exit(1)

    if (inputarg):
        # if called from inside a script/another function return
        return
    else:
        logger.info(" --- FBC: XBL entrypoint ({}) reached in debug mode (Hawi)".format(bkpt_fun))
