#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file swm.py
#
# @section Description
# Script for SWM image debugging.
#

import os
import time
from utils.debug_context import g_dbgContext, logger
from utils.source_map import setSourceMap
from utils.telnet_client import TelnetClient

def debugSWM(debugger, command, exe_ctx, result, inputarg=None):
    """!
    This function defines a command to debug SWM by loading the specified
    .elf file and setting the program counter (PC) to VECTOR CATCH .
    
    Usage:  debug -d swm

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
    """
    
    subsys = "lpai"
    subsys_type = "swm"
    lpass_wm_swm_cc_swm_cc_debug_proc_cbcr_addr = None
    lpass_wm_swm_cc_swm_cc_debug_proc_cbcr_val = None
    lpass_wm_wm_cpu_addr = None
    lpass_wm_wm_cpu_val = None
    chip_found = False
    dap_ap_num = None
    dap_control_status = None
    swm_elf_name = f"LPAI_SWM_IMG_aspen.swm.prod.elf"
        
    chip_name = g_dbgContext.chipsetName.lower()
    subsys = "lpai"
    swm_elf_path = g_dbgContext.openocdConfig.getBinaryPath(subsys, swm_elf_name)
    
    swm_gdb_remote_connection = f"connect_remote -s {subsys_type}"
    
    for chip in g_dbgContext.chipInfo.findall("chipset"):
        if chip.get("name").upper() == chip_name.upper():
            cortex_m_config = chip.find("cortex_m")
            if cortex_m_config is None:
                logger.error(f"Missing cortex_m_config configuration for {subsys_type.upper()}")
                return
            
            cortex_m55_config = cortex_m_config.find("cortex_m55")
            if cortex_m55_config is None:
                logger.error(f"Missing cortex_m55_config configuration for {subsys_type.upper()}")
                return
            
            swm_config = cortex_m55_config.find("swm_proc")
            if swm_config is None:
                logger.error(f"Missing swm_config configuration for {subsys_type.upper()}")
                return        
            
            debug_proc_elem = swm_config.find("lpass_wm_swm_cc_swm_cc_debug_proc_cbcr")
            if debug_proc_elem is None:
                logger.error(f"Missing lpass_wm_swm_cc_swm_cc_debug_proc_cbcr configuration for {subsys_type.upper()}")
                return

            lpass_wm_swm_cc_swm_cc_debug_proc_cbcr_addr = debug_proc_elem.get("reg")
            if not lpass_wm_swm_cc_swm_cc_debug_proc_cbcr_addr:
                logger.error("Missing 'reg' attribute in debug_proc configuration")
                return
            
            lpass_wm_swm_cc_swm_cc_debug_proc_cbcr_val = debug_proc_elem.get("value")
            
            swm_cpu_elem = swm_config.find("lpass_wm_wm_cpu")
            if swm_cpu_elem is None:
                logger.error(f"Missing lpass_wm_wm_cpu configuration for {subsys_type.upper()}")
                return
            
            lpass_wm_wm_cpu_addr = swm_cpu_elem.get("reg")
            if not lpass_wm_wm_cpu_addr:
                logger.error("Missing 'reg' attribute in lpass_wm_wm_cpu configuration")
                return

            lpass_wm_wm_cpu_val = swm_cpu_elem.get("value")
            if not lpass_wm_wm_cpu_val:
                logger.error("Missing 'value' attribute in lpass_wm_wm_cpu configuration")
                return

            dap_ap_num_elem = swm_config.find("dap_ap_num")
            if dap_ap_num_elem is None:
                logger.error(f"Missing dap_ap_num configuration for {subsys_type.upper()}")
                return
            
            dap_ap_num = dap_ap_num_elem.get("ap_num")
            if not dap_ap_num:
                logger.error("Missing 'ap_num' attribute in dap_ap_num configuration")
                return
                        
            dap_control_status_elem = swm_config.find("dap_control_status")
            if dap_control_status_elem is None:
                logger.error(f"Missing dap_control_status configuration for {subsys_type.upper()}")
                return
           
            dap_control_status = dap_control_status_elem.get("csw")
            if not dap_control_status:
                logger.error("Missing 'csw' attribute in dap_control_status configuration")
                return
            
            chip_found = True
            break

    if not chip_found:
        logger.error(f"Chipset configuration not found for {chip_name}")
        return

    dap_register_init_write = (f"dap_reg_write {lpass_wm_swm_cc_swm_cc_debug_proc_cbcr_addr} "
                                f"{lpass_wm_swm_cc_swm_cc_debug_proc_cbcr_val}")
    
    select_dap_ap_18 = f"{chip_name.upper()}.dap apsel {dap_ap_num}"
    configure_dap_control_status = f"{chip_name.upper()}.dap apcsw {dap_control_status}"
    examine_swm_arp = f"{chip_name.upper()}.{subsys_type} arp_examine"
    halt_core = f"{chip_name.upper()}.{subsys_type} arp_halt"
    axi_register_init_write = f"axi_reg_write {lpass_wm_wm_cpu_addr} {lpass_wm_wm_cpu_val}"

    try:
        with TelnetClient() as telnet:
            try:
                telnet.send_command(dap_register_init_write)
                telnet.send_command(select_dap_ap_18)
                telnet.send_command(configure_dap_control_status)
                telnet.send_command(examine_swm_arp)
                telnet.send_command(halt_core)
                time.sleep(0.1)
                telnet.send_command(axi_register_init_write)
            except Exception as e:
                logger.error(f"Error executing command : {e}")
                return
    except Exception as e:
        logger.error(f"Error: {e}")
        return
    
    time.sleep(0.1)
    
    # Load ELF
    try:
        if os.path.exists(swm_elf_path):
            target = debugger.CreateTarget(swm_elf_path)
        else:
            logger.error(f"{swm_elf_path} - File not found.")
            return
    except Exception as e:
        logger.error(f"Failed to create target: {e}")
        return

    # Set Source code path
    try:
        source_path = g_dbgContext.sourcePath.get(subsys_type)
        if source_path is not None and os.path.exists(source_path):
            setSourceMap(debugger, subsys_type, source_path)
            logger.info(f"Source mapping set for swm: {source_path}")
        else:
            logger.error(f"{source_path} - No such file or directory")
    except Exception as e:
        logger.warning(
            f"Unable to set the source code map; using default behavior. "
            f"Error: {type(e).__name__}: {e}"
        )

    time.sleep(0.1)

    # Connect GDB remote
    try:
        logger.info("Establishing gdb connection to SWM target...")
        debugger.HandleCommand(swm_gdb_remote_connection)
        logger.info("GDB remote connection established successfully.")
        logger.info(f"---- {subsys_type.upper()} entry point reached ----")
    except Exception as e:
        logger.error("Unable to establish GDB connection.")

