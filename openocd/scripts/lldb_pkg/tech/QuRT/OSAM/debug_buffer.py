# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file debug_buffer.py
#
# @section Description
# This file contains the implementation of the debug buffer class
#

import lldb
import logging

try:
    from utils.debug_context import logger
    from lldb_pkg.tech.QuRT.OSAM.command_registry import register_command
    from lldb_pkg.tech.QuRT.OSAM.utils import (
        read_4_byte_symbol_value,
        save_formatter,
        restore_formatter
    )
except:
    from command_registry import register_command
    from utils import (
        read_4_byte_symbol_value,
        save_formatter,
        restore_formatter
    )
    from logger import logger

def qurt_debug_buffer(debugger):
    """
    Reads and prints the debug buffer.
    """
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()

    def read_debug_buffer(symbol_name, write_index, total_buffer_size):
        """
        Reads the debug buffer.

        Args:
            symbol_name (str): The name of the symbol.
            write_index (int): The write index.
            total_buffer_size (int): The total buffer size.

        Returns:
            bytearray: The debug buffer.
        """
        display_data_write_index = 0
        display_data_buffer = bytearray(total_buffer_size)
    
        symbols = target.FindSymbols(symbol_name, lldb.eSymbolTypeAny)
        if not symbols:
            logger.info(f"Symbol {symbol_name} not found")
            return
        
        symbol_addr = symbols[0].symbol.GetValue()
        value_bytes = process.ReadMemory(symbol_addr, 4, lldb.SBError())

        ptr_debug_data_start = int.from_bytes(value_bytes, byteorder='little')
        
        # Read from 0 to write_index
        start_offset = 0
        end_offset = total_buffer_size

        read_location = ptr_debug_data_start + start_offset
        read_index = start_offset

        while read_index < end_offset:
            read_size = 1024 if (end_offset - read_index) >= 1024 else (end_offset - read_index)
            debug_data = process.ReadMemory(read_location, read_size, lldb.SBError())
            display_data_buffer[display_data_write_index:display_data_write_index + read_size] = debug_data
            display_data_write_index += read_size
            read_index += read_size
            read_location += read_size

        if display_data_buffer[write_index] == 0:
            display_data_buffer = display_data_buffer[0:write_index]
        else:
            display_data_buffer = display_data_buffer[write_index:len(display_data_buffer)] + display_data_buffer[0: write_index]

        return display_data_buffer

    # Read debug buffer index
    write_index = read_4_byte_symbol_value(target,"debug_buf_index",logger)
    if write_index is None:
        return

    # Read buffer size
    total_buffer_size = read_4_byte_symbol_value(target,"QURTK_config_debug_buffer_size",logger)
    if total_buffer_size is None:
        return

    # Read data from index to buffer end
    display_data_buffer = read_debug_buffer("QURTK_debug_buf", write_index, total_buffer_size)
     # Save the current formatter
    original_formatter = save_formatter(logger)
    #Display output
    logger.info("%s", display_data_buffer.decode('utf-8', errors='ignore'))
    # Restore the original formatter
    restore_formatter(logger,original_formatter)
    
register_command('debugbuffer', qurt_debug_buffer)