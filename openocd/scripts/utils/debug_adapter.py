#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file debug_adapter.py
#
# @section Description
# This file contains a script for getting debug adapter information.

from utils.debug_context import logger
from utils.debug_context import g_dbgContext


def dbg_adapter(debugger, command, exe_ctx):
    
    debug_adapters = ["eud", "jlink", "olimex"]
    debug_protocols = ["jtag", "swd"]        
    
    args = command.split()
    
    # Check -d argument is present or not
    try:
        index = args.index("-d")
    except:
        index = None
        pass
    
    # To select appropriate debug adapter and protocol
    if index == None:
        g_dbgContext.debug_protocol = None
        g_dbgContext.debug_adapter = None
    else:
        g_dbgContext.debug_adapter = (args[index + 1]).lower()
        if g_dbgContext.debug_adapter in debug_adapters:
            pass
        else:
            logger.error(f"Provided debug adapter \"{g_dbgContext.debug_adapter.upper()}\" is not valid select any one of them: {debug_adapters}")
            return False
        try:
            g_dbgContext.debug_protocol = (args[index + 2]).lower()
            if g_dbgContext.debug_protocol in debug_protocols:
                pass
            else:
                logger.error(f"Provided protocol \"{g_dbgContext.debug_protocol.upper()}\" is not valid select either {debug_protocols[0]} or {debug_protocols[1]}")
                return False
        except:
            g_dbgContext.debug_protocol = None
    
    #Default adapter selected as EUD
    if g_dbgContext.debug_adapter == None:
        g_dbgContext.debug_adapter = "eud"
        logger.info("Debug adapter not selected defaulting to EUD")
    else:
        logger.info(f"The selected debug adapter is {g_dbgContext.debug_adapter.upper()}")
    
    #Default protocol selected as SWD
    if (g_dbgContext.debug_protocol == None):
        if g_dbgContext.debug_adapter != "olimex":
            g_dbgContext.debug_protocol = "swd"
            logger.info("Debug protocol not selected defaulting to SWD")
        else:
            g_dbgContext.debug_protocol = "jtag"
            logger.info("Debug protocol not selected defaulting to JTAG")
    else:
        logger.info(f"The selected debug protocol is {g_dbgContext.debug_protocol.upper()}")
    
    #Error if EUD is selected with JTAG protocol
    if (g_dbgContext.debug_adapter == "eud" and g_dbgContext.debug_protocol == "jtag"):
        logger.error("JTAG is not supported on EUD, please use SWD")
        return False

    return True
