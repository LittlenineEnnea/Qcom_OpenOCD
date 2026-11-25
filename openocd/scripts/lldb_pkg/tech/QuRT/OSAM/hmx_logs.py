# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# All rights reserved.
# Confidential and Proprietary - Qualcomm Technologies, Inc.
#
# @file hmx_logs.py
#
# @section Description
# This file contains the implementation of the hmx_log_data class
#

import lldb
import time
import logging

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
        utils_user_exception,
        check_symbol_exists
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
        utils_user_exception,
        check_symbol_exists
    )

class QuRTK_HMX_data:

    class LogEntry:
        """
        Represents a log entry for an HMX action.
        """

        def __init__(self):
            self.timestamp = None
            self.action = None
            self.thread = None
            self.hmx_thread = None
            self.hmx_queue = None

        def __str__(self):
            """
            Returns a string representation of the log entry.

            Returns:
                list: A list of string values representing the log entry.
            """
            return [
                hex(self.timestamp),
                f"{self.action}",
                hex(self.thread),
                hex(self.hmx_thread),
                hex(self.hmx_queue),
            ]

        def hmx_action(self, action, hmx_version):
            """
            Maps an HMX action code to a human-readable string.
            
            :param action: Integer representing the HMX action code.
            :param hmx_version: Integer representing the HMX version.
            :return: String representing the human-readable action.
            """
            if hmx_version == 1:
                action_map = {
                    0: "0 - HMX LOCK",
                    1: "1 - HMX UNLOCK",
                    2: "2 - HMX TRYLOCK",
                    3: "3 - HMX FREEZE WAIT",
                    4: "4 - HMX UNINSTALL"
                }
            else:
                action_map = {
                    0x10: "0x10 - HMXLOG_NON_SHARED_LOCK",
                    0x11: "0x11 - HMXLOG_SHARED_LOCK",
                    0x20: "0x20 - HMXLOG_NON_SHARED_UNLOCK",
                    0x21: "0x21 - HMXLOG_SHARED_UNLOCK",
                    0x30: "0x30 - HMXLOG_TRYLOCK",
                    0x40: "0x40 - HMXLOG_FREEZE_WAIT",
                    0x50: "0x50 - HMXLOG_UNINSTALL"
                }
            
            # Get the action description from the map, or return an invalid message if not found
            return action_map.get(action, f"{action} - *** INVALID")

    def __init__(self, debugger):
        self._debugger = debugger
        self.target = debugger.GetSelectedTarget()
        self.process = self.target.GetProcess()
        self.QURTK_cp_units = read_4_byte_symbol_value(self.target, "QURTK_cp_units", logger)
        self.QURTK_threads = read_4_byte_symbol_value(self.target, "QURTK_MAX_THREADS", logger)
        self.QURTK_threads_tcm = read_4_byte_symbol_value(self.target, "QURTK_MAX_THREADS_IN_TCM", logger)
        self.QURTK_hmxlog_num = read_4_byte_symbol_value(self.target, "QURTK_hmxlog_num", logger)
        self.raw_data = bytearray()
        self.QURTK_hmxlogging_addr = read_symbol_addr(self.target, "QURTK_hmxlogging", logger)
        self.headers = ["Timestamp", "hmx_action", "thread", "hmx_thread", "hmx_queue"]
        self.QURTK_hmxlogging_values = []
        self.log_data = []
        self.max_log_entries = 32
        self.log_struct = None
        self.HMXVersion = 0
        self.QURTK_hmx_thread = None

        if check_symbol_exists(self.target,"QURTK_hmx_thread",logger):
            self.HMXVersion = 1
            self.QURTK_hmx_thread = read_4_byte_symbol_value(self.target, "QURTK_hmx_thread", logger)
        elif check_symbol_exists(self.target,"QURTK_non_shared_hmx_thread",logger):
            self.HMXVersion = 2
            self.QURTK_hmx_thread = read_4_byte_symbol_value(self.target, "QURTK_non_shared_hmx_thread", logger)
        else:
            raise utils_user_exception("*** HMX doesn't exist ***")

    def print_table(self):
        """
        Prints the HMX data in a formatted table.
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
        struct_type = self.target.FindFirstType("QURTK_hmx_debug_struct")
        if not struct_type:
            raise utils_user_exception(f"Type QURTK_hmx_debug_struct not found")
        else:
            size = struct_type.GetByteSize()
            self.log_struct = struct_type

        bytes_to_be_read = size * self.max_log_entries
        bytes_read_out = 0
        bytes_remaining = bytes_to_be_read
        self.raw_data = bytearray(bytes_to_be_read)
        data_start = self.QURTK_hmxlogging_addr

        while bytes_read_out < bytes_to_be_read:
            bytes_iter = min(1024, bytes_remaining)
            self.raw_data[bytes_read_out:bytes_read_out + bytes_iter] = raw_read_memory(self.process,
                                                                                     logger,
                                                                                     data_start + bytes_read_out,
                                                                                     bytes_iter)
            bytes_read_out += bytes_iter
            bytes_remaining -= bytes_iter

    def fetch_logs(self):
        self.fetch_raw_data()
        iter = 0
        entry_size = self.log_struct.GetByteSize()

        while iter < self.max_log_entries:
            data = self.raw_data[(iter * entry_size): (iter * entry_size + entry_size)]
            self.QURTK_hmxlogging_values.append(create_value_from_data(self.target,
                                                                      logger,
                                                                      "QURTK_hmx_debug_struct", 
                                                                      data, 
                                                                      self.log_struct))
            iter += 1

        # Analyze the circular buffer
        if self.QURTK_hmxlog_num == 0:
            entry_to_be_processed = self.max_log_entries - 1
        else:
            entry_to_be_processed = self.QURTK_hmxlog_num - 1

        while entry_to_be_processed != self.QURTK_hmxlog_num:
            log_entry = self.LogEntry()
            log_entry.timestamp = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                                            "qtimer", 
                                                            logger).GetValueAsUnsigned()
            action_code = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                                   "action", 
                                                   logger).GetValueAsUnsigned()
            log_entry.action = log_entry.hmx_action(action_code, self.HMXVersion)
            log_entry.thread = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                                         "thread", 
                                                         logger).GetValueAsUnsigned()
            if self.HMXVersion == 1:
                log_entry.hmx_thread = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                                                 "hmx_thread", 
                                                                 logger).GetValueAsUnsigned()
            else:
                log_entry.hmx_thread = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                                                 "hmx_thread_cnt", 
                                                                 logger).GetValueAsUnsigned()
            log_entry.hmx_queue = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                                           "hmx_queue", 
                                                           logger).GetValueAsUnsigned()
            self.log_data.append(log_entry.__str__())

            if entry_to_be_processed == 0:
                entry_to_be_processed = self.max_log_entries - 1
            else:
                entry_to_be_processed -= 1

        # Take care of the oldest entry
        entry_to_be_processed = self.QURTK_hmxlog_num
        log_entry = self.LogEntry()
        log_entry.timestamp = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                                        "qtimer", 
                                                        logger).GetValueAsUnsigned()
        action_code = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                               "action", 
                                               logger).GetValueAsUnsigned()
        log_entry.action = log_entry.hmx_action(action_code, self.HMXVersion)
        log_entry.thread = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                                     "thread", 
                                                     logger).GetValueAsUnsigned()
        if self.HMXVersion == 1:
            log_entry.hmx_thread = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                                             "hmx_thread", 
                                                             logger).GetValueAsUnsigned()
        else:
            log_entry.hmx_thread = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                                             "hmx_thread_cnt", 
                                                             logger).GetValueAsUnsigned()
        log_entry.hmx_queue = get_child_value_err_raise(self.QURTK_hmxlogging_values[entry_to_be_processed],
                                                       "hmx_queue", 
                                                       logger).GetValueAsUnsigned()
        self.log_data.append(log_entry.__str__())

def hmx_logs(debugger):
    hmx_data = QuRTK_HMX_data(debugger)
    hmx_data.fetch_logs()
    hmx_data.print_table()
    
register_command('hmxlogs', hmx_logs)