#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file tme.py
#
# @section Description
# Script for TME image debugging.
#

import os
import time
import lldb
from utils.debug_context import g_dbgContext, logger
from utils.source_map import setSourceMap


def wait_for_breakpoint_hit(process, timeout_sec=5.0):
    """!
    Wait for a breakpoint to be hit on the given process within a specified timeout.
    
    This function polls the process state at regular intervals and checks if the
    process has stopped due to a breakpoint being hit. If a breakpoint is detected,
    the function returns immediately. If the timeout is exceeded without detecting
    a breakpoint, the function returns False.
    
    @param process      The LLDB process object to monitor for breakpoint hits.
    @param timeout_sec  The maximum time (in seconds) to wait for a breakpoint.
                        Default is 5.0 seconds.
    
    @return True if a breakpoint was hit within the timeout period, False otherwise.
    
    @note This function blocks until either a breakpoint is hit or the timeout
          is reached. It checks the process state every 10 millisecond.
    """
    start = time.time()

    while time.time() - start < timeout_sec:
        state = process.GetState()
        
        # Handle terminal states
        if state in (lldb.eStateExited, lldb.eStateCrashed, lldb.eStateDetached):
            return False
            
        if state == lldb.eStateStopped:
            thread = process.GetSelectedThread()
            if thread is not None and thread.GetStopReason() == lldb.eStopReasonBreakpoint:
                return True
        
        time.sleep(0.01)

    return False


