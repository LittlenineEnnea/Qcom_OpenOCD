# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# All rights reserved.
# Confidential and Proprietary - Qualcomm Technologies, Inc.
#
# @file hvx_logs.py
#
# @section Description
# This file contains the implementation of the hvx_log_data class

import lldb
import time
import logging
#from utils.debug_context import logger

try:
    from utils.debug_context import logger
    from lldb_pkg.tech.QuRT.OSAM.command_registry import register_command
    from lldb_pkg.tech.QuRT.OSAM.utils import (
        get_expression_value,
        create_value_from_data,
        raw_read_memory,
        save_formatter,
        read_4_byte_symbol_value,
        restore_formatter,
        get_thread_name_from_ugp_data,
        get_task_name,
        read_symbol_name,
        read_symbol_addr,
        get_child_value_err_raise,
        utils_user_exception
    )
except:
    from logger import logger
    from command_registry import register_command
    from utils import (
        get_expression_value,
        create_value_from_data,
        raw_read_memory,
        save_formatter,
        read_4_byte_symbol_value,
        restore_formatter,
        get_thread_name_from_ugp_data,
        get_task_name,
        read_symbol_name,
        read_symbol_addr,
        get_child_value_err_raise,
        utils_user_exception
    )

class QuRTK_HVX_data:

    class LogEntry:
        """
        Represents a log entry for an interrupt.
        """

        def __init__(self):
            self.timestamp = None
            self.action = None
            self.unit = None
            self.free_units = None
            self.flag = None
            self.data1 = None
            self.data2 = None
            self.data3 = None
            self.pending_queue = None
            self.ready_pending = None

        def __str__(self):
            """
            Returns a string representation of the log entry.

            Returns:
                list: A list of string values representing the log entry.
            """
            return [
                hex(self.timestamp),
                f"{self.action}",
                hex(self.unit),
                hex(self.free_units),
                f"{self.flag}",
                hex(self.data1),
                hex(self.data2),
                hex(self.data3),
                hex(self.pending_queue),
                hex(self.ready_pending),
            ]

        def hvx_action(self, action):
            """
            Maps an HVX action code to a human-readable string.
            
            :param action: Integer representing the HVX action code.
            :return: String representing the human-readable action.
            """
            action_map = {
                0x1: "0x01 - GET_UNIT",
                0x2: "0x02 - RELEASE RUNNING",
                0x3: "0x03 - RELEASE PREEMPTED",
                0x4: "0x04 - PENDING",
                0x5: "0x05 - GRAB UNIT",
                0x6: "0x06 - FREEZE RUNNING",
                0x7: "0x07 - BUFFER POP",
                0x8: "0x08 - BUFFER PUSH",
                0x9: "0x09 - READY PENDING",
                0x11: "0x11 - GET VREGS",
                0x12: "0x12 - SUSPEND",
                0x13: "0x13 - RESUME"
            }
            
            # Get the action description from the map, or return an invalid message if not found
            return action_map.get(action, f"{action} - *** INVALID")


    def __init__(self, debugger):
        self._debugger = debugger
        self.target = debugger.GetSelectedTarget()
        self.process = self.target.GetProcess()
        self.QURTK_cp_units = read_4_byte_symbol_value(self.target,"QURTK_cp_units",logger)
        self.QURTK_hvx_units = read_4_byte_symbol_value(self.target,"QURTK_hvx_units",logger)
        self.QURTK_hvx_free_units = read_4_byte_symbol_value(self.target,"QURTK_hvx_free_units",logger)
        self.QURTK_hvxlog_num = read_4_byte_symbol_value(self.target,"QURTK_hvxlog_num",logger)
        self.raw_data = bytearray()
        self.QURTK_hvxlogging_addr =  read_symbol_addr(self.target,"QURTK_hvxlogging",logger)
        self.headers = ["Timestamp","action","unit","free_units","flag","data1","data2","data3","pending_queue","Ready_Pending"]
        self.QURTK_hvxlogging_values = []
        self.log_data = []
        self.max_log_enitres = 256
        self.log_struct = None

    def print_table(self):
        """
        Prints the interrupt data in a formatted table.
        """
        # Save the current formatter
        original_formatter = save_formatter(logger)

        # Calculate the width of each column
        col_widths = [len(header) for header in self.headers]
        for row in self.log_data:
            for i, value in enumerate(row):
                col_widths[i] = max(col_widths[i], len(str(value)))

        # Print the header
        header_line = " | ".join(f"{header:^{col_widths[i]}}" for i, header in enumerate(self.headers))
        logger.info(header_line)
        logger.info("-" * len(header_line))

        # Print each row
        for row in self.log_data:
            row_line = " | ".join(f"{str(value):^{col_widths[i]}}" for i, value in enumerate(row))
            logger.info(row_line)

        # Restore the original formatter
        restore_formatter(logger, original_formatter)

    def fetch_raw_data(self):
        struct_type = self.target.FindFirstType("QURTK_hvx_logging_struct")
        if not struct_type:
            raise utils_user_exception(f"Type QURTK_hvx_logging_struct not found")
        else:
            size = struct_type.GetByteSize()
            self.log_struct = struct_type
        
        bytes_to_be_read = size*self.max_log_enitres #need to find out how to remove hard coding as its a MACRO
        bytes_read_out = 0
        bytes_remaining = bytes_to_be_read
        self.raw_data =bytearray(bytes_to_be_read)
        data_start = self.QURTK_hvxlogging_addr
        while(bytes_read_out<bytes_to_be_read):
            bytes_iter = min (1024, bytes_remaining)
            self.raw_data[bytes_read_out: bytes_read_out+bytes_iter] = raw_read_memory(self.process,
                                                                logger,data_start+bytes_read_out,bytes_iter)
            bytes_read_out = bytes_read_out+bytes_iter
            bytes_remaining -= bytes_iter
    def fetch_logs(self):

        self.fetch_raw_data()
        iter = 0 
        entry_size = self.log_struct.GetByteSize()

        while(iter < self.max_log_enitres):
            data = self.raw_data[(iter*entry_size) : (iter*entry_size + entry_size)]
            self.QURTK_hvxlogging_values.append(create_value_from_data(self.target,
                                                                        logger,"QURTK_hvx_logging_struct", data, self.log_struct))
            
            iter += 1
        #analyze the circular buffer
        if (self.QURTK_hvxlog_num == 0):
            entry_to_be_processed = self.max_log_enitres -1
        else:
            entry_to_be_processed = self.QURTK_hvxlog_num - 1
        while(entry_to_be_processed != self.QURTK_hvxlog_num):
            log_entry = self.LogEntry()
            log_entry.timestamp = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"qtimer",logger).GetValueAsUnsigned()
            log_entry.action = log_entry.hvx_action(get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],
                                                                              "action",logger).GetValueAsUnsigned())
            log_entry.unit = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"unit",logger).GetValueAsUnsigned()
            log_entry.free_units = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"free_units",logger).GetValueAsUnsigned()
            log_entry.flag = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"flag",logger).GetValueAsUnsigned()
            log_entry.data1 = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"data1",logger).GetValueAsUnsigned()
            log_entry.data2 = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"data2",logger).GetValueAsUnsigned()
            log_entry.data3 = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"data3",logger).GetValueAsUnsigned()
            log_entry.pending_queue = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"pending_queue",logger).GetValueAsUnsigned()
            log_entry.ready_pending = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"ready_pending",logger).GetValueAsUnsigned()
            self.log_data.append(log_entry.__str__())

            if (entry_to_be_processed == 0):
                entry_to_be_processed = self.max_log_enitres -1
            else:
                entry_to_be_processed -= 1

        #Take care of th oldest entry
        entry_to_be_processed = self.QURTK_hvxlog_num
        log_entry = self.LogEntry()
        log_entry.timestamp = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"qtimer",logger).GetValueAsUnsigned()
        log_entry.action = log_entry.hvx_action(get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],
                                                                              "action",logger).GetValueAsUnsigned())
        log_entry.unit = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"unit",logger).GetValueAsUnsigned()
        log_entry.free_units = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"free_units",logger).GetValueAsUnsigned()
        log_entry.flag = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"flag",logger).GetValueAsUnsigned()
        log_entry.data1 = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"data1",logger).GetValueAsUnsigned()
        log_entry.data2 = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"data2",logger).GetValueAsUnsigned()
        log_entry.data3 = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"data3",logger).GetValueAsUnsigned()
        log_entry.pending_queue = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"pending_queue",logger).GetValueAsUnsigned()
        log_entry.ready_pending = get_child_value_err_raise(self.QURTK_hvxlogging_values[entry_to_be_processed],"ready_pending",logger).GetValueAsUnsigned()
        self.log_data.append(log_entry.__str__())
        
def hvx_logs(debugger):
    hvx_data = QuRTK_HVX_data(debugger)
    hvx_data.fetch_logs()
    hvx_data.print_table()
    
register_command('hvxlogs', hvx_logs)