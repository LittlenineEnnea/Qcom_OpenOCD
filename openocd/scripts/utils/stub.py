#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file stub.py
#
# @section Description
# This file contains a script for stubbing a function.

import sys
import lldb

from utils.debug_context import logger


def find_symbol_address(target, func_name):
    """!
    This function is used to find a symbol from target.
    @param target : current active target
    @param func_name : function name
    """
    symbol_context_list = target.FindSymbols(func_name)
    if symbol_context_list.GetSize() == 0:
        logger.error("Symbol {} not found".format(func_name))
        sys.exit(1)
    return symbol_context_list


def stub_func(debugger, command, exe_ctx, func_name, ret_val):
    """!
    This function is used to stub a funtion.
    @param func_name : function name
    @param ret_val : function return value <optional>
    """
    target = debugger.GetSelectedTarget()
    immdiate_val = ret_val
    machine_code = 0xD2800000  # machine code of 'mov x0, 0' assembly instruction
    opcode = machine_code
    Rd = 0  # Destination register x0
    res_status = 1
    shift = 5

    if target and target.IsValid():
        symbol_context_list = find_symbol_address(target, func_name)
        process = target.GetProcess()
    else:
        logger.error("Taget not created. Please start debug and retry this command")
        sys.exit(1)

    addr = symbol_context_list[0].symbol.GetValue()

    if immdiate_val != None:
        # Creating machine code for the assembly instruction 'mov x0, <immediate_val>', where the immediate value is other than zero.
        # machine_code = opcode | immediate_val<<shift | Rd
        machine_code = opcode | (immdiate_val << shift) | Rd

    if process.IsValid():
        try:
            cmd1 = "memory write -s 4 -f x {} {}".format(addr, hex(machine_code))
            res1 = lldb.SBCommandReturnObject()
            debugger.GetCommandInterpreter().HandleCommand(cmd1, res1)

            cmd2 = "memory write -s 4 -f x {} 0xD65F03C0".format(addr + 4)  # 0xD65F03C0 is machine code of 'ret'
            res2 = lldb.SBCommandReturnObject()
            debugger.GetCommandInterpreter().HandleCommand(cmd2, res2)

            if res_status != res1.GetStatus() or res_status != res2.GetStatus():
                raise Exception("Failed to execute Memory write command")
            else:
                logger.info("Stubbing successful")
        except Exception as e:
            logger.error("Stubbing not successful, {}.".format(e))
    else:
        logger.error("Command requires a current process")
