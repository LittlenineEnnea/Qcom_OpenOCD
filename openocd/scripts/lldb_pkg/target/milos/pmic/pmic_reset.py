#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file pmic_reset.py
#
# @section Description
# This script contains functions to perform a PMIC hard reset operation
# specific to Milos target.

import time
import re
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.telnet import *
from lldb_pkg.target.milos.pmic.pmic_io_config import g_pmic_config


def pmic_reset(debugger, command, exe_ctx, result):    
    """
    Performs a PMIC hard reset operation.

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
    spmi_owner_channel = 3  # LPASS Channel by default
    # BusAccessMode  = 'dap_access'
    #  chip id is "", so it is choosing slave_id by default
    slave_id = 0x0
    # cmd code for hard reset
    reset_cmd = 0x7
    bus_id = 0
    
    g_pmic_config.pmic_io(debugger, command, exe_ctx, result, "write", "DAP", spmi_owner_channel ,\
         bus_id, slave_id, 0x857, 0x0)
    time.sleep(1)
    g_pmic_config.pmic_io(debugger, command, exe_ctx, result, "write", "DAP", spmi_owner_channel , \
        bus_id, slave_id, 0x856, reset_cmd)
    time.sleep(1)
    g_pmic_config.pmic_io(debugger, command, exe_ctx, result, "write", "DAP", spmi_owner_channel ,\
         bus_id, slave_id, 0x815, 0x20)
    time.sleep(1)
    g_pmic_config.pmic_io(debugger, command, exe_ctx, result, "write", "DAP", spmi_owner_channel ,\
         bus_id, slave_id, 0x857, 0xA5)
    time.sleep(1)

