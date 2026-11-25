#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file soccp.py
#
# @section Description
# Script for SOCCP image debugging.
#

import os
import time
import lldb
import traceback
from utils.source_map import setSourceMap
from utils.telnet_client import TelnetClient
from utils.debug_context import g_dbgContext, logger
from lldb_pkg.target.maili.image.xbl import debugXBL

# Constants
SLEEP_2_MS = 0.02   # 2 ms


def find_symbol_addr(target: lldb.SBTarget, symbol_name):
    """
    This function is used to get the starting address from elf.

    @param target       -   An instance of the created target.

    @param symbol_name  -   The name of the symbol whose
                            address is to be retrieved.
    """
    # Iterate all modules in the target
    for m in target.module_iter():
        sc_list = m.FindSymbols(symbol_name)  # SBSymbolContextList
        size = sc_list.GetSize()
        for i in range(size):
            sc = sc_list.GetContextAtIndex(i)    # <-- CORRECT: get SBSymbolContext
            sym = sc.GetSymbol()                 # <-- SBSymbol
            if sym.IsValid() and sym.GetName() == symbol_name:
                addr = sym.GetStartAddress()     # SBAddress
                return {
                    "name": sym.GetName(),
                    "module": addr.GetModule().GetFileSpec().GetFilename(),
                    "file_address": addr.GetFileAddress(),           # link-time/ELF address
                    "load_address": addr.GetLoadAddress(target),     # -1 if sections not loaded
                }
    return None


