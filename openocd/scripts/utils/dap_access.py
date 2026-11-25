#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file dap_access.py
#
# @section Description
# This script is used for performing read and write operations using DAP 
# Access over GDB.

import os
import time
import lldb
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.utilities import get_adi_version

def dap_access_gdb_read(cmd_intr: lldb.SBCommandInterpreter, result, scriptRun, addr):
    """!
    @brief Perform a dap access read of a single 32-bit word.

    @param cmd_intr     - command interpreter to run
                          command over lldb
    @param result       - command interpreter result
    @param scriptRun    - variable to track if function is
                          triggered from a script or commandline
    @param addr         - address to read

    @return The read value as a hex string, or None if the read failed.
    """
    if addr < 0:
        logger.error(f"dap_access_gdb_read: refusing negative address {hex(addr)}")
        return None

    cmd_list = dap_access_cmd_list(addr, None, 'read')

    for cmd in cmd_list:
        cmd_intr.HandleCommand("{} {}".format(g_dbgContext.monitorPrefix, cmd), result)
        if not result.Succeeded():
            logger.error(f"dap_access_gdb_read: command failed: {cmd} ({result.GetError()})")
            return None

    res = str(result.GetOutput())
    # LLDB's GDB-monitor reply framing prefixes the OpenOCD Tcl result with
    # this marker; the actual command output starts one character after it.
    response_marker = "response:"
    r = res.find(response_marker)
    res = res[r + len(response_marker) + 1 :]
    try:
        res = bytearray.fromhex(res).decode()
        res = res.replace("\n", "")
        if scriptRun is None:
            print(res)
        return res
    except Exception as e:
        if res.startswith("\n") and "error: UNIMPLEMENTED" in res:
            logger.info("OpenOCD Disconnected")
        else:
            logger.error(
                f"dap_access_gdb_read: failed to decode response for addr "
                f"{hex(addr)}: {e} (raw={res!r})"
            )
        return None


def dap_access_gdb_read_consecutive(
    cmd_intr: lldb.SBCommandInterpreter, result, scriptRun, addr, words_len):
    """!
    @brief Perform a dap access read of consecutive addresses.

    @param cmd_intr     -   command interpreter to run command over lldb
    @param result       -   command interpreter result
    @param scriptRun    -   Varible to check if this function is running
                            from script or command
    @param addr         -   address to read
    @param words_len    -   Number of consecutive registers to read
    """
    if not words_len or words_len < 0:
        return

    if addr < 0:
        logger.error(f"dap_access_gdb_read_consecutive: refusing negative address {hex(addr)}")
        return

    cmd_list = dap_access_cmd_list(addr, None, 'read')

    for cmd in cmd_list[:-1]:
        cmd = "{} {}".format(g_dbgContext.monitorPrefix, cmd)
        cmd_intr.HandleCommand(cmd, result)

    cmd = "{} {}".format(g_dbgContext.monitorPrefix, cmd_list[-1])

    for j in range(0, words_len):
        cmd_intr.HandleCommand(cmd, result)
        if result.Succeeded():
            res = str(result.GetOutput())
            # LLDB's GDB-monitor reply framing prefixes the OpenOCD Tcl
            # result with this marker; the actual output starts one
            # character after it.
            response_marker = "response:"
            r = res.find(response_marker)
            res = res[r + len(response_marker) + 1 :]
            try:
                addr_val = bytearray.fromhex(res).decode().replace("\n", "")
                print("{}: {}".format(hex(addr + (4 * j)), addr_val))
            except Exception as e:
                logger.error(
                    f"dap_access_gdb_read_consecutive: failed to decode response for "
                    f"{hex(addr + (4 * j))} (word {j}): {e} (raw={res!r})"
                )
        else:
            logger.error(
                f"dap_access_gdb_read_consecutive: command failed for "
                f"{hex(addr + (4 * j))} (word {j}): {result.GetError()}"
            )


def dap_access_gdb_write(cmd_intr: lldb.SBCommandInterpreter, result, addr, data):
    """!
    @brief Perform a dap access write of a single 32-bit word.

    @param cmd_intr -   command interpreter to run command over lldb
    @param result   -   command interpreter result
    @param addr     -   address to write
    @param data     -   value to write

    @return True if every command in the write batch succeeded, False
            otherwise.
    """
    if addr < 0 or (data is not None and data < 0):
        logger.error(f"dap_access_gdb_write: refusing negative addr={hex(addr)} data={data}")
        return False

    cmd_list = dap_access_cmd_list(addr, data, 'write')

    for cmd in cmd_list:
        cmd_intr.HandleCommand("{} {}".format(g_dbgContext.monitorPrefix, cmd), result)
        if not result.Succeeded():
            logger.error(f"dap_access_gdb_write: command failed: {cmd} ({result.GetError()})")
            return False

    return True


def dap_access_gdb_write_mask(cmd_intr: lldb.SBCommandInterpreter, result, write_reg, data, mask):
    """!
    @brief Perform a masked dap access write: read-modify-write write_reg,
    replacing only the bits selected by mask.

    @param cmd_intr -   command interpreter to run command over lldb
    @param result   -   command interpreter result
    @param write_reg -  address to write
    @param data     -   data to write
    @param mask     -   mask for bit change

    """
    if mask is not None and mask < 0:
        logger.error(f"dap_access_gdb_write_mask: refusing negative mask {hex(mask)}")
        return

    # Read data from register
    old_data = dap_access_gdb_read(cmd_intr, result, "No", write_reg)

    if old_data is None:
        logger.error(
            f"dap_access_gdb_write_mask: aborting masked write to {hex(write_reg)}, "
            f"read of current value failed"
        )
        return

    old_data = int(old_data, 16)

    # calculate bit mask
    # clear the bits in old_data where the mask has 1s
    if mask == 0:
        shifted_value = 0
        cleared_data = old_data
    else:
        cleared_data = old_data & ~mask
        # Shift the new value into position at the mask's lowest set bit,
        # then apply the mask (mask.bit_length()-1 would instead shift to
        # the mask's HIGHEST set bit, wrong for any multi-bit field).
        shift = (mask & -mask).bit_length() - 1
        shifted_value = (data << shift) & mask

    # Combine the cleared data and the shifted new value
    masked_new_data = cleared_data | shifted_value

    # Write mask value in register
    dap_access_gdb_write(cmd_intr, result, write_reg, masked_new_data)


