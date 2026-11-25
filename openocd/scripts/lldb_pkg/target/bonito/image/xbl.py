#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file xbl.py
#
# @section Description
# This file contains a script for XBL image debugging.

import time
import os
import sys
import lldb
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.source_map import setSourceMap
from utils.reset import ResetCore

def debugXBL(debugger, command, exe_ctx, result, inputarg = None):
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
    # Defaulting adi_version to 5
    adi_version = 5

    # Get APSS port information and frame connect command
    connect_cmd = "connect_remote -s {}".format("apss")
    
    # If this script is called from another script, avoid this print
    if (inputarg == None):  
        logger.info (" --- Preparing for Debug of XBL  Image ---")

    # LLDB configuration setup
    Chipset_name = g_dbgContext.chipsetName.lower()
    debugger.HandleCommand("settings set plugin.process.gdb-remote.packet-timeout 10000000")
    debugger.HandleCommand("platform select remote-linux")
    debugger.HandleCommand("settings set auto-confirm on")

    # Filname for XBL Loader
    fileName ='XBLLoader.dll'

    # Get path to XBL Loader DLL from boot build
    xbl_elf_path = g_dbgContext.openocdConfig.getBinaryPath ('boot', fileName)
    
    # Kodiak -IOT Target specific check
    # CLANG version got changed from CLANG100 to CLANG140 for Kodiak-IoT(qcm6490) but is not updated in contents.xml
    if (Chipset_name == 'qcm6490'):
        xbl_elf_path = xbl_elf_path.replace("CLANG100", "CLANG140")

    # Create target
    if (os.path.exists(xbl_elf_path)):
        g_dbgContext.tgt = debugger.CreateTarget(xbl_elf_path)
    else:
        logger.error(" Unable to find XBLLoader.dll")
        sys.exit(1)
    
    # Set source path if provided in settings.json
    try :
        sourcePathSet =  g_dbgContext.sourcePath["boot"]
        setSourceMap(debugger,"boot", sourcePathSet )
    except:
        pass

    # Reading the wait time from chip_config.xml to wait for re-attach after pmic hard reset
    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname=chip.get('name')
        if (chipname.upper() == Chipset_name.upper()):
            adi = chip.find('adi')
            sleep_config = chip.find('sleep_config')
            for sleep in sleep_config.iter('pmic_sleep'):
                pmic_sleep = int(sleep.attrib['sleep_time'])

    if adi is not None:
        adi_version = int(adi.find('version').attrib['adi_version'])

    # Get xbl loader module details
    Module = g_dbgContext.tgt.GetModuleAtIndex(0)

    # If pmic sleep time is read as 0, connect to target without doing pmic reset
    # This is added for targets not having pmic reset enabled via openocd   
    if (pmic_sleep == 0):
        logger.info (" Trying to connect to target")
        debugger.HandleCommand (connect_cmd)
        logger.info (" Connected to target")
        debugger.HandleCommand ("br del")
        if Chipset_name == "aspen":
            debugger.HandleCommand ("qreset")
            time.sleep(0.2)
            bkpt_fun = 'sbl1_main_ctl'
        else:
            bkpt_fun = 'boot_do_procedures'
        bp_obj = g_dbgContext.tgt.BreakpointCreateByName(bkpt_fun)
    else:
        # Perform pmic hard reset. This will reset device
        debugger.HandleCommand ("pmic_hardreset")
        time.sleep(pmic_sleep)

        # OpenOCD shoutdown is need for Jlink and Olimex dongles
        # to disconnect the existing gdb connection after pmic hard reset
        if g_dbgContext.debug_adapter != "eud":
            debugger.HandleCommand("process plugin packet monitor shutdown")
        # Perform step operation to let lldb know that connection is broken and raise a timeout
        debugger.HandleCommand ("s")

        # Try re-Connection after pmic hard reset
        logger.info (" Trying to reconnect to target")
        debugger.HandleCommand (connect_cmd)
        logger.info (" Connected to target")
        
        # Bypassing monitor reset for Hamoa & Molokai
        if Chipset_name == "hamoa" or Chipset_name == "molokai":
            pass
        else:
            ResetCore(debugger)
            # OpenOCD disconnection is required for ADIv6 targtes to work softreset
            if adi_version == 6 :
                debugger.HandleCommand("process plugin packet monitor shutdown")
                debugger.HandleCommand ("s")
                time.sleep(5)
                logger.info (" Trying to reconnect to target")
                debugger.HandleCommand (connect_cmd)
                logger.info (" Connected to target")
        
        # Set XBL Entry breakpoint
        if (Chipset_name == 'matrix' or Chipset_name == 'milos'):
            bkpt_fun = 'boot_log_early_info'
        elif chipname == "hamoa" or chipname == "molokai":
            bkpt_fun = "boot_do_procedures"
        else:
            bkpt_fun = 'sbl1_main_ctl' 
        bp_obj = g_dbgContext.tgt.BreakpointCreateByName(bkpt_fun)

    # get the running process info
    process = g_dbgContext.tgt.GetProcess()
    debugger.HandleCommand ("c")

    # get the process state
    state = process.GetState()

    # checking if the process halted at the breakpoint
    if state == lldb.eStateStopped:
        thread = process.GetSelectedThread()
        if thread.GetStopReason() == lldb.eStopReasonBreakpoint:
            # if halted at the breakpoint continue further
            if bp_obj.GetHitCount() == 1:
                pass
            else:
                logger.info (" --- FBC: failed to reach XBL entrypoint")
                sys.exit(1)
    if (inputarg):
        # if called from inside a script/another function return
        return 
    else:
        logger.info (" --- FBC: XBL entrypoint reached in debug mode")
