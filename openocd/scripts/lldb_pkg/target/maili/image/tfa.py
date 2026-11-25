#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file tz.py
#
# @section Description
# This file contains a script for TZ image debugging.

import os
import sys
import time
import lldb
from utils.debug_context import logger
from utils.source_map import setSourceMap
from utils.debug_context import g_dbgContext
from lldb_pkg.target.maili.image.xbl import debugXBL

def debugTFA(debugger: lldb.SBDebugger, command, exe_ctx, result, inputarg = None, subsys = None):
    """!   
    This function is used to define a command to debug tz. First we start with 
    hard-reset and halt at sbl1_main_ctl similar to XBL debug. Then we load 
    mon.elf and qsee.elf are loaded. We halt at tzbsp_mmu_enable and hit a 
    breakpoint at tzbsp_cold_and_warm_boot_cmn and then find the 
    aslr_reloc_offset. 
    We relocate tz symbols with this offset and are finally able to halt at
    "tzbsp_boot".                                                 

    Usage : debug -d tz  

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

    # global g_dbgContext
    if (inputarg == None):  
        logger.info(" --- Preparing for Debug of TFA  Image ---")

    # Chipset_name = g_dbgContext.chipsetName.lower()
    debugXBL(debugger, command, exe_ctx, result, "run")
    fileName ='bl31.elf'
    tfa_elf_path = g_dbgContext.openocdConfig.getBinaryPath('tfa', fileName)
    if os.path.exists(tfa_elf_path):
        mon_dSYM_cmd = "{}.dSYM".format(tfa_elf_path)
        module = g_dbgContext.tgt.AddModule (tfa_elf_path, None, None, mon_dSYM_cmd)
        g_dbgContext.tgt.SetModuleLoadAddress (module, 0)

    try:
        sourcePathSet = g_dbgContext.sourcePath["tfa"]
        setSourceMap(debugger, "tfa", sourcePathSet )
    except:
        pass

    cmd = "br s -n bl31_entrypoint --hardware -o true"
    debugger.HandleCommand(cmd)
    debugger.HandleCommand("c")

