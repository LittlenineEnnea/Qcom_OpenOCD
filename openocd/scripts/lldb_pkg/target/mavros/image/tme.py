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
import sys
import time
import lldb
from utils.reset import ResetCore
from utils.source_map import setSourceMap
from utils.stop_wait import wait_for_breakpoint_hit
from utils.debug_context import g_dbgContext, logger
from lldb_pkg.target.mavros.target_utils.pbl_load_fbc import pbl_load_fbc

def debugTME(debugger: lldb.SBDebugger, command, exe_ctx, result, inputarg=None, tme_img=None):

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
    apss_gdb_remote_connection = "connect_remote -s apss0"

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
            apss_pbl_1st_break_point = get_element_attr("apss_pbl_1st_break_point", "addr")
            apss_pbl_2nd_break_point = get_element_attr("apss_pbl_2nd_break_point", "addr")
            tme_qtmr_qtmr_v1_cntp_ctl_f0_addr = get_element_attr("tme_qtmr_qtmr_v1_cntp_ctl_f0", "addr")
            tme_qtmr_qtmr_v1_cntp_ctl_f0_val = get_element_attr("tme_qtmr_qtmr_v1_cntp_ctl_f0", "val")
            tme_mrom_entry_point = get_element_attr("tme_mrom_entry_point", "addr")
            tme_fw_entry_point = get_element_attr("tme_fw_entry_point", "addr")

            # Validate all required attributes were extracted
            required_attrs = [
                apss_pbl_2nd_break_point, apss_pbl_1st_break_point,
                tme_qtmr_qtmr_v1_cntp_ctl_f0_addr, tme_qtmr_qtmr_v1_cntp_ctl_f0_val,
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
        ResetCore(debugger)
        if not pbl_load_fbc():
            logger.error(" --- FBC: APSS Core0 bring-up failed.")
            sys.exit(1)

        debugger.HandleCommand(apss_gdb_remote_connection)

        apss_target: lldb.SBTarget = debugger.GetSelectedTarget()
        if apss_target is None:
            logger.error("Failed to get APSS target")
            return

        apss_process = apss_target.GetProcess()
        if apss_process is None:
            logger.error("Failed to get APSS process")
            return

        # Set breakpoint at apss_pbl_1st_break_point
        brk_pnt = apss_target.BreakpointCreateByAddress(int(apss_pbl_1st_break_point, 16))
        if not brk_pnt.IsValid():
            logger.error(f"Failed to create breakpoint at {apss_pbl_1st_break_point}")
            return
        brk_pnt.SetOneShot(True)

        apss_process.Continue()
        if not wait_for_breakpoint_hit(apss_process):
            logger.error("Timeout waiting for apss_pbl_1st_break_point")
            return

        debugger.HandleCommand(
            f"{g_dbgContext.monitorPrefix} DAP.cxapb mww {dmcontrol_reg} {dmcontrol_data_1}"
        )
        time.sleep(0.01)

        debugger.HandleCommand(
            f"{g_dbgContext.monitorPrefix} DAP.cxapb mww {dmcontrol_reg} {dmcontrol_data_2}"
        )
        time.sleep(0.01)

        # Set breakpoint at apss_pbl_2nd_break_point
        brk_pnt = apss_target.BreakpointCreateByAddress(int(apss_pbl_2nd_break_point, 16))
        if not brk_pnt.IsValid():
            logger.error(f"Failed to create breakpoint at {apss_pbl_2nd_break_point}")
            return
        brk_pnt.SetOneShot(True)

        apss_process.Continue()
        if not wait_for_breakpoint_hit(apss_process):
            logger.error("Timeout waiting for apss_pbl_2nd_break_point")
            return

        debugger.HandleCommand(f"{g_dbgContext.monitorPrefix} {chip_name.upper()}.{subsys} arp_examine")
        debugger.HandleCommand(f"{g_dbgContext.monitorPrefix} {chip_name.upper()}.{subsys} write_memory {tme_qtmr_qtmr_v1_cntp_ctl_f0_addr} 32 {tme_qtmr_qtmr_v1_cntp_ctl_f0_val}")

        logger.info("---- TME Debug STEP 1 complete, proceed with STEP 2 ----")

    elif step == 2:
        if tme_image == "tme_fmw":
            tme_elf_name = "firmware.elf"
            entry_point_addr = int(tme_fw_entry_point, 16)
        elif tme_image == "mrom":
            tme_elf_name = f"tme{chip_name.capitalize()}MissionRom.elf"
            entry_point_addr = int(tme_mrom_entry_point, 16)
        else:
            logger.error(f"Invalid tme_image: {tme_image}. Expected 'tme_fmw' or 'mrom'")
            return

        # Get ELF path from configuration
        tme_elf_path = g_dbgContext.openocdConfig.getBinaryPath(
            "tme", tme_elf_name
        )

        if not os.path.exists(tme_elf_path):
            logger.error(f"ELF not found: {tme_elf_path}")
            return

        # LOAD ELF
        tme_target: lldb.SBTarget = debugger.CreateTarget(tme_elf_path)
        if tme_target is None:
            logger.error(f"Failed to create target for: {tme_elf_path}")
            return

        # Set source path
        try:
            source_path = g_dbgContext.sourcePath.get(subsys)
            if source_path and os.path.exists(source_path):
                setSourceMap(debugger, subsys, source_path)
        except Exception as e:
            logger.warning(f"Source mapping failed: {e}")

        debugger.HandleCommand(tme_gdb_remote_connection)

        tme_target = debugger.GetSelectedTarget()
        if tme_target is None or not tme_target.IsValid():
            logger.error(f"Failed to connect to TME target via: {tme_gdb_remote_connection}")
            return

        tme_process = tme_target.GetProcess()
        if tme_process is None or not tme_process.IsValid():
            logger.error("Failed to get TME process")
            return

        # Create breakpoint at entry point
        brk_pnt = tme_target.BreakpointCreateByAddress(entry_point_addr)
        if not brk_pnt.IsValid():
            logger.error(f"Failed to create breakpoint at address: {hex(entry_point_addr)}")
            return
        brk_pnt.SetOneShot(True)

        logger.info(f"-------- TME entry point has been reached for image: {tme_elf_name} --------")
    else:
        logger.error(f"Invalid step: {step}. Expected 1 or 2")
        return
