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
import re
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.dap_access import dapAccessGDB
from lldb_pkg.target.hawi.image.tz import debugTZ
from utils.source_map import setSourceMap
from lldb_pkg.arch.arm.coprocessor_reg import registerRead
from lldb_pkg.target.hawi.image.debugwithpil import pil_debugger

def get_pc_address_type(debugger, command, exe_ctx, result, commandInterpreter, returnObj):
    """
    Determine if the current PC is a physical or virtual address.
    
    Returns:
        bool: True if PC is in kernel virtual address space, False otherwise
    """
    
    # Read PC register
    cmd = "register read pc"
    commandInterpreter.HandleCommand(cmd, returnObj)
    if not returnObj.Succeeded():
        return False
    pc_output = str(returnObj.GetOutput())
    pc_match = re.search(r'0x[0-9a-fA-F]+', pc_output)
    if not pc_match:
        return False
    
    pc = pc_match.group(0)
    pc_int = int(pc, 16)
    # Read SCTLR_EL1 to check if MMU is enabled
    sctlr_output = registerRead(debugger, command, exe_ctx, result, "sctlr_el1", "yes")
    parts = sctlr_output.split(':')
    if len(parts) >= 2:
        pass
    sctlr_match = re.search(r'0x[0-9a-fA-F]+', sctlr_output)
    if not sctlr_match:
        return False
    
    sctlr_val = int(sctlr_match.group(0), 16)
    mmu_enabled = (sctlr_val & 0x1) != 0
    # Determine address type
    if not mmu_enabled:
        debugger.HandleCommand("c")
        time.sleep(0.1)
        return get_pc_address_type(debugger, command, exe_ctx, result, commandInterpreter, returnObj)  # Return the recursive call
    elif pc_int >= 0xFFFF000000000000:
        return True
    elif pc_int <= 0x0000FFFFFFFFFFFF:
        return False
    return False  # Default fallback


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
    if (inputarg == None):
        logger.info (" --- Preparing for Debug of HYP Image ---")
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
    # sec_wdog = wdog_config.find('WDOG_CTL_REG').attrib['reg']

    if (subsys):
        wait_pil_tz = debugTZ(debugger, command, exe_ctx, result, subsys=subsys)
    else:
        debugTZ(debugger, command, exe_ctx, result, "run")

    if HYP_GUNY_ENTRY_VECTOR_ADDR != None:
        cmd = "br s -a {} --hardware".format(HYP_GUNY_ENTRY_VECTOR_ADDR)
        debugger.HandleCommand(cmd)
    if HYP_QHEE_ENTRY_VECTOR_ADDR != None:
        cmd = "br s -a {} --hardware".format(HYP_QHEE_ENTRY_VECTOR_ADDR)
        debugger.HandleCommand(cmd)

    debugger.HandleCommand("c")
    cmd = "register read pc"
    commandInterpreter.HandleCommand(cmd, returnObj)
    if returnObj.Succeeded():
        res = str(returnObj.GetOutput())
        match = re.search(r'0x[0-9a-fA-F]+', res)
        if match:
            pc = match.group(0)
    
    if(int(pc, 16) == int(HYP_GUNY_ENTRY_VECTOR_ADDR, 16)):
        fileName ='hypvm.elf'
        hyp_elf_path = g_dbgContext.openocdConfig.getBinaryPath ('hyp',fileName)
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
        # disabling secure wdog if it is enabled
        # res = dapAccessGDB( debugger, command, result, "script", sec_wdog)
        # res = int(res, 16)
        # sec_wdog_disable = int("0x0", 16)
        # if ( res != sec_wdog_disable):
        #     cmd = "dap_access -w {} -v 0x0".format(sec_wdog)

        # getting the ASLR_BASE using non secure el2
        catch_exc_on_cmd = f"{g_dbgContext.monitorPrefix} {g_dbgContext.chipsetName.upper()}.apss.core3 catch_exc nsec_el2"
        debugger.HandleCommand(catch_exc_on_cmd)
        debugger.HandleCommand("c")
        res = get_pc_address_type(debugger, command, exe_ctx, result, commandInterpreter, returnObj)
        if res == True:
            cmd = "register read pc"
            commandInterpreter.HandleCommand(cmd, returnObj)
            if returnObj.Succeeded():
                res = str(returnObj.GetOutput())
                match = re.search(r'0x[0-9a-fA-F]+', res)
                if match:
                    pc = match.group(0)
        pc = int(pc, 16)  # Convert hex string to integer
        kaslr = (pc & (~0x1fffff))
        kaslr = hex(kaslr)
        cmd = "target modules load --file {} --slide {}".format(hyp_elf_path, kaslr)
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
    catch_exc_off_cmd = catch_exc_on_cmd.replace("nsec_el2","off")
    debugger.HandleCommand(catch_exc_off_cmd)
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