# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file command_register.py
#
# @section Description
# This file contains a script to register commands for a LLDB script

import inspect

# Dictionary to store registered commands
command_registry = {}

def register_command(name, func):
    """
    Registers a command with its associated function and arguments.

    Parameters:
    ----------
    name : str
        The name of the command to register.
    func : callable
        The function to associate with the command.

    Side Effects:
    -------------
    Adds the command to the global command_registry dictionary
    """
    sig = inspect.signature(func)
    arguments = list(sig.parameters.keys())
    command_registry[name.lower()] = {
        'func': func,
        'args': arguments
    }
