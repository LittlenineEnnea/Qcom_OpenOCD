# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file task_list.py
#
# @section Description
# This file contains the implementation of the task list class
#


import lldb
import time
import logging

try:
    from utils.debug_context import logger
    from lldb_pkg.tech.QuRT.OSAM.command_registry import register_command
    from lldb_pkg.tech.QuRT.OSAM.utils import (
        create_value_from_data,
        raw_read_memory,
        save_formatter,
        restore_formatter,
        read_4_byte_symbol_value,
        asid_from_ssr,
        get_thread_name_from_ugp_data,
        get_child_value_err_raise,
        get_child_value_as_uint32,
        read_symbol_addr,
        utils_user_exception
    )
except:
    from logger import logger
    from command_registry import register_command
    from utils import (
        create_value_from_data,
        raw_read_memory,
        save_formatter,
        restore_formatter,
        read_4_byte_symbol_value,
        asid_from_ssr,
        get_thread_name_from_ugp_data,
        get_child_value_err_raise,
        get_child_value_as_uint32,
        read_symbol_addr,
        utils_user_exception
    )


class TCB_data:

    def __init__(self):
        self.tcb_ptr = None
        self.valid = None
        self.ugp_address = None
        self.tid = None
        self.pid = None
        self.prio = None
        self.asid = None
        self.tnum = None
        self.state = None
        self.thread_name = None

    def __str__(self):
        """
        Returns a string representation of the log entry.

        Returns:
            list: A list of string values representing the log entry.
        """
        return [
            f"{hex(self.tcb_ptr)}",
            f"{self.valid.GetValueAsUnsigned()}",
            f"{hex(self.ugp_address.GetValueAsUnsigned())}",
            f"{hex(self.tid.GetValueAsUnsigned())}",
            f"{hex(self.pid.GetValueAsUnsigned())}",
            f"{hex(self.prio.GetValueAsUnsigned())}",
            f"{self.asid}",
            f"{self.tnum}",
            f"{self.state}",
            f"{self.thread_name}"
        ]
        
