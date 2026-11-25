#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file openocd.py
#
# @section Description
# This file contains script for starting OpenOCD in a new python window.

import platform
import os
import shlex
import subprocess
import sys
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.debug_adapter import dbg_adapter



def runOpenocd(debugger, command, exe_ctx, result, chipset, inputarg = None):
    """! 
    Implementation of the openocd command that uses meta information and 
    recognises chipset name. This command starts openocd in a new 
    python window, with the identified chipset configuration.

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
        
    """

    osName = platform.system()
    currentDir = os.getcwd()       
    subsystem = inputarg
    ChipsetName = chipset
    
    # Get the adapter details
    if dbg_adapter(debugger, command, exe_ctx) == False:
        return # Wrong parameters are passed

    debug_interface = "{}_{}.cfg".format(g_dbgContext.debug_adapter, g_dbgContext.debug_protocol)

    if (ChipsetName == None):
        meta_dir = g_dbgContext.openocdConfig.getMetaPath()
        ChipsetName = g_dbgContext.chipsetName.lower()
        logger.info("Starting OpenOCD for chipset : %s", ChipsetName.upper())
    else:
        logger.info("Starting OpenOCD for chipset : %s", ChipsetName.upper())
        g_dbgContext.chipsetName = ChipsetName

    if (osName == 'Windows'):
        OPENOCD_PATH = g_dbgContext.openocdDir.replace("\\bin","")
    elif (osName == 'Linux'):
        OPENOCD_PATH = g_dbgContext.openocdDir.replace("/bin","")
    elif (osName == 'Darwin'):
        OPENOCD_PATH = os.path.dirname(g_dbgContext.openocdDir.rstrip('/'))

    if ((g_dbgContext.userMeta == 1) or (ChipsetName != None)):
        openocd_config_dir = "{0}/scripts/lldb_pkg/target/{1}/{1}.cfg".format(OPENOCD_PATH, ChipsetName.lower())
        if (str(subsystem).upper() == 'Q6'):
            openocd_config_dir = "{0}/scripts/lldb_pkg/target/{1}/{1}_hex.cfg".format(OPENOCD_PATH, ChipsetName.lower())
    else:
        openocd_config_dir = "{0}/common/Core/openocd/scripts/lldb_pkg/target/{1}/{1}.cfg".format(meta_dir, ChipsetName.lower())
        if ((os.path.exists(openocd_config_dir)) == False):
            openocd_config_dir = "{0}/scripts/lldb_pkg/target/{1}/{1}.cfg".format(OPENOCD_PATH, ChipsetName.lower())

    run_file_path = "{}/utils/run.py".format(currentDir)

    if (osName == 'Windows'):
        cmd = "shell start python {} {} {} {}".format(run_file_path, openocd_config_dir, g_dbgContext.chipsetName.lower(), debug_interface)
    elif (osName == 'Linux'):
        cmd="script os.popen('/usr/bin/x-terminal-emulator -e bash -c \"python {} {} {} {};exec bash\"')".format(run_file_path, openocd_config_dir, g_dbgContext.chipsetName.lower(), debug_interface)
    elif (osName == 'Darwin'):
        # Open a new Terminal.app window running run.py, like the Windows/Linux
        # branches do. The new window does not inherit our environment, so
        # pass OPENOCD_DIR explicitly.
        shell_cmd = "export OPENOCD_DIR={}; python3 {} {} {} {}".format(
            shlex.quote(g_dbgContext.openocdDir), shlex.quote(run_file_path),
            shlex.quote(openocd_config_dir), shlex.quote(g_dbgContext.chipsetName.lower()),
            shlex.quote(debug_interface))
        applescript_cmd = shell_cmd.replace('\\', '\\\\').replace('"', '\\"')
        subprocess.Popen(["osascript",
                          "-e", 'tell application "Terminal" to do script "{}"'.format(applescript_cmd),
                          "-e", 'tell application "Terminal" to activate'])
        return

    debugger.HandleCommand(cmd)