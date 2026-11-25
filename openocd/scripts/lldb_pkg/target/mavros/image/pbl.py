#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file pbl.py
#
# @section Description
# This file contains a script for PBL image debugging.

import os
import sys
import time
import lldb
import shlex
from utils.reset import ResetCore
from utils.telnet_client import TelnetClient
from utils.debug_context import logger, g_dbgContext
from lldb_pkg.target.mavros.target_utils.qreset import axi_ap_init
from lldb_pkg.target.mavros.target_utils.pbl_load_fbc import axi_write32
from lldb_pkg.target.mavros.target_utils.pbl_load_fbc import pbl_load_fbc

def get_tcsr_magic_cookie(chip_name: str):
    """!
    @brief Look up the TCSR magic-cookie register (addr/val) written during
    the PBL workaround, for the active chipset, from chip_config.xml.

    @param chip_name  Chip name to match against chip_config.xml's
                       <chipset name="..."> attribute (case-insensitive).

    @return (addr, val) tuple of ints, or None if the active chipset has
            no such configuration.
    """
    for chip in g_dbgContext.chipInfo.findall("chipset"):
        if chip.get("name", "").lower() != chip_name.lower():
            continue

        pbl_apss_config = chip.find("pbl_apss_config")
        if pbl_apss_config is None:
            return None

        cookie_elem = pbl_apss_config.find("tcsr_magic_cookie")
        if cookie_elem is None:
            return None

        return int(cookie_elem.attrib["addr"], 16), int(cookie_elem.attrib["val"], 16)

    return None


def pbl_workaround(debugger):
    """!
    @brief Reset APSS Core0 through the AXI-AP debug path so it halts at
    PBL entry, then wait for FBC (First Boot Chain) load to complete.

    @param debugger         -   An instance of the SBDebugger Object that
                                provides access to SBTargets used for
                                debugging.
    """
    chip_name = g_dbgContext.chipsetName.lower()

    cookie = get_tcsr_magic_cookie(chip_name)
    if cookie is None:
        logger.error("pbl_workaround: missing tcsr_magic_cookie configuration")
        sys.exit(1)
    tcsr_magic_cookie_addr, tcsr_magic_cookie_val = cookie

    with TelnetClient() as telnet:
        axi_ap_init(telnet, g_dbgContext.chipsetName.upper())
        axi_write32(telnet, tcsr_magic_cookie_addr, tcsr_magic_cookie_val)
        time.sleep(1)

        ResetCore(debugger)
        time.sleep(1)

        if not pbl_load_fbc():
            logger.error(" --- FBC: APSS Core0 bring-up failed.")
            sys.exit(1)


def debugPBL(debugger: lldb.SBDebugger, command, exe_ctx, result, \
    inputarg = None, Module = None, path = None):
    """!   
    This function is used to define a command to apps and perform a warm boot 
    to stop at the entry of PBL 
                                              
    Usage : debug -d pbl

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

    connect_cmd = "connect_remote -s apss0"

    if inputarg == None:
        logger.info (" --- Preparing for Debug of PBL  Image ---")

    debugger.HandleCommand("settings set plugin.process.gdb-remote.packet-timeout \
                           10000000")
    debugger.HandleCommand("platform select remote-linux")
    debugger.HandleCommand("settings set auto-confirm on")  
    debugger.HandleCommand("settings set target.default-arch aarch64")
    args = shlex.split(command)
    if len(args) < 3:
        pass
    else:
        path = args[2]
        if os.path.exists(path):
            g_dbgContext.tgt = debugger.CreateTarget(path)
        else:
            logger.error(f"Unable to find {path} file")
            sys.exit(1)

    pbl_workaround(debugger)
    
    logger.info("Trying to connect to target")
    debugger.HandleCommand(connect_cmd)
    logger.info("Connected to target")
    debugger.HandleCommand("register write x8 0x0")
   
