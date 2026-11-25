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
import traceback

from utils.source_map import setSourceMap
from lldb_pkg.target.netrani.image.xbl import debugXBL
from utils.debug_context import g_dbgContext, logger
from utils.dap_access import dapAccessGDB

# Constants
DEBUGGER_TIMEOUT = 10000000  # Timeout for GDB remote connection in milliseconds
DEFAULT_AOP_ELF_NAME = 'AOP_AAAAANAAO.elf'  # Default AOP ELF filename
CONNECTION_DELAY = 0.2  # Delay in seconds between connection operations
BREAKPOINT_DELAY = 1.0  # Delay after setting breakpoints

def get_aop_reg(xml_handle, reg):
    elem = xml_handle.find(reg)
    
    if elem is None:
        logger.error(f"{reg} reg is missing in chip_config.xml")
        return False
        
    reg_name = elem.attrib.get('reg')
    val = elem.attrib.get('value')
    
    if not reg_name or not val:
        logger.error(f"register or value missing in chip_config.xml for {reg}")
        return False
    
    return {reg_name: val}

def debugAOSS(debugger, command, exe_ctx, result, option=None, inputarg=None):
    """!   
    This function is used to define a command to debug aop. 
    In stage1, we do hard-reset and then do "pmic reset halt" to stop at
    PBL entry, set breakpoint at qsee_jump_func and say continue.
    AOP comes out of reset in between the device boot from PBL entry to
    the qsee_jump_func. write 0x1 to the reg "DEMCR_REG" to stop execution
    at aop entry point when we connect.
    In stage2, load AOP_AAAAANAAO.elf file and connect to AOP which got up
    in stage1(came out of reset)
    
    Usage : debug -d aoss

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
    @param option           -   Optional parameter for additional command options.
    @param inputarg         -   A variable used to denote whether the command
                                is being used from inside the script or being
                                used from lldb (by the user).

    @return                 -   None. Results are communicated through the result parameter
                                and through logger messages.
    """
    subsys: str = "aoss"
    aop_elf_name = DEFAULT_AOP_ELF_NAME
    apss_connect_cmd = "connect_remote -s apss"
    aoss_gdb_remote_connection = f"connect_remote -s {subsys}"
    chip_name = g_dbgContext.chipsetName.lower()
    architecture = None
    chip = None
   
    # Configure debugger settings
    debugger.HandleCommand(f"settings set plugin.process.gdb-remote.packet-timeout {DEBUGGER_TIMEOUT}")
    debugger.HandleCommand("platform select remote-linux")
    debugger.HandleCommand("settings set auto-confirm on")
    
    # Find chip configuration and architecture
    for chip_elem in g_dbgContext.chipInfo.findall('chipset'):
        if chip_elem.get('name').lower() == chip_name:
            chip = chip_elem
            arch_elem = chip.find("arch")
            if arch_elem is None:
                logger.error(f"Missing architecture configuration for chip {chip_name}")
                return False
                
            aoss_elem = arch_elem.find("aoss")
            if aoss_elem is None:
                logger.error(f"Missing AOSS architecture configuration for chip {chip_name}")
                return False
                
            architecture = aoss_elem.attrib.get("arch")
            if architecture is None:
                logger.error(f"Missing 'arch' attribute in AOSS configuration for chip {chip_name}")
                return False
            break
    
    if architecture is None:
        logger.error(f"Could not find chip configuration for {chip_name}")
        return False


    # Run XBL debugging
    debugXBL(debugger, command, exe_ctx, result, "run")
    debugger.HandleCommand("br del")

    if architecture == "cortex_m":
        try:
            # Examine AOSS for Cortex-M architecture
            cmd = f"{g_dbgContext.monitorPrefix} {g_dbgContext.chipsetName.upper()}.aoss arp_examine"
            debugger.HandleCommand(cmd)
        except Exception as e:
            logger.error(f"AOSS examine failed: {str(e)}")
            return False
        
        # Detach and reconnect
        debugger.HandleCommand("detach")
        debugger.HandleCommand(aoss_gdb_remote_connection)

        # Get DEMCR register configuration
        try:
            aop_config = chip.find("aop_config")
            if aop_config is None:
                logger.error("Missing aop_config configuration")
                return False
                
            demcr_elem = aop_config.find('DEMCR_REG')
            if demcr_elem is None:
                logger.error("Missing DEMCR_REG configuration")
                return False
                
            DEMCR_reg = demcr_elem.attrib.get('reg')
            DEMCR_val = demcr_elem.attrib.get('value')
            
            if not DEMCR_reg or not DEMCR_val:
                logger.error("Missing DEMCR register or value in configuration")
                return False
                
            # Write to DEMCR register
            cmd = f"memory write -f x -s 4 {DEMCR_reg} {DEMCR_val}"
            debugger.HandleCommand(cmd)
            debugger.HandleCommand("detach")
        except Exception as e:
            logger.error(f"Failed to configure Cortex-M: {str(e)}")
            logger.error(traceback.format_exc())
            return False
    
    elif architecture == "riscv":
        # Helper function to validate and get configuration element
        def get_config_element(parent, element_name, error_prefix=""):
            element = parent.find(element_name)
            if element is None:
                logger.error(f"{error_prefix}Missing {element_name} configuration")
                return None
            return element
            
        # Helper function to validate and get attribute
        def get_attribute(element, attr_name, error_prefix=""):
            if element.attrib is None or attr_name not in element.attrib:
                logger.error(f"{error_prefix}Missing {attr_name} attribute")
                return None
            return element.attrib.get(attr_name)
        
        # Get RISC-V configuration
        riscv_config = get_config_element(chip, "riscv_config", "RISC-V: ")
        if riscv_config is None:
            return False

        # Get AOSS configuration
        aoss_config = get_config_element(riscv_config, "aoss_config", "RISC-V: ")
        if aoss_config is None:
            return False
            
        # Get AOSS debug base configuration
        aoss_debug_base = get_config_element(aoss_config, "aoss_debug_base", "RISC-V: ")
        if aoss_debug_base is None:
            return False

        # Get AOSS base address
        aoss_base_addr = get_attribute(aoss_debug_base, "addr", "RISC-V: ")
        if aoss_base_addr is None:
            return False
                    
        try:
            # Get dmcontrol configuration
            dmcontrol_elem = get_config_element(aoss_config, "dmcontrol", "RISC-V: ")
            if dmcontrol_elem is None:
                return False
            
            # Get dmcontrol attributes
            dmcontrol_offset_addr = get_attribute(dmcontrol_elem, "offset_addr", "RISC-V: ")
            dmcontrol_data_1_str = get_attribute(dmcontrol_elem, "dmcontrol_data_1", "RISC-V: ")
            dmcontrol_data_2_str = get_attribute(dmcontrol_elem, "dmcontrol_data_2", "RISC-V: ")
            
            if not all([dmcontrol_offset_addr, dmcontrol_data_1_str, dmcontrol_data_2_str]):
                return False
            
            # Convert hex strings to integers
            dmcontrol_data_1 = int(dmcontrol_data_1_str, 16)
            dmcontrol_data_2 = int(dmcontrol_data_2_str, 16)
            
            # Calculate dmcontrol register address
            dmcontrol_reg = int(aoss_base_addr, 16) + int(dmcontrol_offset_addr, 16)
            
            # Write to dmcontrol register
            cmd1 = f"{g_dbgContext.monitorPrefix} DAP.cxapb_{subsys} mww \
                {hex(dmcontrol_reg)} {hex(dmcontrol_data_1)}"
            debugger.HandleCommand(cmd1)
            time.sleep(CONNECTION_DELAY)  # Wait for register write to take effect
            cmd2 = f"{g_dbgContext.monitorPrefix} DAP.cxapb_{subsys} mww \
                {hex(dmcontrol_reg)} {hex(dmcontrol_data_2)}"
            debugger.HandleCommand(cmd2)
                
        except (KeyError, ValueError) as e:
            logger.error(f"Failed to parse AOSS config: {e}")
            logger.error(traceback.format_exc())
            return False
    else:
        logger.error(f"{architecture} architecture is not supported")
        return False

    # Connect to APSS and set breakpoint
    try:
        debugger.HandleCommand(apss_connect_cmd)

        res = get_aop_reg(aop_config, "LOCKACCESS")
        lockaccess_addr = list(res.keys())[0]
        lockaccess_val = list(res.values())[0]
        
        res = get_aop_reg(aop_config, "SWCSYSPWRCTRL")
        swcsyspwrctrl_addr = list(res.keys())[0]
        swcsyspwrctrl_val = list(res.values())[0]

        res = get_aop_reg(aop_config, "SWDBGPWRCTRL")
        swdbgpwrctrl_addr = list(res.keys())[0]
        swdbgpwrctrl_val = list(res.values())[0]

        res = get_aop_reg(aop_config, "PDCPWRUPREQ")
        pdcpwrupreq_addr = list(res.keys())[0]
        pdcpwrupreq_val = list(res.values())[0]

        res = get_aop_reg(aop_config, "SWCSYSPWRCTRL")
        swcsyspwrctrl_addr = list(res.keys())[0]
        swcsyspwrctrl_val = list(res.values())[0]
        
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            lockaccess_addr, lockaccess_val)
        time.sleep(1)
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            swcsyspwrctrl_addr, swcsyspwrctrl_val)
        time.sleep(1)
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            swdbgpwrctrl_addr, swdbgpwrctrl_val)
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            pdcpwrupreq_addr, pdcpwrupreq_val)
        time.sleep(1)
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            swcsyspwrctrl_addr, swcsyspwrctrl_val)
        time.sleep(1)
        debugger.HandleCommand("br del")
        bkpt_cmd = 'br s -n qsee_jump_func --hardware'  
        debugger.HandleCommand(bkpt_cmd)
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")
        time.sleep(BREAKPOINT_DELAY)
        
        if architecture == "riscv":
            # Examine AOSS for RISCV subsystem
            debugger.HandleCommand(f"{g_dbgContext.monitorPrefix} \
                {chip_name.upper()}.{subsys} arp_examine")
            time.sleep(CONNECTION_DELAY)  # Wait for examine to complete

        # Detach from APPS GDB connection
        debugger.HandleCommand("detach")
    except Exception as e:
        logger.error(f"Failed during APSS connection and breakpoint setup: {str(e)}")
        logger.error(traceback.format_exc())
        return False

    # Get AOP ELF path and load it
    try:
        aop_elf_path = g_dbgContext.openocdConfig.getBinaryPath('aop', aop_elf_name)
        if not aop_elf_path:
            logger.error("Failed to find AOP binary path")
            return False
    except Exception as e:
        logger.error(f"Failed to find binary path: {str(e)}")
        return False
       
    # Create target from AOP ELF file
    try:
        if os.path.exists(aop_elf_path):
            target = debugger.CreateTarget(aop_elf_path)
            if not target:
                logger.error(f"Failed to create target for ELF: {aop_elf_path}")
                return False
            logger.info(f"Successfully loaded ELF: {aop_elf_path}")
        else:
            logger.error(f"{aop_elf_path} - File not found.")
            return False
    except (RuntimeError, ValueError) as e:
        logger.error(f"Failed to create target for ELF: {aop_elf_path}")
        logger.error(f"Error type: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        return False
    except Exception as e:
        logger.error(f"Unexpected error creating target: {aop_elf_path}")
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
        
        if architecture == "cortex_m":
            try:
                cmd = f"{g_dbgContext.monitorPrefix} gdb_breakpoint_override soft"
                debugger.HandleCommand(cmd)
                logger.info("Set breakpoint type as soft for Cortex-M")
            except Exception as e:
                logger.error(f"Failed to set breakpoint type as soft: {str(e)}")
                return False
        logger.info("---- AOP entry point reached ----")
        return True
    except Exception as e:
        logger.error(f"Unable to establish GDB connection: {str(e)}")
        logger.error(traceback.format_exc())
        return False