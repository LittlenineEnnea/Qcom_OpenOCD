#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file std_reset.py
#
# @section Description
# This file contains a script to perform standard hard reset.

import time
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.source_map import setSourceMap
from utils.telnet import *
from utils.reset import ResetCore
from utils.hwio import g_hwioConfig
import importlib


def import_reset_modules(chip_name):
    """
    This function imports the SPMI and PMIC reset modules dynamically
    based on the chip name.

    @param chip_name        -   The name of the chip for which the modules
                                need to be imported.This should be a string
                                representing the chip name
                                (e.g., 'bonito', 'milos').

    @return                 -   Returns the spmi_config and pmic_reset
                                functions from the imported modules.
    """
    # Construct the module names
    spmi_cfg_module_name = f"lldb_pkg.target.{chip_name}.pmic.spmi.spmi_cfg"
    pmic_reset_module_name = f"lldb_pkg.target.{chip_name}.pmic.pmic_reset"

    # Import the modules dynamically
    spmi_cfg_module = importlib.import_module(spmi_cfg_module_name)
    pmic_reset_module = importlib.import_module(pmic_reset_module_name)

    # Extract the required functions
    spmi_config = getattr(spmi_cfg_module, 'spmi_config')
    pmic_reset = getattr(pmic_reset_module, 'pmic_reset')

    return spmi_config, pmic_reset

if (g_dbgContext.chipsetName.lower() != None):
    chip_name = g_dbgContext.chipsetName.lower()
    spmi_config, pmic_reset = import_reset_modules(chip_name)

def std_reset(debugger, command, exe_ctx, result):    
    """!

    This function is the main standard reset function that performs reset in 2 parts.
    First part is spmi_config, second is pmic_reset.

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
    @param command          -   String containing all the input arguments
                                for the used command. 
    @param result           -   A return object which encapsulates 
                                success/failure information for the command
                                and output text that needs to be printed 
                                as a result of the command.
    @param inputFile        -   Path to register dump cmm 

    """

    logger.info("PERFORMING STANDARD RESET")   
    try :
        tln = getTelnetHandle()
    except :
        logger.error (" Unable to establish telnet connection with openocd."
        "OpenOCD server should have been started prior to issuing this command. " )

    chipset = g_dbgContext.chipsetName.upper()
    if chipset == "MAVROS":
        core = "apss0"
    elif chipset == "MAILI" or chipset == "HAWI":
        core = "apss3"
    else:
        core = "apss"

    connect_cmd = "connect_remote -s {}".format(core)
    debugger.HandleCommand (connect_cmd)
    
    en_reset_cmd = "process plugin packet monitor reset_config trst_only"
    debugger.HandleCommand (en_reset_cmd)

    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname = chip.get('name')
        if (chipname.upper() == g_dbgContext.chipsetName.upper()):

            logger.info (" --- WAKING UP APPS CORE 0 ...")
            chip_config_writes(debugger,command, result, tln, 'wake_cpu0' , False)

    if (g_dbgContext.chipsetName.upper() == 'CLARENCE' or g_dbgContext.chipsetName.upper() == 'POROS'):
        pass
    else:
        spmi_config(debugger, command, exe_ctx, result)
    pmic_reset(debugger, command, exe_ctx, result)

def chip_config_writes(debugger,command, result, tln, configuration, hwio = False ):
    """!
    This function writes the specified configuration to the chip using
     telnet connection.

    @param debugger         - An instance of the SBDebugger Object that
                            provides access to SBTargets used for debugging.
     
    @param command          - String containing all the input arguments for 
                            the used command.

    @param result           - A return object which encapsulates 
                            success/failure information for the command and 
                            output text that needs to be printed as a result
                            of the command.
    @param telnet              - The telnet handle.
    @param configuration    - The configuration to write.
    @param hwio             - Flag indicating whether to use HWIO.

    """
    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname = chip.get('name')
        if (chipname.upper() == g_dbgContext.chipsetName.upper()):
            element = chip.find(configuration)
            for pair in element.iter('write'):
                
                if (hwio):
                    reg = str(pair.attrib['reg'])
                    reg = int(g_hwioConfig.hwioRegisterAddr(debugger, command, \
                        result, reg),16)
                else :
                    reg = int(pair.attrib['reg'],16)

                value = int(pair.attrib['value'],16)
                try :
                    logger.info ("Reg : {}\t value : {}".format(hex(reg),hex(value)))

                    dapAccessTelnet(debugger,command, result, tln, True, None, None,\
                         hex(reg), hex(value))
                except :
                    logger.error (" Lost telnet connection, retrying")
                    tln = g_dbgContext.telnetHandle
                    dapAccessTelnet(debugger,command, result, tln, True, None, None, \
                        hex(reg), hex(value))
