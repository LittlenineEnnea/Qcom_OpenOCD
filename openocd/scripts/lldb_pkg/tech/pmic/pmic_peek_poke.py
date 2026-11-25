#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file pmic_peek_poke.py
#
# @section Description
# This script contains functions to perform read and write operation on pmic 
# registers.

import sys
import shlex
import importlib

from utils.debug_context import logger
from utils.debug_context import g_dbgContext

NO_OF_BUS = 2
NO_OF_PMICS = 14

def import_pmic_config_modules(chip_name):
    """
    This function imports the PMIC configuration modules dynamically
    based on the chip name.

    @param chip_name        -   The name of the chip for which the modules
                                need to be imported.This should be a string
                                representing the chip name
                                (e.g., 'bonito', 'milos').

    @return                 -   Returns the pmic_io_configuration
                                functions from the imported modules.
    """
    # Construct the module names
    pmic_config_module_name = f"lldb_pkg.tech.pmic.{chip_name}.pmic_io_config"

    # Import the modules dynamically
    pmic_config_module = importlib.import_module(pmic_config_module_name)

    # Extract the required functions
    g_pmic_config = getattr(pmic_config_module, "g_pmic_config")

    return g_pmic_config


if g_dbgContext.chipsetName.lower() != None:
    chip_name = g_dbgContext.chipsetName.lower()
    g_pmic_config = import_pmic_config_modules(chip_name)


def pmic_peek_poke(debugger, command, exe_ctx, result):
    """
    Performs a PMIC PeeK Poke operation.

    @param debugger         - An instance of the SBDebugger Object that
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command.
    @param exe_ctx          - exe_ctx: The execution context, providing access
                              to the current target, process, thread, and frame.

    @param result           - A return object which encapsulates
                              success/failure information for the command
                              and output text that needs to be printed
                              as a result of the command.
    """

    spmi_owner_channel = g_pmic_config.spmi_owner_channel
    slave_id = 0x0
    bus_id = 0
    data = 0x00

    # format
    # pmic_peek_poke <flag> <bus_id> <slave_id> <address> <data>
    args = shlex.split(command)

    if not (len(args) == 5 or (len(args) == 4 and args[0] == "-r")):
        logger.error("Required arguments not provided")
        logger.info('Please enter "help pmic_peek_poke" for command format')
        sys.exit(1)

    if args[0] == "-r":
        flag = "read"
    elif args[0] == "-w":
        flag = "write"
    else:
        return "please use '-w' for write and '-r' for read"

    # bus_id parsing
    if args[1].find("0x") != -1:
        bus_id = int(args[1], 16)
    else:
        bus_id = int(args[1])
    
    if bus_id >= NO_OF_BUS:
        logger.info(" Please enter valide bus ID")
        return

    # slave_id parsing
    if args[2].find("0x") != -1:
        slave_id = int(args[2], 16)
    else:
        slave_id = int(args[2])

    if slave_id >= NO_OF_PMICS:
        logger.info(" Please enter valide Slave ID")
        return

    # register address parsing
    if args[3].find("0x") != -1:
        address = int(args[3], 16)
    else:
        address = int(args[3])

    # Write Data parsing
    if args[0] == "-r":
        data = 0
    elif args[0] == "-w":
        if args[4].find("0x") != -1:
            data = int(args[4], 16)
        else:
            data = int(args[4])

    g_pmic_config.pmic_io(debugger, command, exe_ctx, result, flag, "DAP",
                          spmi_owner_channel, bus_id, slave_id, address, data)

    if args[0] == "-r":
        print("Data read: {}".format(g_pmic_config.read_data))