def debugTME(debugger, command, exe_ctx, result, inputarg=None, tme_img=None):
    
    """!
    This function defines a command to debug TME by loading the specified .elf.
    
    Usage:  
        Step 1: debug -d tme -s 1
        Step 2: debug -d tme -i <image_type> -s 2

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
    @param tme_img          -   Image type to debug: 'tme_fmw' or 'mrom'.
    """
    
    # Parse command arguments: "-d tme -s 1" or "-d tme -i <image> -s 2"
    try:
        args = command.split()
        if len(args) < 4:
            logger.error(f"Insufficient arguments: {args}")
            logger.error("Usage: -d tme -s 1  OR  -d tme -i <image> -s 2")
            return
        
        # Verify "-d" and "tme" flags
        if args[0] != "-d" or args[1] != "tme":
            logger.error("Expected: -d tme ... ")
            return
        
        # Parse step number
        if "-s" not in args:
            logger.error("Missing '-s' flag for step number")
            return
        
        step_index = args.index("-s")
        if step_index + 1 >= len(args):
            logger.error("Missing step number after '-s' flag")
            return
        
        step = int(args[step_index + 1])
        
        # Parse image type for step 2
        tme_image = tme_img
        if step == 2 and "-i" in args:
            image_index = args.index("-i")
            if image_index + 1 < step_index:
                tme_image = args[image_index + 1]
        
    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse command arguments: {e}")
        return

    subsys = "tme"
    chip_name = g_dbgContext.chipsetName.lower()

    tme_gdb_remote_connection = f"connect_remote -s {subsys}"
    apss_gdb_remote_connection = "connect_remote -s apss"


    for chip in g_dbgContext.chipInfo.findall("chipset"):
        if chip.get("name", "").lower() != chip_name:
            continue
               
        sleep_config = chip.find('sleep_config')
        if sleep_config is None:
            logger.error("Missing sleep_config configuration")
            return
        
        sleep_elm = sleep_config.find('pmic_sleep')
        if sleep_elm is None:
            logger.error("Missing pmic_sleep configuration")
            return
         
        pmic_sleep = int(sleep_elm.attrib['sleep_time'])
                
        riscv_config = chip.find("riscv_config")
        tme_config = riscv_config.find("tme_config") if riscv_config else None
        if tme_config is None:
            logger.error("Missing TME configuration")
            return
        try:
            # Extract base address
            tme_debug_base_elem = tme_config.find("tme_debug_base")
            if tme_debug_base_elem is None:
                logger.error("Missing tme_debug_base element")
                return
            tme_base_addr = int(tme_debug_base_elem.attrib["addr"], 16)

            # Extract dmcontrol configuration
            dmcontrol_elem = tme_config.find("dmcontrol")
            if dmcontrol_elem is None:
                logger.error("Missing dmcontrol element")
                return
            dmcontrol_reg = tme_base_addr + int(dmcontrol_elem.attrib["offset_addr"], 16)
            dmcontrol_data_1 = int(dmcontrol_elem.attrib["dmcontrol_data_1"], 16)
            dmcontrol_data_2 = int(dmcontrol_elem.attrib["dmcontrol_data_2"], 16)

            # Helper function to safely extract address from element
            def get_element_attr(elem_name, attr_name):
                """Extract attribute from TME config element."""
                elem = tme_config.find(elem_name)
                if elem is None:
                    logger.error(f"Missing {elem_name} element")
                    return None
                return elem.attrib.get(attr_name)

            # Extract configuration attributes using helper function
            tme_seq_init_post_ram_load_apps = get_element_attr("tme_seq_init_post_ram_load_apps", "addr")
            pbl_auth_hash_seg = get_element_attr("pbl_auth_hash_seg", "addr")
            tme_qtmr_qtmr_v1_cntp_ctl_f0_addr = get_element_attr("tme_qtmr_qtmr_v1_cntp_ctl_f0", "addr")
            tme_qtmr_qtmr_v1_cntp_ctl_f0_val = get_element_attr("tme_qtmr_qtmr_v1_cntp_ctl_f0", "val")
            pause_point_reg_addr = get_element_attr("pause_point_reg", "addr")
            pause_point_reg_val = get_element_attr("pause_point_reg", "val")
            tme_mrom_entry_point = get_element_attr("tme_mrom_entry_point", "addr")
            tme_fw_entry_point = get_element_attr("tme_fw_entry_point", "addr")

            # Validate all required attributes were extracted
            required_attrs = [
                tme_seq_init_post_ram_load_apps, pbl_auth_hash_seg,
                tme_qtmr_qtmr_v1_cntp_ctl_f0_addr, tme_qtmr_qtmr_v1_cntp_ctl_f0_val,
                pause_point_reg_addr, pause_point_reg_val,
                tme_mrom_entry_point, tme_fw_entry_point
            ]
            if any(attr is None for attr in required_attrs):
                logger.error("Failed to extract all required TME configuration attributes")
                return

            break
        except (KeyError, ValueError, AttributeError) as e:
            logger.error(f"Failed to parse TME configuration: {e}")
            return
    
    if step == 1:
        debugger.HandleCommand(apss_gdb_remote_connection)
        
        if g_dbgContext.debug_adapter == "eud":
            if pmic_sleep:
                # Perform pmic hard reset. This will reset device
                debugger.HandleCommand ("pmic_hardreset")
                time.sleep(int(pmic_sleep))

            debugger.HandleCommand(apss_gdb_remote_connection)
        
        debugger.HandleCommand("qreset")
        debugger.HandleCommand("br del")
        
        debugger.HandleCommand(
            f"dap_access -w {pause_point_reg_addr} -v {pause_point_reg_val}"
        )

        apss_target = debugger.GetSelectedTarget()
        if apss_target is None:
            logger.error("Failed to get APSS target")
            return
        
        apss_process = apss_target.GetProcess()
        if apss_process is None:
            logger.error("Failed to get APSS process")
            return
        
        # Set breakpoint at tme_seq_init_post_ram_load_apps
        bp = apss_target.BreakpointCreateByAddress(
            int(tme_seq_init_post_ram_load_apps, 16)
        )
        if bp is None:
            logger.error(f"Failed to create breakpoint at {tme_seq_init_post_ram_load_apps}")
            return
        
        bp.SetOneShot(True)
        apss_process.Continue()
        if not wait_for_breakpoint_hit(apss_process):
            logger.error("Timeout waiting for tme_seq_init_post_ram_load_apps")
            return
        
        debugger.HandleCommand(
            f"{g_dbgContext.monitorPrefix} DAP.cxapb_tme mww {dmcontrol_reg} {dmcontrol_data_1}"
        )
        time.sleep(0.01)

        debugger.HandleCommand(
            f"{g_dbgContext.monitorPrefix} DAP.cxapb_tme mww {dmcontrol_reg} {dmcontrol_data_2}"
        )
        time.sleep(0.1)

        # Set breakpoint at pbl_auth_hash_seg
        bp = apss_target.BreakpointCreateByAddress(int(pbl_auth_hash_seg, 16))
        if bp is None:
            logger.error(f"Failed to create breakpoint at {pbl_auth_hash_seg}")
            return
        bp.SetOneShot(True)
        apss_process.Continue()
        if not wait_for_breakpoint_hit(apss_process):
            logger.error("Timeout waiting for pbl_auth_hash_seg")
            return

        debugger.HandleCommand(
            f"{g_dbgContext.monitorPrefix} {chip_name.upper()}.{subsys} arp_examine"
        )
        
        time.sleep(0.01)
        debugger.HandleCommand(
            f"dap_access -w {tme_qtmr_qtmr_v1_cntp_ctl_f0_addr} -v {tme_qtmr_qtmr_v1_cntp_ctl_f0_val}"
        )
        
        debugger.HandleCommand(
            f"dap_access -r {tme_qtmr_qtmr_v1_cntp_ctl_f0_addr}"
        )

    elif step == 2:
        if tme_image == "tme_fmw":
            tme_elf_name = "firmware.elf"
        elif tme_image == "mrom":
            tme_elf_name = f"tme{chip_name.capitalize()}MissionRom.elf"
        else:
            logger.error(f"Invalid tme_image: {tme_image}. Expected 'tme_fmw' or 'mrom'")
            return

        # Get ELF path from configuration
        tme_elf_path = g_dbgContext.openocdConfig.getBinaryPath(
            "boot", tme_elf_name
        )
        
        if not os.path.exists(tme_elf_path):
            logger.error(f"ELF not found: {tme_elf_path}")
            return

        # LOAD ELF
        tme_target = debugger.CreateTarget(tme_elf_path)
        if tme_target is None:
            logger.error(f"Failed to create target for: {tme_elf_path}")
            return
        
        tme_process = tme_target.GetProcess()
        if tme_process is None:
            logger.error("Failed to get TME process")
            return
        
        # Set source path
        try:
            source_path = g_dbgContext.sourcePath.get(subsys)
            if source_path and os.path.exists(source_path):
                setSourceMap(debugger, subsys, source_path)
        except Exception as e:
            logger.warning(f"Source mapping failed: {e}")

        debugger.HandleCommand(tme_gdb_remote_connection)

        # Create breakpoint at entry point
        try:
            if tme_image == "tme_fmw":
                entry_point_addr = int(tme_fw_entry_point, 16)
            elif tme_image == "mrom":
                entry_point_addr = int(tme_mrom_entry_point, 16)
            else:
                logger.error(f"Unknown tme_image: {tme_image}")
                return
            
            bp = tme_target.BreakpointCreateByAddress(entry_point_addr)
            if bp is None:
                logger.error(f"Failed to create breakpoint at address: {hex(entry_point_addr)}")
                return
            
            bp.SetOneShot(True)
        except ValueError as e:
            logger.error(f"Failed to parse entry point address: {e}")
            return

        # Continue TME
        tme_process.Continue()
        time.sleep(0.01)
        logger.info(f"-------- TME entry point has been reached for image: {tme_elf_name} --------")
    
    else:
        logger.error(f"Invalid step: {step}. Expected 1 or 2")
        return