def dapAccessGDB(debugger: lldb.SBDebugger, command, result, scriptRun=None, regAddress=None, num_of_words=None,
                 write_reg=None, data=None, mask=None):
    """!
    This function to implement dap_access command, that is used to access secure
    memory regions for reads and writes via gdb.

    Usage :
        dump        :  dap_access -r <address> -n <units>
        read        :  dap_access -r <address>
        write       :  dap_access -w <address> -v <value>
        write mask  :  dap_access -w <address> -v <value> -m <mask>

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

    global g_dbgContext
    result = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()

    # Convert string to hexadecimal integer
    # if regAddress/write_reg/data/mask is integer type then this is skipped
    if isinstance(regAddress, str):
        regAddress = int(regAddress, 16)

    if isinstance(write_reg, str):
        write_reg = int(write_reg, 16)

    if isinstance(data, str):
        data = int(data, 16)

    if isinstance(mask, str):
        mask = int(mask, 16)

    # this section is used for reading a single address
    if regAddress is not None and num_of_words is None:
        read_data = dap_access_gdb_read(commandInterpreter, result, scriptRun,
                                        regAddress)
        return read_data

    # this section is used for dumping n number of addresses
    if regAddress is not None and num_of_words:
        dap_access_gdb_read_consecutive(commandInterpreter, result, scriptRun, regAddress,
                                        num_of_words)

    # this section is used writing to an address
    if write_reg is not None and mask is None:
        dap_access_gdb_write(commandInterpreter, result, write_reg,
                             data)

    if write_reg is not None and mask is not None:
        dap_access_gdb_write_mask(commandInterpreter, result, write_reg,
                                  data, mask)


def dap_access_cmd_list(regAddress, data = None,  rw = None):
    """!
    This function returns the list of raw dpreg/apreg dap read and write
    command strings for both ADIv5 and ADIv6, used by dap_access.py's own
    GDB-monitor read/write functions and by telnet.py's dapAccessTelnet.
    ADIv5 uses a small AP index and a manual "dap dpreg 0x8 ..." SELECT
    poke before apreg; ADIv6 uses the AP's base address as its apreg
    ap-num and needs no manual SELECT poke (apreg self-manages that).

    @param regAddress       -   Address of the read register that we want to
                                read or write.
    @param  data            -   Value that we want to write to the specified
                                write register address.

    @param  rw              -   Variable denotes a string that represents either
                                read or write operation.
    """
    chip_name = g_dbgContext.chipsetName.upper()

    if isinstance(regAddress, str):
        regAddress = int(regAddress, 16)

    if isinstance(data, str):
        data = int(data, 16)

    adi_version = get_adi_version()

    dap_dpreg_substring = f"{chip_name}.dap dpreg"
    dap_apreg_substring = f"{chip_name}.dap apreg"

    if adi_version == '6':
        # For an ADIv6 DAP, apreg's ap-num argument is the AP's base
        # address in the debug memory map, not a small 0-255 index like
        # ADIv5. apreg self-manages DP SELECT/bank-switching -- no manual
        axi_ap_num = 0x24000
        axi_csw_default = 0x10806000
        axi_csw_32bit = axi_csw_default | 0x12  # CSW_ADDRINC_SINGLE(0x10) | CSW_32BIT(0x02)
        axi_reg_csw = 0xD00
        axi_reg_tar_lsw = 0xD04
        axi_reg_tar_msw = 0xD08
        axi_reg_drw = 0xD0C

        cmd_list = [
                f"{dap_apreg_substring} {hex(axi_ap_num)} {hex(axi_reg_csw)} {hex(axi_csw_32bit)}",
                f"{chip_name}.dap apsel {axi_ap_num}",
                f"{chip_name}.dap apcsw 0x10806000 0x30806000",
                f"{dap_apreg_substring} {hex(axi_ap_num)} {hex(axi_reg_tar_lsw)} {hex(regAddress & 0xFFFFFFFF)}",
                f"{dap_apreg_substring} {hex(axi_ap_num)} {hex(axi_reg_tar_msw)} {hex((regAddress >> 32) & 0xFFFFFFFF)}",
                f"{dap_apreg_substring} {hex(axi_ap_num)} {hex(axi_reg_drw)} ",
            ]
    else:
        cmd_list = [
                "%s %s" % (dap_dpreg_substring, "0x8 0xf0"),
                "%s %s" % (dap_dpreg_substring, "0x8 0x0"),
                "%s %s" % (dap_apreg_substring, "0 0x00 0x11006012"),
                "%s %s %s" % (dap_apreg_substring, str("0 0x04" ), hex(regAddress)),
                "%s %s" % (dap_apreg_substring, str("0 0x08 0x0 ")),
                "%s %s" % (dap_apreg_substring, str("0 0x0C " )),
            ]

    if (rw.lower() == 'write'):
        cmd_list[-1] += hex(data)

    return cmd_list

