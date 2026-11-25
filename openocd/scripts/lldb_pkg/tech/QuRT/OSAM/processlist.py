# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file processlist.py
#
# @section Description
# This file contains the implementation of the processlist class
#

import lldb

try:
    from lldb_pkg.tech.QuRT.OSAM.command_registry import register_command
    from lldb_pkg.tech.QuRT.OSAM.utils import (
        raw_read_memory,
        save_formatter,
        restore_formatter,
        get_task_name,
        read_4_byte_symbol_value,
        read_memory_uint32, 
        read_memory_cstring,
        get_struct_field_offset
    )
except:
    from command_registry import register_command
    from utils import (
        raw_read_memory,
        save_formatter,
        restore_formatter,
        get_task_name,
        read_4_byte_symbol_value,
        read_memory_uint32, 
        read_memory_cstring,
        get_struct_field_offset
    )


# Constants for thread configuration flags
FROZEN = 0x2                #Indicates the thread is frozen.
MARKED_FOR_KILL = 0x4       #Indicates the thread is marked for termination
FREEZE_REQUESTED = 0x8      #Indicates a freeze has been requested for the thread

class ProcessEntry:
    """
    Represents a single process entry with relevant metadata.

    Attributes:
        pcb_addr (int): Address of the process control block.
        name (str): Name of the process.
        ptype (str): Type/class of the process.
        state (str): Current state of the process.
        asid (int): Address space ID.
        pid (int): Process ID.
    """
    def __init__(self, pcb_addr, name, ptype, state, asid, pid):
        self.pcb_addr = pcb_addr
        self.name = name
        self.ptype = ptype
        self.state = state
        self.asid = asid
        self.pid = pid

    
    def __str__(self):
        """
        Return a formatted string representation of the process entry.

        Returns:
            str: Formatted process details.
        """
        return f"{self.pcb_addr:08X} | {self.name:<16} | {self.ptype:<10} | {self.state:<20} | {self.asid:08X} | {self.pid:08X}"


