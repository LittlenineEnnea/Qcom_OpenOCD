#
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
# @file reload_osam.py
#
# @section Description
# This file contains the implementation of the reload command 
import lldb
import sys
import importlib
from logger import logger

def reload(debugger, command, results, internal_dict):
    """
    Reloads or imports a specified Python module in LLDB.

    This function takes a module name as input and attempts to reload the module if it is already imported.
    If the module is not imported, it attempts to import the module. It logs the result of the operation.

    Parameters:
    debugger (lldb.SBDebugger): The LLDB debugger instance.
    command (str): The command string provided to the LLDB command interpreter.
    results (lldb.SBCommandReturnObject): The command return object used to send results back to the LLDB command interpreter.
    internal_dict (dict): An internal dictionary used by LLDB for command execution.

    """
    module_name = command.strip().lower()
    if not module_name:
        logger.info("[ReloadScript] Error: No module name provided.")
        return
    try:
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
            logger.info(f"[ReloadScript] Reloaded module: {module_name}")
        else:
            importlib.import_module(module_name)
            logger.info(f"[ReloadScript] Imported module: {module_name}")
    except Exception as e:
        logger.info(f"[ReloadScript] Failed to reload/import module '{module_name}': {e}")

def __lldb_init_module(debugger, internal_dict):
    """
    Initializes the LLDB command script for reloading modules.

    This function registers the 'ReloadScript' command with LLDB, allowing users to reload or import Python modules
    using the command 'ReloadScript <module_name>'.

    Parameters:
    debugger (lldb.SBDebugger): The LLDB debugger instance.
    internal_dict (dict): An internal dictionary used by LLDB for command execution.

    """
    debugger.HandleCommand('command script add -f reload_osam.reload ReloadScript')
    logger.info("[ReloadScript] Command 'ReloadScript' installed. Usage: ReloadScript <module_name>")
