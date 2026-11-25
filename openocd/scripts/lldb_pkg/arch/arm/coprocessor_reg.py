#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file coprocessor_reg.py
#
# @section Description
# This file contains script for reading and writing to coprocessor register 
# values.

import lldb
from utils.debug_context import logger
from utils.debug_context import g_dbgContext

def registerCategory(debugger, command, exe_ctx, result):
    Registers = g_dbgContext.regInfo.findall('Register')
    count = 0
    for Register in Registers:
        name = Register.get('category')
        name = name.upper()
        g_dbgContext.category.append(name)
        count = count + 1
        print(str(count)+ "." +name)

def categoryList(debugger, command, exe_ctx, result, inputarg):
    Registers = g_dbgContext.regInfo.findall('Register')
    returnObj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()
    for Register in Registers:
        name = Register.get('category')
        name = name.upper()
        user_choice = inputarg
        if (name == g_dbgContext.category[user_choice - 1]):
            subsystems = Register.findall ('register')
            for i in range(0, len(subsystems)):
                for k,v in subsystems[i].attrib.items():
                    if k == "reg":
                        reg = v
                        cmd = "{} aarch64 mrs {}".format(g_dbgContext.monitorPrefix, reg)
                        commandInterpreter.HandleCommand( cmd, returnObj )
                        if returnObj.Succeeded():
                            res = str(returnObj.GetOutput())
                            if 'mrs' in res:
                                continue
                            if 'DSCR.ERR' in res:
                                continue
                            r = res.find("response:")
                            res = res[(r+10):]
                            res = bytearray.fromhex(res).decode()
                            res = res.replace("\n","")
                        if reg in res:
                            r = res.find(":")
                            for i in range(r, 16):
                                res = res.replace(":", " :")
                            print(res)

def registerRead(debugger, command, exe_ctx, result, reg, script_run=None):
    returnObj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()
    cmd = "{} aarch64 mrs {}".format(g_dbgContext.monitorPrefix, reg)
    commandInterpreter.HandleCommand( cmd, returnObj )
    if returnObj.Succeeded():
        res = str(returnObj.GetOutput())
        if 'mrs' in res:
            logger.error (" Coprocessor register read failed")
        if 'DSCR.ERR' in res:
            logger.error (" Exception level mismatch")
        else:
            r = res.find("response:")
            res = res[(r+10):]
            res = bytearray.fromhex(res).decode()
            res = res.replace("\n","")
            if script_run == None:
                print(res)
            else:
                return res

def registerWrite(debugger, command, exe_ctx, result, reg, data):
    returnObj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()
    cmd = "{} aarch64 msr {} {}".format(g_dbgContext.monitorPrefix, reg, str(hex(data)))
    commandInterpreter.HandleCommand( cmd, returnObj )
    if (not(returnObj.Succeeded())):
        logger.error (" write failed")

