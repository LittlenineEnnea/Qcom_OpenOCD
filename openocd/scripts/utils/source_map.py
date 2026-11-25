#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file source_map.py
#
# @section Description
# This file contains a script to map source path with the path provided in 
# <image>_SRC_PATH variable.

import lldb
from utils.debug_context import g_dbgContext
from utils.debug_context import logger

def setSourceMap(debugger , image = None, path = None):
    """!   
    This function is used to map source path with the path provided in source-path command.

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
    @param image            -   The name of the image
    @param path             -   The source path where codebase for debug is available
                                and is used for mapping with disassembly

    """
    Chipset_name = g_dbgContext.chipsetName.lower()
    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname=chip.get('name')
        if (chipname.upper() == Chipset_name.upper()):
            sourceConfig = chip.find('source_config')
            image = sourceConfig.find(str(image))
            symbolName = image.attrib["func"]
            endString = image.attrib["end"]

    returnObj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()
    cmd = "list {}".format(symbolName)
    commandInterpreter.HandleCommand( cmd, returnObj )
    result = str(returnObj.GetOutput())
    startIndex = result.find("File: ")
    endIndex = result.find(str(endString))
    localPath = result [startIndex+len("File: ") : endIndex]
    mappingCommand = "settings set target.source-map {} {}".format(localPath, str(path))
    debugger.HandleCommand(mappingCommand)
    logger.info("MAPPED COMMAND : {}".format(mappingCommand))
    return