def debugSOCCP(debugger: lldb.SBDebugger, command, exe_ctx, result, inputarg=None, step_arg=None):
    """!
    This function defines a command to debug SOCCP by loading the specified
    .elf file and setting the program counter (PC) to __start.

    Usage:
        Step 1: debug -d soccp -s 1
        Step 2: debug -d soccp -s 2

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
    @param step_arg         -   Step number to debug: 1 or 2.
    """

    # Parse command arguments: "-d soccp -s 1"
    try:
        args = command.split()
        if "-s" not in args:
            logger.error("Missing '-s' flag for step number")
            logger.error("Usage: -d soccp -s 1  OR  -d soccp -s 2")
            return

        step_index = args.index("-s")
        if step_index + 1 >= len(args):
            logger.error("Missing step number after '-s' flag")
            return

        step = int(args[step_index + 1])
    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse command arguments: {e}")
        return

    subsys: str = "soccp"
    chip_name = g_dbgContext.chipsetName.lower()
    soccp_gdb_remote_connection = f"connect_remote -s {subsys}"

    if step == 1:
        dmcontrol_reg = 0
        dmcontrol_data_1 = 0
        dmcontrol_data_2 = 0
        soccp_dbg_clocks = []

        # Extract chip info
        for chip in g_dbgContext.chipInfo.findall("chipset"):
            if chip.get("name").upper() == chip_name.upper():
                riscv_config = chip.find("riscv_config")
                if riscv_config is None:
                    logger.error("Missing riscv_config configuration for SOCCP")
                    return

                soccp_config = riscv_config.find("soccp_config")
                if soccp_config is None:
                    logger.error("Missing soccp_config configuration for SOCCP")
                    return

                soccp_debug_base = soccp_config.find("soccp_debug_base")
                if soccp_debug_base is None:
                    logger.error("Missing soccp_debug_base configuration for SOCCP")
                    return

                if soccp_debug_base.attrib is None:
                    logger.error("Missing SOCCP base address in configuration")
                    return

                soccp_base_addr = soccp_debug_base.attrib.get("addr")
                if not soccp_base_addr:
                    logger.error("Missing 'addr' attribute in soccp_debug_base configuration")
                    return

                try:
                    dmcontrol_elem = soccp_config.find("dmcontrol")
                    if dmcontrol_elem is None:
                        logger.error("Missing dmcontrol configuration")
                        return

                    dmcontrol_offset_addr = dmcontrol_elem.attrib.get("offset_addr")
                    dmcontrol_data_1_str = dmcontrol_elem.attrib.get("dmcontrol_data_1")
                    dmcontrol_data_2_str = dmcontrol_elem.attrib.get("dmcontrol_data_2")

                    if not all([dmcontrol_offset_addr, dmcontrol_data_1_str, dmcontrol_data_2_str]):
                        logger.error("Incomplete dmcontrol configuration")
                        return

                    dmcontrol_data_1 = int(dmcontrol_data_1_str, 16)
                    dmcontrol_data_2 = int(dmcontrol_data_2_str, 16)
                except (KeyError, ValueError) as e:
                    logger.error(f"Failed to parse SOCCP config: {e}")
                    logger.error(traceback.format_exc())
                    return

                dmcontrol_reg = int(soccp_base_addr, 16) + int(dmcontrol_offset_addr, 16)

                clocks_elem = soccp_config.find("soccp_dbg_clocks")
                if clocks_elem is None:
                    logger.error("Missing soccp_dbg_clocks configuration for SOCCP")
                    return
                try:
                    for clock_elem in clocks_elem.findall("clock"):
                        soccp_dbg_clocks.append((
                            int(clock_elem.attrib["addr"], 16),
                            int(clock_elem.attrib["val"], 16),
                        ))
                except (KeyError, ValueError) as e:
                    logger.error(f"Failed to parse soccp_dbg_clocks config: {e}")
                    logger.error(traceback.format_exc())
                    return
                if not soccp_dbg_clocks:
                    logger.error("soccp_dbg_clocks configuration has no <clock> entries")
                    return
                break

        # Start XBL debug to reach qsee_jump_func
        debugXBL(debugger, command, exe_ctx, result, "run")

        # Enable SOCCP debug/APB/ATB/trigger/timestamp clocks before dmcontrol,
        # matching run_fbc.cmm's SOCCP block.
        for clock_addr, clock_val in soccp_dbg_clocks:
            debugger.HandleCommand(f"{g_dbgContext.monitorPrefix} DAP.cxaxi mww\
                {hex(clock_addr)} {hex(clock_val)}")
            time.sleep(SLEEP_2_MS)

        debugger.HandleCommand(f"{g_dbgContext.monitorPrefix} DAP.cxapb mww\
            {hex(dmcontrol_reg)} {hex(dmcontrol_data_1)}")
        time.sleep(SLEEP_2_MS)
        debugger.HandleCommand(f"{g_dbgContext.monitorPrefix} DAP.cxapb mww\
            {hex(dmcontrol_reg)} {hex(dmcontrol_data_2)}")

        debugger.HandleCommand("br del")
        debugger.HandleCommand("br s -n qsee_jump_func")
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")

        # LLDB settings
        debugger.HandleCommand("settings set auto-confirm on")
        debugger.HandleCommand("settings set plugin.process.gdb-remote.packet-timeout 10000000")

        # Examine SOCCP
        debugger.HandleCommand(f"{g_dbgContext.monitorPrefix}\
            {chip_name.upper()}.soccp arp_examine")

        logger.info("---- SOCCP examined; ready for STEP 2 ----")

    elif step == 2:
        build_id = f"{chip_name}.{subsys}.test"
        primary_elf = f"{subsys.upper()}_IMG_{build_id}.elf"
        build_root = g_dbgContext.openocdConfig.getImageBasePath(subsys)
        primary_elf_path = os.path.join(build_root, f"{subsys}_proc",
            "build", "ms", primary_elf)

        # Load ELF
        try:
            if os.path.exists(primary_elf_path):
                target = debugger.CreateTarget(primary_elf_path)
                logger.info(f"Successfully loaded ELF: {primary_elf_path}")

                # Get __start from ELF (no remote connection required)
                start_info = find_symbol_addr(target, "__start")
                if not start_info:
                    logger.error(f"Symbol __start not found in ELF: {primary_elf_path}")
                    return

                __start_addr = start_info["file_address"]
                __start_load_addr = start_info["load_address"]  # may be -1 before remote connect

                if __start_load_addr is not None and __start_load_addr != -1:
                    logger.info(f"__start in {start_info['module']}: "
                                f"file=0x{__start_addr:x}")
                else:
                    logger.info(f"__start in {start_info['module']}: "
                                f"file=0x{__start_addr:x}, load=N/A (sections not loaded)")

                with TelnetClient() as telnet:
                    telnet.send_command(f"targets {chip_name.upper()}.{subsys}")
                    telnet.send_command(f"reg pc {hex(__start_addr)}")
            else:
                logger.error(f"{primary_elf} - File not found.")
                return
        except (RuntimeError, ValueError) as e:
            logger.error(f"Failed to create target for ELF: {primary_elf_path}")
            logger.error(f"Error type: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            return
        except Exception as e:
            logger.error(f"Unexpected error creating target: {primary_elf_path}")
            logger.error(f"Error type: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            return

        # Set Source code path
        try:
            source_path = g_dbgContext.sourcePath[subsys]
            if source_path is not None and os.path.exists(source_path):
                setSourceMap(debugger, subsys, source_path)
                logger.info(f"Source mapping set for {subsys}: {source_path}")
            else:
                logger.error(f"{source_path} - No such file or directory")
        except Exception as e:
            logger.warning(f"Unable to set the source code map; using default\
                        behavior. Error: {type(e).__name__}: {e}")

        # Connect GDB remote
        try:
            logger.info("Establishing gdb connection to SOCCP target...")
            debugger.HandleCommand(soccp_gdb_remote_connection)
            logger.info("GDB remote connection established successfully.")
            logger.info("---- SOCCP entry point reached ----")
        except Exception as e:
            logger.error("Unable to establish GDB connection.")
            logger.error(traceback.format_exc())

    else:
        logger.error(f"Invalid step: {step}. Expected 1 or 2")
        return
