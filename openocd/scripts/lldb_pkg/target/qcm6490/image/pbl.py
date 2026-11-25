#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file pbl.py
#
# @section Description
# This file contains a script for PBL image debugging.

import time
import os
import sys
import shlex
from utils.debug_context import logger
from utils.debug_context import g_dbgContext
from utils.reset import ResetCore

def debugPBL(debugger, command, exe_ctx, result, inputarg = None, Module = None, path = None):
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

    connect_cmd = "connect_remote -s {}".format("apss")

    if (inputarg == None):
        logger.info (" --- Preparing for Debug of PBL  Image ---")
    debugger.HandleCommand("settings set plugin.process.gdb-remote.packet-timeout \
                           10000000")
    debugger.HandleCommand("platform select remote-linux")
    debugger.HandleCommand("settings set auto-confirm on")
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
    logger.info (" Trying to connect to target")
    debugger.HandleCommand (connect_cmd)
    logger.info (" Connected to target")
    time.sleep(0.5)
    ResetCore(debugger)
    debugger.HandleCommand ("stepi")
