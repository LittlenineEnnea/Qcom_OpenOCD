# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file utils.py
#
# @section Description
# This file contains the implementation of the utility functions used in the OSAM plugin.
#

import lldb
import logging


class utils_user_exception(Exception):
    pass
	
#TO Do
# Constant to define the maximum chunk size for memory reads.
# OpenOCD currently supports reading up to 1KB of memory at a time.
ONE_KB = 1024

def get_expression_value(target, logger, expression):
    """
    Evaluates an expression in the given target and returns its value.

    :param target: The lldb.SBTarget object representing the target process.
    :param logger: The logging.Logger object for logging information and errors.
    :param expression: The expression to evaluate as a string.
    :return: The value of the expression as a string if valid, otherwise None.
    """
    expression_result = target.EvaluateExpression(expression)
    if expression_result.IsValid():
        value = expression_result.GetValue()
        # logger.info("Value of Expression " + str(expression) + "  :  " + str(value))
        return value
    else:
        logger.info(f"Failed with error: {expression_result.GetError()}")
        return None


def create_value_from_data(target, logger, name, data, data_type):
    """
    Creates an lldb.SBValue object from the given data and data type.

    :param target: The lldb.SBTarget object representing the target process.
    :param logger: The logging.Logger object for logging information and errors.
    :param name: The name of the value as a string.
    :param data: The data to create the value from as bytes.
    :param data_type: The lldb.SBType object representing the type of the data.
    :return: The lldb.SBValue object if creation is successful, otherwise None.
    """
    sbdata = lldb.SBData()
    error = lldb.SBError()
    sbdata.SetData(error, data, target.GetByteOrder(), target.GetAddressByteSize())
    value = target.CreateValueFromData(name, sbdata, data_type)
    if not value.IsValid():
        logger.error(f"Failed to create value from data for {name}")
        return None
    return value


def raw_read_memory(process, logger, address, size):
    """
    Reads raw memory from the given process at the specified address and size.

    :param process: The lldb.SBProcess object representing the process.
    :param logger: The logging.Logger object for logging information and errors.
    :param address: The memory address to read from as an integer.
    :param size: The number of bytes to read as an integer.
    :return: The data read from memory as bytes if successful, otherwise None.
    """
    error = lldb.SBError()
    # logger.info("address type: {}, size type: {}".format(type(address), type(size)))
    # logger.info("address: {}, size: {}".format(address, size))
    data = process.ReadMemory(address, size, error)
    if error.Fail():
        logger.error(f"Failed to read memory at address {address}: {error}")
        return None
    return data


def save_formatter(logger):
    """
    Saves the current formatter of the logger and replaces it with a new formatter that does not include the log level.

    :param logger: The logging.Logger object for which the formatter is to be saved and replaced.
    :return: The original formatter of the logger.
    """
    original_formatter = logger.handlers[0].formatter
    # Create a new formatter without the prefix
    new_formatter = logging.Formatter('%(message)s')  # No level name
    # Replace the current formatter with the new one
    logger.handlers[0].setFormatter(new_formatter)
    return original_formatter


def restore_formatter(logger, original_formatter):
    """
    Restores the original formatter to the logger.

    :param logger: The logging.Logger object for which the formatter is to be restored.
    :param original_formatter: The original logging.Formatter object to restore.
    """
    logger.handlers[0].setFormatter(original_formatter)

def read_4_byte_symbol_value(target, symbol_name,logger):
    """
    Reads the value of a 4-byte symbol from the target.

    Args:
        target (lldb.SBTarget): The target from which to read the symbol.
        symbol_name (str): The name of the symbol to read.
        logger (logging.Logger): The logger to use for logging messages.

    Returns:
        int or None: The value of the symbol as an integer, or None if the symbol is not found.
    """
    symbols = target.FindSymbols(symbol_name, lldb.eSymbolTypeAny)
    if not symbols or symbols.GetSize() == 0:
        raise utils_user_exception(f"Symbol {symbol_name} not found")

    symbol_addr = symbols[0].symbol.GetValue()
    value_bytes = target.GetProcess().ReadMemory(symbol_addr, 4, lldb.SBError())
    return int.from_bytes(value_bytes, byteorder='little')