class TcbList:

    def __init__(self, debugger):
        self.debugger = debugger
        self.target = debugger.GetSelectedTarget()
        self.process = self.target.GetProcess()
        self.TCB_List = []
        self.par_tcblist = 0
        self.user_error_value = lldb.SBValue()
        self.user_error_addr = 0
        self.max_threads_ddr = 0
        self.max_threads_tcm = 0
        self.max_hthreads = 0
        self.context_size = 0
        self.island_mode = 0
        self.table = []
        self.headers = [
            "tcb_ptr",
            "Valid",
            "Ugp Address",
            "Thread_ID",
            "PID",
            "Priority",
            "ASID",
            "TNUM",
            "State",
            "Thread Name"
        ]

    def qurt_os_initted(self):
        """Checks if the OS is initialized for the TCB."""
        # OS_Initialized 
        # Read max TCM threads.
        self.max_threads_tcm = read_4_byte_symbol_value(self.target,"QURTK_MAX_THREADS_IN_TCM",logger)
        # Read max TCM threads.
        self.context_size = read_4_byte_symbol_value(self.target,"QURTK_CONTEXT_SIZE",logger)
        # Read max HW threads.
        self.max_hthreads = read_4_byte_symbol_value(self.target,"QURTK_MAX_HTHREADS",logger)
        # Read island mode.
        self.island_mode = read_4_byte_symbol_value(self.target,"QURTK_island_mode",logger)

        self.user_error_addr = read_symbol_addr(self.target,"QURT_error_info",logger)

        error_type = self.target.FindFirstType("QURT_error")
        if not error_type:
            raise utils_user_exception(f"Type QURT_error not found")
        else:
            error_size = error_type.GetByteSize()
            user_err_data = raw_read_memory(self.target.GetProcess(),logger,self.user_error_addr,error_size)
            self.user_error_value = create_value_from_data(self.target,logger,"user_error_value",user_err_data,error_type)
            

        # If island is garbage when TCM not succesfully backed up.
        if self.island_mode > 1:
            self.island_mode = 0
        # If island mode.
        if self.island_mode:
            # Read TCM context.
            self.par_tcblist = read_4_byte_symbol_value(self.target,"QURTK_thread_contexts_tcm",logger)
            # Read page table symbol.
            par_slist = read_4_byte_symbol_value(self.target,"QURTK_island_vtlb_main",logger)
            return "OS_Initted"
        else:
            # Read Thread context.
            self.par_tcblist = read_4_byte_symbol_value(self.target,"QURTK_thread_contexts",logger)
            # Read page table symbol.
            par_slist = read_4_byte_symbol_value(self.target,"QURTK_vtlb_main",logger)


        # Read max config threads QURTK_config_MAX_THREADS
        max_threads = read_4_byte_symbol_value(self.target,"QURTK_config_MAX_THREADS",logger)
        self.max_threads_ddr = max_threads - self.max_threads_tcm

        #Not need to check if QURTK_vtlb_main and QURTK_thread_contexts are avialable since it would have asserted
        #if assert removed we need to add the check here.
        if self.par_tcblist != 0x1F1F1F1F and par_slist != 0x1F1F1F1F:
            init_flag = read_4_byte_symbol_value(self.target,"qurt_has_initted",logger)
            if init_flag == 1:
                return "OS_Initted"
            else:
                raise utils_user_exception(f"symbol qurt_has_initted is not found or value is not 1. OS_Initialized fails, returning 0")
        else:
            raise utils_user_exception(f"QURTK_vtlb_main or QURTK_thread_contexts is not initialized. OS_Initialized fails, returning 0")

    def print_table(self):
        """Prints the table."""
        # Save the current formatter
        original_formatter = save_formatter(logger)

        # Calculate the width of each column
        col_widths = [len(header) for header in self.headers]
        for row in self.TCB_List:
            for i, value in enumerate(row.__str__()):
                col_widths[i] = max(col_widths[i], len(str(value)))

        # Print the header
        header_line = " | ".join(f"{header:^{col_widths[i]}}" for i, header in enumerate(self.headers))
        logger.info(header_line)
        logger.info("-" * len(header_line))

        # Print each row
        for row in self.TCB_List:
            row_line = " | ".join(f"{str(value):^{col_widths[i]}}" for i, value in enumerate(row.__str__()))
            logger.info(row_line)

        # Restore the original formatter
        restore_formatter(logger,original_formatter)
    
    def is_stopped_process(self, value, tcb_ptr):
        """Checks if a process is stopped."""
        error = get_child_value_err_raise(value,"error",logger)
        cause = get_child_value_err_raise(error,"cause",logger)
        cause_value = cause.GetValueAsUnsigned()

        user_errors = self.user_error_value.GetChildMemberWithName("users_errors").GetChildMemberWithName("entry")
        process_state = None
        err_code = 1
        for i in range(user_errors.GetNumChildren()):
            err_code = user_errors.GetChildAtIndex(i).GetChildMemberWithName("error_code").GetValueAsUnsigned()
            err_tcb = user_errors.GetChildAtIndex(i).GetChildMemberWithName("error_tcb").GetValueAsUnsigned()

            if err_code == cause_value and tcb_ptr == err_tcb:
                process_state =  "STOPPED"
            if err_code == 0:
                process_state =  None
        return process_state
    
    def determine_thread_state(self, value, hthread_num, tcb_ptr):
        """Determines the state of a thread."""
        state_str = "READY"
        
        futex_ptr = get_child_value_err_raise(value,"futex_ptr",logger)
        futex_value = futex_ptr.GetValueAsUnsigned()
 
        pending_var = get_child_value_err_raise(value,"pending",logger)
        pending_value = pending_var.GetValueAsUnsigned()
            
        cfg = get_child_value_err_raise(value,"cfg",logger)    
        if hthread_num >= 0 and hthread_num < self.max_hthreads:
            state_str = "RUNNING"
            if get_child_value_as_uint32(cfg, "bit_suspend_mark",logger):
                state_str = "RUNNING (SUSP_M)"

        elif get_child_value_as_uint32(cfg, "bit_frozen",logger):
            state_str = "FROZEN"
        elif get_child_value_as_uint32(cfg, "bit_marked_for_kill",logger):
            state_str = "CAPTURED"
        elif get_child_value_as_uint32(cfg, "bit_freeze_requested",logger):
            state_str = "FREEZE_REQUESTED"
        elif self.is_stopped_process(value, tcb_ptr):
            state_str = "EXCEPTION"
        elif get_child_value_as_uint32(cfg, "bit_suspended",logger):
            state_str = "SUSPENDED"
        elif futex_value != 0 or pending_value != 0:
            state_str = "WAITING"
            if get_child_value_as_uint32(cfg, "bit_suspend_mark",logger):
                state_str = "WAITING (SUSP_M)"
        else:
            state_str = "READY"
            if get_child_value_as_uint32(cfg, "bit_suspend_mark",logger):
                state_str = "READY (SUSP_M)"
        return state_str

    def fetch_n_print_tcb_list(self, tcb_ptr, max_iter_threads):
        """Fetches and prints the TCB list."""
        index = 0

        struct_type = self.target.FindFirstType("QURTK_thread_context")
        offset = 0
        if not struct_type:
            raise utils_user_exception(f"Type QURTK_thread_context not found")
        else:
            size = struct_type.GetByteSize()

        bytes_to_be_read = size*max_iter_threads
        bytes_read_out = 0
        bytes_remaining = bytes_to_be_read
        tcb_raw =bytearray(bytes_to_be_read)
        tcb_start = tcb_ptr
        while(bytes_read_out<bytes_to_be_read):
            bytes_iter = min (1024, bytes_remaining)
            tcb_raw[bytes_read_out: bytes_read_out+bytes_iter] = raw_read_memory(self.process,
                                                                logger,tcb_start+bytes_read_out,bytes_iter)
            bytes_read_out = bytes_read_out+bytes_iter
            bytes_remaining -= bytes_iter

        while index < max_iter_threads:
            tcb_data = tcb_raw[index*size:((index+1)*size)]
            value = create_value_from_data(self.target,logger,"value", tcb_data, struct_type)

            ssr = get_child_value_err_raise(value,"ssr",logger)
            
            prio = get_child_value_err_raise(value,"prio",logger)
            
            flags = get_child_value_err_raise(value,"flags",logger)
            valid = get_child_value_err_raise(flags,"valid",logger)

            if valid.GetValueAsUnsigned() == 1:

                tcb_attributes = TCB_data()
                tcb_attributes.tcb_ptr = tcb_ptr
                tcb_attributes.valid = valid

                hthread = get_child_value_err_raise(value,"hthread",logger)
                state_str = self.determine_thread_state(value, hthread.GetValueAsUnsigned(), tcb_ptr)
                if hthread.GetValueAsUnsigned() >= 0 and hthread.GetValueAsUnsigned() < self.max_hthreads:
                    hthread_str = hex(hthread.GetValueAsUnsigned())
                    state_str = "RUNNING"
                else:
                    hthread_str = " "

                ugp = get_child_value_err_raise(value,"ugp",logger)
                # Ensure ugp is a valid address
                if not ugp.GetValueAsUnsigned():
                    logger.error(f"ugp is not a valid address")

                
                # Fetch type for ugp
                ugp_struct_type = self.target.FindFirstType("QURT_ugp_ptr")
                if not ugp_struct_type:
                    raise utils_user_exception(f"Type QURT_ugp_ptr not found")
                # Read the memory at the address pointed to by ugp
                ugp_data = raw_read_memory(self.process,logger,ugp.GetValueAsUnsigned(), ugp_struct_type.GetByteSize())
                ugp_value = create_value_from_data(self.target,logger,"ugp_value", ugp_data, ugp_struct_type)
                if not ugp_value.IsValid():
                    raise utils_user_exception(f"Failed to create value from ugp data")
                else:
                    # Get thread_name
                    utcb = get_child_value_err_raise(ugp_value,"utcb",logger)
                    attr = get_child_value_err_raise(utcb,"attr",logger)
                    thread_name = get_thread_name_from_ugp_data(ugp_value,ugp_data,logger)                
                    thread_id = get_child_value_err_raise(utcb,"thread_id",logger)
                    pid = get_child_value_err_raise(utcb,"pid",logger)

                    tcb_attributes.ugp_address = ugp
                    tcb_attributes.tid = thread_id
                    tcb_attributes.pid = pid
                    tcb_attributes.prio = prio
                    tcb_attributes.asid = asid_from_ssr(ssr.GetValueAsUnsigned())
                    tcb_attributes.tnum = hthread_str
                    tcb_attributes.state = state_str
                    tcb_attributes.thread_name = thread_name
                    
                    self.TCB_List.append(tcb_attributes)

            tcb_ptr = tcb_ptr + self.context_size
            index = index + 1
        # Print the table
        self.print_table()

    def fetch_task_list(self):
        """Fetches the task list."""
        if self.qurt_os_initted():
            tcb_ptr = self.par_tcblist

        if self.island_mode == 0:
            self.fetch_n_print_tcb_list(tcb_ptr, self.max_threads_ddr)
        else:
            self.fetch_n_print_tcb_list(tcb_ptr, self.max_threads_tcm)

def fetch_task_list(debugger):
    """Fetches the task list."""
    tcb = TcbList(debugger)
    tcb.fetch_task_list()

register_command('tasklist', fetch_task_list)