class ProcessList:
    """
    Manages and displays a list of processes from the target system.

    Attributes:
        debugger: LLDB debugger instance.
        logger: Logger instance for logging messages.
        target: LLDB target object.
        process: LLDB process object.
        entries (list): List of ProcessEntry objects.
        debug_pcb (int): PCB address of the debugged process.
        thread_active (int): Number of active threads in the debugged process.
        thread_list_addr (int): Address of the thread list.
        pcb_type: LLDB type object for QURTK_pcb_t.
        pcb_size (int): Size of the PCB structure.
    """

    def __init__(self, debugger, logger):
        self.debugger = debugger
        self.logger = logger
        self.target = debugger.GetSelectedTarget()
        self.process = self.target.GetProcess()
        self.entries = []
        self.debug_pcb = None
        self.thread_active = 0
        self.thread_list_addr = 0

        self.pcb_type = self.target.FindFirstType("QURTK_pcb_t")
        if not self.pcb_type.IsValid():
            self.logger.error("Failed to find type QURTK_pcb_t")
        self.pcb_size = self.pcb_type.GetByteSize()
   
    def resolve_state(self, state_val):
        """
        Resolve the numeric state value to a human-readable string.

        Parameters:
            state_val (int): Numeric state value.

        Returns:
            str: Human-readable state name.
        """
        return {
            1: "RUNNING",
            5: "FREEZE",
            6: "CAPTURE",
            7: "KILL",
            8: "EXITED"
        }.get(state_val, "UNKNOWN")

    def resolve_name_from_clients(self, asid):
        """
        Resolves the client name associated with a given ASID (Address Space Identifier).

        This method traverses the linked list of client structures in memory to find a match
        for the provided ASID. If ASID is 0, it returns the name of the local client.
        Otherwise, it searches through user clients and returns the name of the matching client.

        Parameters:
            asid (int): The Address Space Identifier to match against client structures.

        Returns:
            str: The name of the client if found, otherwise an empty string.
        """
        local_client_syms = self.target.FindSymbols("localclient")
        if not local_client_syms:
            self.logger.warning("Symbol 'localclient' not found")
            return ""

        local_client_addr = local_client_syms[0].symbol.GetStartAddress().GetLoadAddress(self.target)
        local_client_type = self.target.FindFirstType("struct qurtos_local_client")
        user_client_type = self.target.FindFirstType("struct qurtos_user_client")
        client_info_type = self.target.FindFirstType("struct qurtos_client_info")
        space_type = self.target.FindFirstType("struct space")

        off_clientinfo = get_struct_field_offset(local_client_type, "clientinfo", self.logger)
        off_pclient_next = get_struct_field_offset(local_client_type, "pClient_next", self.logger)
        off_attr = get_struct_field_offset(client_info_type, "attr", self.logger)
        off_space = get_struct_field_offset(client_info_type, "space", self.logger)
        off_client_asid = get_struct_field_offset(user_client_type, "asid", self.logger)
        off_usr_client_info = get_struct_field_offset(user_client_type, "client_info", self.logger)
        off_usr_next = get_struct_field_offset(user_client_type, "pClient_next", self.logger)
        off_id = get_struct_field_offset(space_type, "id", self.logger)

        if asid == 0:
            name_addr = read_memory_uint32(self.process, self.logger, local_client_addr + off_clientinfo + off_attr)
            return read_memory_cstring(self.process, self.logger, name_addr, 32)

        pclient = read_memory_uint32(self.process, self.logger, local_client_addr + off_pclient_next)
        while pclient:
            client_asid = read_memory_uint32(self.process, self.logger, pclient + off_client_asid)
            if client_asid == asid:
                name_addr = read_memory_uint32(self.process, self.logger, pclient + off_usr_client_info + off_attr)
                return read_memory_cstring(self.process, self.logger, name_addr, 32)
            else:
                space_id = read_memory_uint32(self.process, self.logger, pclient + off_usr_client_info + off_space)
                id_val = read_memory_uint32(self.process, self.logger, space_id + off_id)
                if id_val == asid:
                    name_addr = read_memory_uint32(self.process, self.logger, pclient + off_usr_client_info + off_attr)
                    return read_memory_cstring(self.process, self.logger, name_addr, 32)
            pclient = read_memory_uint32(self.process, self.logger, pclient + off_usr_next)
        return ""

    def read_processes(self):
        """
        Read and populate the list of processes from memory.

        This method reads PCB entries, extracts relevant fields, and stores
        them as ProcessEntry objects in the `entries` list.
        """
        max_proc = read_4_byte_symbol_value(self.target, "QURTK_MAX_USER_PROCESSES", self.logger)
        pcb_base = read_4_byte_symbol_value(self.target, "QURTK_pcb", self.logger)

        if max_proc is None or pcb_base is None:
            self.logger.error("Failed to read process list symbols.")
            return

        offsets = {
            "asid": get_struct_field_offset(self.pcb_type, "asid", self.logger),
            "pid": get_struct_field_offset(self.pcb_type, "pid", self.logger),
            "name": get_struct_field_offset(self.pcb_type, "name", self.logger),
            "state_a": get_struct_field_offset(self.pcb_type, "state_a", self.logger),
            "pclass": get_struct_field_offset(self.pcb_type, "pclass", self.logger),
            "thread_count": get_struct_field_offset(self.pcb_type, "thread_count", self.logger),
            "threads_active": get_struct_field_offset(self.pcb_type, "threads_active", self.logger),
            "thread_list": get_struct_field_offset(self.pcb_type, "thread_list", self.logger)
        }

        for i in range(max_proc):
            pcb_addr = pcb_base + i * self.pcb_size
            thread_num = read_memory_uint32(self.process, self.logger, pcb_addr + offsets["thread_count"])
            if thread_num == 0:
                continue

            asid = read_memory_uint32(self.process, self.logger, pcb_addr + offsets["asid"])
            pclass = read_memory_uint32(self.process, self.logger, pcb_addr + offsets["pclass"])
            state_val = read_memory_uint32(self.process, self.logger, pcb_addr + offsets["state_a"])
            pid = read_memory_uint32(self.process, self.logger, pcb_addr + offsets["pid"])
            name_addr = read_memory_uint32(self.process, self.logger, pcb_addr + offsets["name"])

            if state_val in (5, 6):
                self.debug_pcb = pcb_addr

            name = read_memory_cstring(self.process, self.logger, name_addr, 32) if name_addr else self.resolve_name_from_clients(asid)
            ptype = read_memory_cstring(self.process, self.logger, pclass, 16) if pclass else ""
            state = self.resolve_state(state_val)

            self.entries.append(ProcessEntry(pcb_addr, name, ptype, state, asid, pid))

        if self.debug_pcb:
            self.thread_active = read_memory_uint32(self.process, self.logger, self.debug_pcb + offsets["threads_active"])
            self.thread_list_addr = read_memory_uint32(self.process, self.logger, self.debug_pcb + offsets["thread_list"])

   
    def display_processes(self):
        """
        Display the list of processes in a formatted table using the logger.
        """
        self.logger.info("PCB      | NAME             | TYPE       | STATE                | ASID      | PID")
        for entry in self.entries:
            self.logger.info(str(entry))

    def display_debug_threads(self):
        """
        Display threads of the debugged process that may be stuck or frozen.

        This method checks the thread list and prints thread states such as
        FROZEN, CAPTURED, FREEZE REQUESTED, or RUNNING.
        """
        if not self.debug_pcb or self.thread_active == 0:
            self.logger.info("All threads seem to be in right state. Check why ProcessReaper not able to proceed.")
            return

        self.logger.info(f"PCB with ASID {read_memory_uint32(self.process, self.logger, self.debug_pcb):08X} could be stuck on exit")
        self.logger.info(f"Active threads are {self.thread_active}\n")
        self.logger.info("TCB        | NAME            | STATE")

        thread_list = self.thread_list_addr
        visited = set()

        thread_list_type = self.target.FindFirstType("QURTK_thread_list")
        thread_ctx_type = self.target.FindFirstType("QURTK_thread_context")

        offset_tcb = get_struct_field_offset(thread_list_type, "tcb", self.logger)
        offset_cfg = get_struct_field_offset(thread_ctx_type, "cfg", self.logger)
        offset_ugp = get_struct_field_offset(thread_ctx_type, "ugp", self.logger)

        while thread_list and thread_list not in visited:
            visited.add(thread_list)
            tcb = read_memory_uint32(self.process, self.logger, thread_list + offset_tcb)
            ugp = read_memory_uint32(self.process, self.logger, tcb + offset_ugp)
            name = get_task_name(tcb, self.target, self.logger) or ""

            cfg = read_memory_uint32(self.process, self.logger, tcb + offset_cfg)
            if cfg & FROZEN:
                state = "FROZEN"
            elif cfg & MARKED_FOR_KILL:
                state = "CAPTURED"
            elif cfg & FREEZE_REQUESTED:
                state = "FREEZE REQUESTED"
            else:
                state = "RUNNING"

            self.logger.info(f"{tcb:08X} | {name:<15} | {state}")
            thread_list = read_memory_uint32(self.process, self.logger, thread_list)

def FetchProcessList(debugger, logger):
    """
    Fetch and display the process list and debug thread information.

    Parameters:
        debugger: LLDB debugger instance.
        logger: Logger instance for logging messages.

    This function initializes a ProcessList object, reads processes,
    displays them, and shows debug thread states. It also handles
    exceptions and restores the original logger formatter.
    """
    original_formatter = save_formatter(logger)
    plist = ProcessList(debugger, logger)
    plist.read_processes()
    plist.display_processes()
    plist.display_debug_threads()
    restore_formatter(logger, original_formatter)

register_command('processlist', FetchProcessList)