def asid_from_ssr(x):
    """
    Extracts the ASID from the SSR (System Status Register).

    Args:
        x (int): The SSR value.

    Returns:
        int: The ASID extracted from the SSR.
    """
    return (x >> 8) & 0x3F

def get_thread_name_from_ugp_data(ugp_value,ugp_data,logger):
    """
    Extracts the thread name from the raw UGP (User Global Pointer) data.

    Args:
        ugp_value (lldb.SBValue): The UGP value.
        ugp_data (bytes): The raw data of the UGP.
        logger (logging.Logger): The logger to use for logging messages.

    Returns:
        str or None: The thread name as a string, or None if any field is not found.
    """
    utcb = ugp_value.GetChildMemberWithName("utcb")
    if not utcb:
        raise utils_user_exception("utcb field not found")
    else:
        attr = utcb.GetChildMemberWithName("attr")
        if not attr:
            raise utils_user_exception("attr field not found")
        else:
            name_size = attr.GetChildMemberWithName("name").GetNumChildren()
            utcb_offset = ugp_value.GetType().GetFieldAtIndex(ugp_value.GetIndexOfChildWithName("utcb")).GetOffsetInBytes()
            attr_offset = utcb.GetType().GetFieldAtIndex(utcb.GetIndexOfChildWithName("attr")).GetOffsetInBytes()
            tname_offset = attr.GetType().GetFieldAtIndex(attr.
                        GetIndexOfChildWithName("name")).GetOffsetInBytes() + attr_offset + utcb_offset
            thread_name = ugp_data[tname_offset: tname_offset + name_size].split(b'\x00')[0].decode('utf-8',errors = 'ignore')
            return thread_name

def get_child_value_err_raise(value, field_name,logger):
    """
    Gets the child value of a field from an SBValue and raises an exception if the field is not found.

    Args:
        value (lldb.SBValue): The parent SBValue.
        field_name (str): The name of the child field to retrieve.
        logger (logging.Logger): The logger to use for logging messages.

    Returns:
        lldb.SBValue: The child SBValue if found.

    Raises:
        utils_user_exception: If the field is not found.
    """
    child = value.GetChildMemberWithName(field_name)
    if not child:
        raise utils_user_exception(f"{field_name} not found raising exception")
    else:
        return child

def get_child_value_as_uint32(value, field_name,logger):
    """
    Gets the value of a field as an unsigned 32-bit integer from an SBValue.

    Args:
        value (lldb.SBValue): The parent SBValue.
        field_name (str): The name of the child field to retrieve.
        logger (logging.Logger): The logger to use for logging messages.

    Returns:
        int or None: The value of the field as an unsigned 32-bit integer, or None if the field is not found.
    """
    child = value.GetChildMemberWithName(field_name)
    if not child:
        return None
    else:
        return child.GetValueAsUnsigned()
    
def read_symbol_name(address,target):
    """
    Resolve and return the symbol name for a given address in the target.

    Parameters:
    address (int): The memory address for which to resolve the symbol.
    target (lldb.SBTarget): The target in which to resolve the symbol.

    Returns:
    str or None: The name of the symbol if found, otherwise None.
    """
    resolve_scope = lldb.eSymbolContextEverything
    symbol = target.ResolveSymbolContextForAddress(lldb.SBAddress(address, target),resolve_scope)
    if symbol:
        symbol_name = symbol.GetSymbol().GetName()
        return symbol_name
    else:
        return None
    
