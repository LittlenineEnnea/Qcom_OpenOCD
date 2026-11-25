#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file telnet.py
#
# @section Description
# This file contains methods for starting a Telnet connection with a handle 
# and closing the connection. It also includes method for secure memory read 
# and write operations.

import telnetlib
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.dap_access import dap_access_cmd_list

def sendTelnetCommand(command):
    """!   
    This function is used to examine subsystems before attach.

    """
    tln = getTelnetHandle()

    command = f"{command}\n" 
    logger.info(f"command to be sent : {command}")
    tln.write(command.encode('ascii'))
    result = tln.read_until(b'<', 2).decode('ascii')
    logger.info("{}".format(result))
    closeTelnetHandle()

def getTelnetHandle():
    """!   
    This function is used to start a telnet connection with a global 
    handle. 

    @return Initiates a telnet connection and returns a global handle
    to the telnet connection.
    """
    if (g_dbgContext.telnetHandle != None):
        g_dbgContext.telnetHandle.close()
    g_dbgContext.telnetHandle = telnetlib.Telnet(g_dbgContext.getGdbRemoteHost(), 4444)
    return g_dbgContext.telnetHandle

def closeTelnetHandle():
    """!   
    This function is used to smoothly close a telnet connection with the same 
    global handle that was used to start the connection.

    """
    if (g_dbgContext.telnetHandle != None):
        g_dbgContext.telnetHandle.close()
    g_dbgContext.telnetHandle = None

def dapAccessTelnet(debugger, command,  result, tln = None, scriptRun = None, regAddress = None, 
                 num_of_words = None, write_reg = None, data = None):
    """!   
    This function to implsment dap_access command, that is used to access secure 
    memory regions for reads and writes via gdb.

    Usage : 
        dump    :  dap_access -r <address> -n <units> -s telnet
        read    :  dap_access -r <address> -s telnet
        write   :  dap_access -w <address> -v <value> -s telnet

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
    @param command          -   String containing all the input arguments
                                for the used command. 
    @param result           -   A return object which encapsulates 
                                success/failure information for the command
                                and output text that needs to be printed 
                                as a result of the command.

    @param scriptRun        -   a variable used to denote whether the command
                                is being used from inside the script or being
                                used from lldb (by the user).
    @param regAddress       -   Address of the read register that we want to 
                                read 
                                the value from.
    @param  num_of_words    -   Number of units of content/value that we want
                                to read from the given read register address.
    @param  write_reg       -   Address of the write register that we want to 
                                write the given value to.
    @param  data            -   Value that we want to write to the specified
                                write register address.


    """

    # this section is used for reading a single address - working

    if (write_reg == None and regAddress):
        if (num_of_words == None):
            num_of_words = 1
        for j in range(num_of_words):
            current_address = regAddress + 4 * j  # assuming 32-bit registers
            cmd_list = dap_access_cmd_list(current_address, None, 'read')
            for i in range(len(cmd_list)):
                tln.write(cmd_list[i].encode('ascii') + b"\n")

            result = tln.read_until(b'<', 2).decode('ascii')

            # Parse the value from the response
            startIndex = result.find("0C")  # adjust if needed based on actual response format
            endIndex = result.rfind(">")
            value = result[startIndex+3 : endIndex].strip()

            # Parse the actual value from the response
            lines = result.splitlines()
            value = None
            for line in lines:
                if line.startswith("0x"):
                    value = line
                    break
            # if not scriptRun:
            print("0x{:08X}: {}".format(current_address, value))

            if (num_of_words == 1):
                return value

    # this section is used writing to an address
    if (write_reg):

        cmd_list = dap_access_cmd_list(write_reg, data,  'write')

        for i in range(0, len(cmd_list)):
            cmd = "{}\n".format(cmd_list[i])
            tln.write(str(cmd).encode('ascii'))
            tln.read_until(b"> ")