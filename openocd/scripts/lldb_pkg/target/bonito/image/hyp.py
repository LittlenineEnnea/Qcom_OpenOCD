#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file hyp.py
#
# @section Description
# This file contains a script for HYP image debugging.

import os
import sys
import time
import lldb
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.dap_access import dapAccessGDB
from lldb_pkg.target.bonito.image.tz import debugTZ
from utils.source_map import setSourceMap
from lldb_pkg.target.bonito.image.debugwithpil import pil_debugger


def debugHYP(debugger, command, exe_ctx, result, inputarg = None, subsys = None):
    """!   
    This function helps to debug hypervisor. We derive aslr_reloc_offset 
    similar to how we do in TZ debug. After which we set two breakpoints at
    GUNYA and QHEE entrypoints, based on which of these is hit, we proceed 
    with the pc value. We load  hypvm.elf and hit the breakpoint at 
    aarch64_init_address_space, we disable watchdogs and then find out 
    aarch64_kaslr_base to find the 64 bit offset address required for 
    hypervisor debug.  We finally halt "boot_cold_init".  
                                              
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
    
    if args[0].lower() == "hyp":
        logger.info (" --- Loading symbols for HYP Image ---")
        g_dbgContext.tgt = debugger.GetSelectedTarget()
    elif inputarg == None:
        logger.info (" --- Preparing for Debug of HYP  Image ---")

    Chipset_name = g_dbgContext.chipsetName.lower()
    returnObj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()

    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname=chip.get("name")
        if (chipname.upper() == Chipset_name.upper()):
            wdog_config = chip.find("wdog_config")
            hyp_config = chip.find("hyp_config")

    HYP_QHEE_ENTRY_VECTOR_ADDR = hyp_config.find('QHEE_ENTRY_VECTOR_ADDR').attrib['address']
    HYP_GUNY_ENTRY_VECTOR_ADDR = hyp_config.find('GUNY_ENTRY_VECTOR_ADDR').attrib['address']
    sec_wdog = wdog_config.find('WDOG_CTL_REG').attrib['reg']

    if (subsys):
        wait_pil_tz = debugTZ(debugger, command, exe_ctx, result, subsys=subsys)
    elif args[0].lower() != "hyp":
        debugTZ(debugger, command, exe_ctx, result, "run")

        if HYP_GUNY_ENTRY_VECTOR_ADDR != None:
            cmd = "br s -a {} --hardware".format(HYP_GUNY_ENTRY_VECTOR_ADDR)
            debugger.HandleCommand(cmd)
        if HYP_QHEE_ENTRY_VECTOR_ADDR != None:
            cmd = "br s -a {} --hardware".format(HYP_QHEE_ENTRY_VECTOR_ADDR)
            debugger.HandleCommand(cmd)

        debugger.HandleCommand("c")
        cmd = "register read pc"
        commandInterpreter.HandleCommand( cmd, returnObj )
        if returnObj.Succeeded():
            res = str(returnObj.GetOutput())
            res = res[21:]
            pc = "0x{}".format(res)
            pc = pc.replace("\n","")
        
        if(int(pc, 16) == int(HYP_GUNY_ENTRY_VECTOR_ADDR, 16)):
            fileName ='hypvm.elf'
            hyp_elf_path = g_dbgContext.openocdConfig.getBinaryPath ('tz',fileName)
            if(os.path.exists(hyp_elf_path)):
                Chipset_name = g_dbgContext.chipsetName.lower()
                hyp_dSYM_cmd = "{}.dSYM".format(hyp_elf_path)
                g_dbgContext.tgt.AddModule (hyp_elf_path, None, None, hyp_dSYM_cmd)
            else:
                logger.error (" unable to find hypvm.elf")
                sys.exit(1)

            time.sleep(4)
            selTarget = debugger.GetSelectedTarget()
            module4 = selTarget.modules[3]
            g_dbgContext.tgt.SetModuleLoadAddress (module4, int(HYP_GUNY_ENTRY_VECTOR_ADDR, 16))
            time.sleep(4)
            debugger.HandleCommand("br del")
            debugger.HandleCommand("br s -n aarch64_init_address_space --hardware")
            debugger.HandleCommand("c")
            # disabling secure wdog if it is enabled
            res = dapAccessGDB( debugger, command, result, "script", sec_wdog)
            res = int(res, 16)
            sec_wdog_disable = int("0x0", 16)
            if ( res != sec_wdog_disable):
                cmd = "dap_access -w {} -v 0x0".format(sec_wdog)

            # getting the ASLR_BASE using aarch64_kaslr_base address
            aslr = g_dbgContext.tgt.EvaluateExpression("&aarch64_kaslr_base")
            aslr = str(aslr)
            aarch64_kaslr_base = "0x{}".format(aslr[25:])
            address = hex(int(aarch64_kaslr_base, 16))
            address = address.replace("L","") 
            offset1 = dapAccessGDB( debugger, command, result, "script", address)
            offset1 = offset1.replace("0x","")
            
            aarch64_kaslr_base_1 = int(aarch64_kaslr_base, 16) + int(str(0x4), 16)
            aarch64_kaslr_base_1 = hex(aarch64_kaslr_base_1).rstrip("L")
            address = hex(int(aarch64_kaslr_base_1, 16))
            address = address.replace("L","") 
            offset2 = dapAccessGDB( debugger, command, result, "script", address)
            offset2 = offset2
            ASLR_BASE = offset2 + offset1
            cmd = "target modules load --file {} --slide {}".format(hyp_elf_path, ASLR_BASE)
            debugger.HandleCommand(cmd)

        elif(int(pc, 16) == int(HYP_QHEE_ENTRY_VECTOR_ADDR, 16)):
            logger.info (" Qhee hyp")
        
        else:
            logger.error (" TZ:  Unable to reach HYP entry-point...Quitting...")
            sys.exit(1)

        debugger.HandleCommand("br del")

        # to prevent halting of the debug execution due to exception
        # when incorrect or no source path is set
        try :
            sourcePathSet =  g_dbgContext.sourcePath["tz"]
            setSourceMap(debugger,"tz", sourcePathSet )
        except:
            pass
        debugger.HandleCommand("br s -n boot_cold_init --hardware")
        debugger.HandleCommand("c")
        if (inputarg):
            return
        elif (subsys):
            debugger.HandleCommand("br s -n init_qheebsp")
            debugger.HandleCommand("c")
            wait_pil_hyp = pil_debugger(debugger, command, result, subsystem=subsys)

            if (wait_pil_tz == True) or (wait_pil_hyp == True):
                return True
            else :
                return False
        else:
            logger.info (" --- FBC: HYP entrypoint reached in debug mode")
    else:
        fileName ='hypvm.elf'
        hyp_elf_path = g_dbgContext.openocdConfig.getBinaryPath ('tz',fileName)
        if(os.path.exists(hyp_elf_path)):
            Chipset_name = g_dbgContext.chipsetName.lower()
            hyp_dSYM_cmd = "{}.dSYM".format(hyp_elf_path)
            module = g_dbgContext.tgt.AddModule (hyp_elf_path, None, None, hyp_dSYM_cmd)
        else:
            logger.error (" unable to find hypvm.elf")
            sys.exit(1)
        # module = selTarget.modules[3]
        g_dbgContext.tgt.SetModuleLoadAddress (module, int(HYP_GUNY_ENTRY_VECTOR_ADDR, 16))
        res = dapAccessGDB( debugger, command, result, "script", sec_wdog)
        res = int(res, 16)
        sec_wdog_disable = int("0x0", 16)
        if ( res != sec_wdog_disable):
            cmd = "dap_access -w {} -v 0x0".format(sec_wdog)

        # getting the ASLR_BASE using aarch64_kaslr_base address
        aslr = g_dbgContext.tgt.EvaluateExpression("&aarch64_kaslr_base")
        aslr = str(aslr)
        aarch64_kaslr_base = "0x{}".format(aslr[25:])
        address = hex(int(aarch64_kaslr_base, 16))
        address = address.replace("L","") 
        offset1 = dapAccessGDB( debugger, command, result, "script", address)
        offset1 = offset1.replace("0x","")
        
        aarch64_kaslr_base_1 = int(aarch64_kaslr_base, 16) + int(str(0x4), 16)
        aarch64_kaslr_base_1 = hex(aarch64_kaslr_base_1).rstrip("L")
        address = hex(int(aarch64_kaslr_base_1, 16))
        address = address.replace("L","") 
        offset2 = dapAccessGDB( debugger, command, result, "script", address)
        offset2 = offset2
        ASLR_BASE = offset2 + offset1
        cmd = "target modules load --file {} --slide {}".format(hyp_elf_path, ASLR_BASE)
        debugger.HandleCommand(cmd)
        logger.info ("Symbol loading success")