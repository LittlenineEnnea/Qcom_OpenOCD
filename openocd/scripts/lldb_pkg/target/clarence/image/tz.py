#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file tz.py
#
# @section Description
# This file contains a script for TZ image debugging.

import time
import os
import sys
import lldb
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.source_map import setSourceMap
from lldb_pkg.target.clarence.image.xbl import debugXBL
from lldb_pkg.target.clarence.image.debugwithpil import pil_debugger

def debugTZ(debugger, command, exe_ctx, result, inputarg = None, subsys = None):
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
    args = command.split()

    if len(args) > 1:
        pass
    
    if args[0].lower() == "tz":
        logger.info (" --- Loading symbols for TZ Image ---")
        g_dbgContext.tgt = debugger.GetSelectedTarget()
    elif inputarg == None:
        logger.info (" --- Preparing for Debug of TZ  Image ---")

    connect_cmd = "connect_remote -s {}".format("apss")
    fileName ='mon.elf'
    mon_elf_path = g_dbgContext.openocdConfig.getBinaryPath ('tz', fileName)

    fileName ='qsee.elf'
    qsee_elf_path = g_dbgContext.openocdConfig.getBinaryPath ('tz', fileName)
    Chipset_name = g_dbgContext.chipsetName.lower()

    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname=chip.get('name')
        if (chipname.upper() == Chipset_name.upper()):
            sleep_config = chip.find('sleep_config')
            pmic_sleep = sleep_config.find('pmic_sleep')
            sleep = int(pmic_sleep.attrib['sleep_time'])

    if (args[0].lower() != "tz"):
        debugXBL(debugger, command, exe_ctx, result, "run")
    
    if (os.path.exists(mon_elf_path)):
        mon_dSYM_cmd = "{}.dSYM".format(mon_elf_path)
        module1 = g_dbgContext.tgt.AddModule (mon_elf_path, None, None, mon_dSYM_cmd)
    else:
        logger.error(" Unable to find mon.elf")
        sys.exit(1)

    if (os.path.exists(qsee_elf_path)):
        qsee_dSYM_cmd = "{}.dSYM".format(qsee_elf_path)
        module2 = g_dbgContext.tgt.AddModule (qsee_elf_path, None, None, qsee_dSYM_cmd)
    else:
        logger.error(" Unable to find qsee.elf")
        sys.exit(1)
    # to prevent halting of the debug execution due to exception
    # when incorrect or no source path is set
    try :
        sourcePathSet =  g_dbgContext.sourcePath["tz"]
        setSourceMap(debugger,"tz", sourcePathSet )
    except:
        pass

    logger.info (" module1 = %s", module1)
    logger.info (" module2 = %s", module2)

    sym1 = module2.FindSymbols ("tzbsp_cold_and_warm_boot_cmn")
    sym2 = module2.FindSymbols ("aslr_reloc_offset")
    sym3 = module2.FindSymbols ("tzbsp_mmu_enable")
    try:
        sym4 = module1.FindSymbols ("mon_cold_entry")
    except:
        sym4 = module2.FindSymbols ("mon_warm_entry")
    
    tzbsp_cold_and_warm_boot_cmn_addr = sym1[0].symbol.GetValue()
    aslr_reloc_offset_addr = sym2[0].symbol.GetValue()
    tzbsp_mmu_enable_addr = sym3[0].symbol.GetValue()
    mon_entry_addr = sym4[0].symbol.GetValue()

    if (args[0].lower() != "tz"):
        cmd = "br s -a {} --hardware".format(hex(mon_entry_addr))
        debugger.HandleCommand (cmd)
        debugger.HandleCommand ('c')
        if (sleep > 10):
            time.sleep(5)
            debugger.HandleCommand (connect_cmd)
            
        cmd = "br s -a {} --hardware".format(hex(tzbsp_mmu_enable_addr))
        debugger.HandleCommand (cmd)
        debugger.HandleCommand ('c')
        debugger.HandleCommand ('up')
        debugger.HandleCommand ('br del')

        cmd = "br s -a {} --hardware".format(hex(tzbsp_cold_and_warm_boot_cmn_addr))
        debugger.HandleCommand (cmd)
        debugger.HandleCommand ('c')
        debugger.HandleCommand ('br del')

    process = debugger.GetSelectedTarget().GetProcess()
    error_ref = lldb.SBError()
    aslr_reloc_offset = process.ReadMemory(aslr_reloc_offset_addr, 8, error_ref)   
    aslr_reloc_offset = hex(int.from_bytes(aslr_reloc_offset, byteorder ='little'))
    logger.info (' aslr_reloc_offset is %s', aslr_reloc_offset)
    g_dbgContext.tgt.SetModuleLoadAddress (module2, int(aslr_reloc_offset, 16))

    if (args[0].lower() != "tz"):
        cmd = 'br s -n tzbsp_boot --hardware'
        debugger.HandleCommand (cmd)
        debugger.HandleCommand ("c")
    
    if (inputarg):
        # if called from inside a script/another function return
        return
    elif (subsys):
        wait_pil_tz = pil_debugger(debugger, command, result, subsystem=subsys)
        return wait_pil_tz
    else:
        logger.info (" --- FBC: TZ entrypoint reached in debug mode")
