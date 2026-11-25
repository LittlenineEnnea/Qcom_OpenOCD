#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file cpucp.py
#
# @section Description
# Script for CPUCP image debugging.
#

import os
import time
import lldb
from utils.telnet_client import TelnetClient
from utils.debug_context import g_dbgContext, logger
from lldb_pkg.target.maili.image.xbl import debugXBL
from lldb_pkg.target.maili.target_utils.pbl_load_fbc import (
    axi_write32, is_target_examined, is_target_suspended
)


def get_cpucp_wakeup_req_addr(chip_name: str):
    """!
    @brief Look up the CPUCP wake-up request register address for the
    active chipset from chip_config.xml.

    @param chip_name  Chip name to match against chip_config.xml's
                       <chipset name="..."> attribute (case-insensitive).

    @return The address as an int, or None if the active chipset has no
            such configuration.
    """
    for chip in g_dbgContext.chipInfo.findall("chipset"):
        if chip.get("name", "").lower() != chip_name.lower():
            continue

        riscv_config = chip.find("riscv_config")
        if riscv_config is None:
            return None

        cpucp_config = riscv_config.find("cpucp_config")
        if cpucp_config is None:
            return None

        wakeup_req_elem = cpucp_config.find("cpucp_wakeup_req")
        if wakeup_req_elem is None:
            return None

        return int(wakeup_req_elem.attrib["addr"], 16)

    return None


def debugCPUCP(debugger: lldb.SBDebugger, command, exe_ctx, result, inputarg=None):
    """!
    This function defines a command to debug CPUCP by loading the specified
    .elf file and connecting to CPUCP over gdb-remote.

    Usage:  debug -d cpucp

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

    subsys: str = "cpucp"
    chip_name = g_dbgContext.chipsetName.lower()
    cpucp_target_name = f"{chip_name.upper()}.cpucp"
    cpucp_gdb_remote_connection = f"connect_remote -s {subsys}"

    debugger.HandleCommand("settings set plugin.process.gdb-remote.packet-timeout 10000000")
    debugger.HandleCommand("settings set auto-confirm on")

    # ResetCore(debugger)

    debugXBL(debugger, command, exe_ctx, result, "run")

    debugger.HandleCommand("br del")
    debugger.HandleCommand("br s -n qsee_jump_func")
    debugger.HandleCommand("c")
    debugger.HandleCommand("br del")

    wakeup_req_addr = get_cpucp_wakeup_req_addr(chip_name)
    if wakeup_req_addr is None:
        logger.error("Missing cpucp_wakeup_req configuration for CPUCP")
        return

    try:
        with TelnetClient() as telnet:
            # Wake up CPUCP once APSS/DDR bring-up has completed, matching
            # load_cpucp_from_apps.cmm's silicon-only wakeup write.
            axi_write32(telnet, wakeup_req_addr, 0x1)

            # pbl_load_fbc() ends by shutting OpenOCD down for a supervised
            # relaunch, which only re-examines APSS -- CPUCP itself comes
            # back either unexamined or suspended and needs to be brought
            # up explicitly before a gdb-remote connect can succeed.
            if is_target_examined(telnet, cpucp_target_name):
                if is_target_suspended(telnet, cpucp_target_name):
                    telnet.send_command(f"{cpucp_target_name} riscv resume_debug")
            else:
                telnet.send_command(f"MAILI.cpucp riscv dmi_write 0x40 0x1")
                time.sleep(0.01)
                telnet.send_command(f"MAILI.cpucp riscv dmi_write 0x40 0x9")
                time.sleep(0.01)
                telnet.send_command(f"MAILI.cpucp riscv dmi_write 0x40 0x80000009")
                time.sleep(0.01)
                telnet.send_command(f"{cpucp_target_name} arp_examine")
                time.sleep(0.01)

            time.sleep(1)
    except Exception as e:
        logger.error(f"Failed to wake up / examine CPUCP: {e}")
        return

    # Get ELF path from configuration
    cpucp_elf_path = g_dbgContext.openocdConfig.getBinaryPath("cpucp", "cpucp.elf")
    cpucp_elf_path = r"C:\tools\cpucp.elf"
    if not os.path.exists(cpucp_elf_path):
        logger.error(f"ELF not found: {cpucp_elf_path}")
        return

    # Load ELF for symbols only -- CPUCP's own firmware is already resident
    # via the normal boot chain, this just maps debug symbols onto it.
    g_dbgContext.tgt = debugger.CreateTarget(cpucp_elf_path)
    if g_dbgContext.tgt is None:
        logger.error(f"Failed to create target for: {cpucp_elf_path}")
        return

    # Connect GDB remote
    try:
        logger.info("Establishing gdb connection to CPUCP target...")
        debugger.HandleCommand(cpucp_gdb_remote_connection)

        cpucp_target: lldb.SBTarget = debugger.GetSelectedTarget()
        if cpucp_target is None or not cpucp_target.IsValid():
            logger.error(f"Failed to connect to CPUCP target via: {cpucp_gdb_remote_connection}")
            return

        cpucp_process = cpucp_target.GetProcess()
        if cpucp_process is None or not cpucp_process.IsValid():
            logger.error("Failed to get CPUCP process")
            return

        logger.info("GDB remote connection established successfully.")
        logger.info("---- CPUCP entry point reached ----")
    except Exception as e:
        logger.error(f"Unable to establish GDB connection: {e}")
        return