def get_task_name(address, target, logger):
    """
    Resolves the task (thread) name from a memory address pointing to a thread context structure.

    This function reads the memory at the given address, interprets it as a QURTK_thread_context structure,
    and extracts the UGP pointer. It then reads the UGP structure and extracts the thread name.

    :param address: Memory address of the thread context structure.
    :param target: Debugging target object used to access types and memory.
    :param logger: Logger for error reporting.
    :return: The thread name as a string, or None if resolution fails.
    """
    thread_struct_type = target.FindFirstType("QURTK_thread_context")
    process = target.GetProcess()
    if not thread_struct_type:
        return None

    size = thread_struct_type.GetByteSize()
    data = raw_read_memory(process, logger, address, size)
    if not data:
        return None

    value = create_value_from_data(target, logger, "thread_ctx", data, thread_struct_type)
    if not value or not value.IsValid():
        return None

    ugp = value.GetChildMemberWithName("ugp").GetValueAsUnsigned()
    if not ugp:
        return None

    ugp_struct_type = target.FindFirstType("QURT_ugp_ptr")
    if not ugp_struct_type:
        return None

    ugp_data = raw_read_memory(process, logger, ugp, ugp_struct_type.GetByteSize())
    if not ugp_data:
        return None

    ugp_value = create_value_from_data(target, logger, "ugp_value", ugp_data, ugp_struct_type)
    name = get_thread_name_from_ugp_data(ugp_value, ugp_data, logger)
    return name

def read_symbol_addr(target,symbol_name,logger):
    """
    Reads the address of a symbol.

    Parameters:
    symbol_name (str): The name of the symbol.

    Returns:
    int or None: The address of the symbol if found, otherwise None.
    """
    symbols = target.FindSymbols(symbol_name, lldb.eSymbolTypeAny)
    if not symbols or symbols.GetSize() == 0:
        raise utils_user_exception(f"Symbol {symbol_name} not found exception raised")
    symbol_addr = symbols[0].symbol.GetValue()
    return symbol_addr

def read_memory_uint32(process, logger, addr):
    """
    Read a 32-bit unsigned integer from the memory of a given process.

    Parameters:
        process: The process handle or object from which memory is to be read.
        logger: Logger instance used for logging warnings or errors.
        addr (int): The memory address to read from.

    Returns:
        int: The 32-bit unsigned integer read from memory, or 0 if the read fails.
    """
    data = raw_read_memory(process, logger, addr, 4)

    if not data:
        logger.warning(f"Failed to read memory at 0x{addr:08X}")
        return 0

    return int.from_bytes(data, byteorder='little')


def read_memory_cstring(process, logger, addr, length):
    """
    Read a null-terminated C-style string from the memory of a given process.

    Parameters:
        process: The process handle or object from which memory is to be read.
        logger: Logger instance used for logging warnings or errors.
        addr (int): The starting memory address to read from.
        length (int): The maximum number of bytes to read.

    Returns:
        str: The decoded string read from memory, or an empty string if the read fails.
    """
    data = raw_read_memory(process, logger, addr, length)

    if not data:
        logger.warning(f"Failed to read string at 0x{addr:08X}")
        return ""

    return data.split(b'\x00')[0].decode('utf-8', errors='ignore')


def get_struct_field_offset(struct_type, field_name, logger):
    """
    Get the byte offset of a specified field in a structure.

    Parameters:
        struct_type: The structure type object that provides field metadata.
        field_name (str): The name of the field whose offset is to be retrieved.
        logger: Logger instance used for logging errors.

    Returns:
        int: The byte offset of the field within the structure, or 0 if the field is not found.
    """
    for i in range(struct_type.GetNumberOfFields()):
        field = struct_type.GetFieldAtIndex(i)
        if field.GetName() == field_name:
            offset = field.GetOffsetInBytes()
            return offset

    #logger.error(f"Field '{field_name}' not found in QURTK_pcb_t")
    return 0

def check_symbol_exists(target, symbol_name,logger):
    """
    Check if a symbol exists in the loaded modules.

    Parameters:
        symbol_name (str): The name of the symbol to check.

    Returns:
        bool: True if the symbol is found, False otherwise.
    """
    symbols = target.FindSymbols(symbol_name, lldb.eSymbolTypeAny)
    if not symbols or symbols.GetSize() == 0:
        return False
    else:
        return True