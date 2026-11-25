# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file futexlist.py
#
# @section Description
# This file contains the implementation of the futexlist class
#

import lldb

# Constants for futex analysis and memory parsing
SEARCH_DEPTH = 16            # Depth for deadlock detection traversal
MAX_WAITERS_PER_ENTRY = 32   # Max number of waiters to follow per futex

try:
    from lldb_pkg.tech.QuRT.OSAM.command_registry import register_command
    from lldb_pkg.tech.QuRT.OSAM.utils import (
        create_value_from_data,
        raw_read_memory,
        save_formatter,
        restore_formatter,
        read_4_byte_symbol_value,
        get_task_name,
        ONE_KB
    )
except:
    from command_registry import register_command
    from utils import (
        create_value_from_data,
        raw_read_memory,
        save_formatter,
        restore_formatter,
        read_4_byte_symbol_value,
        get_task_name,
        ONE_KB
    )


class FutexEntry:
    """
    Represents a single futex entry parsed from memory.

    Attributes:
        target (lldb.SBTarget): The current LLDB target.
        logger (object): The logger object for debugging information.
        obj_addr (int): Object address associated with the futex.
        holder_tcb (int): TCB address of the current holder.
        queue (int): Address of the waiting thread queue.
        futex_addr (int): The memory address of the futex entry.
        owner (int): The owner ID of the futex.
        object_type (str): The type of futex (e.g., "mutex", "pi mutex", "signal").
        owner_str (str): The resolved name of the owner task.
        waiters (list): List of waiter thread names.
    """

    def __init__(self, target, logger):
        """
        Initializes a FutexEntry instance.

        Args:
            target (lldb.SBTarget): The current LLDB target.
            logger (object): The logger object for debugging information.
        """
        self.target = target
        self.logger = logger
        self.obj_addr = 0
        self.holder_tcb = 0
        self.queue = 0
        self.futex_addr = 0
        self.owner = 0
        self.object_type = ""
        self.owner_str = ""
        self.waiters = []

    def parse_futex_data(self, process, data, futex_addr):
        """
        Parses raw memory data into a futex structure and populates the entry's attributes.

        Args:
            process (lldb.SBProcess): The current LLDB process.
            data (bytes): Raw memory data of the futex entry.
            futex_addr (int): The memory address from which the data was read.

        Returns:
            bool: True if parsing was successful, False otherwise.
        """
        self.futex_addr = futex_addr
        futex_type = self.target.FindFirstType("QURTK_futex_entry_t")
        value = create_value_from_data(self.target, self.logger, "futex", data, futex_type)
        if not value:
            return False

        self.queue = value.GetChildMemberWithName("queue").GetValueAsUnsigned()
        self.obj_addr = value.GetChildMemberWithName("obj_addr").GetValueAsUnsigned()
        self.holder_tcb = value.GetChildMemberWithName("holder_tcb").GetValueAsUnsigned()

        if self.obj_addr:
            holder_data = raw_read_memory(process, self.logger, self.obj_addr, 4)
            if holder_data:
                self.owner = int.from_bytes(holder_data, byteorder='little')

        return True

    def resolve_owner(self, process):
        """
        Determines the type of futex (mutex, pi mutex, signal) and resolves the owner name.

        Args:
            process (lldb.SBProcess): The current LLDB process.
        """
        if self.holder_tcb:
            self.object_type = "pi mutex"
            self.owner_str = "N/A" if self.owner == 0xfe else get_task_name(self.holder_tcb, self.target, self.logger) or " "
        elif self.owner == 0xfe:
            self.object_type = "mutex"
            self.owner_str = "N/A"
        elif self.owner:
            task_name = get_task_name(self.owner, self.target, self.logger)
            self.object_type = "mutex" if task_name else "signal"
            self.owner_str = task_name or ""
        else:
            self.object_type = "signal"
            self.owner_str = ""

    def __str__(self):
        """
        Returns a formatted string representation of the futex entry.

        Returns:
            str: A string showing futex address, object type, owner, object address, and waiting threads.
        """
        waiters_str = " -> ".join(self.waiters) + " -> NULL"
        return f"{self.futex_addr:08X}      {self.object_type:<13} {self.owner_str:<16} {self.obj_addr:08X}     {waiters_str}"


class FutexList:
    """
    Handles reading the futex buffer, creating futex entries, and detecting deadlocks.

    Attributes:
        debugger (lldb.SBDebugger): The current LLDB debugger instance.
        logger (object): The logger object for debugging information.
        target (lldb.SBTarget): The selected LLDB target.
        process (lldb.SBProcess): The current LLDB process.
        entries (list[FutexEntry]): A list of FutexEntry objects.
        entry_size (int): The byte size of a QURTK_futex_entry_t structure.
    """

    def __init__(self, debugger, logger):
        """
        Initializes a FutexList instance.

        Args:
            debugger (lldb.SBDebugger): The current LLDB debugger instance.
            logger (object): The logger object for debugging information.
        """
        self.debugger = debugger
        self.logger = logger
        self.target = debugger.GetSelectedTarget()
        self.process = self.target.GetProcess()
        self.entries = []
        self.entry_size = self.target.FindFirstType("QURTK_futex_entry_t").GetByteSize()

    def read_futex_buffer(self):
        """
        Reads the futex table from memory, parses each entry, and populates the `entries` list.

        This method reads `QURTK_futex_objs` and `QURTK_futex_objs_end` symbols to determine
        the memory range of the futex table. It then reads the memory in chunks and
        creates `FutexEntry` objects for each valid entry.
        """
        futex_table_addr = read_4_byte_symbol_value(self.target, "QURTK_futex_objs", self.logger)
        futex_table_end = read_4_byte_symbol_value(self.target, "QURTK_futex_objs_end", self.logger)
        if futex_table_addr is None or futex_table_end is None:
            self.logger.info("Sorry: Couldn't display futex list")
            return

        total_size = futex_table_end - futex_table_addr + 1
        futex_table_data = bytearray(total_size)
        offset = 0
        while offset < total_size:
            size = min(ONE_KB, total_size - offset)
            data = self.process.ReadMemory(futex_table_addr + offset, size, lldb.SBError())
            futex_table_data[offset:offset + size] = data
            offset += size

        for i in range(0, len(futex_table_data), self.entry_size):
            entry_data = futex_table_data[i:i + self.entry_size]
            if len(entry_data) < self.entry_size:
                continue
            futex_addr = futex_table_addr + i
            entry = FutexEntry(self.target, self.logger)
            if entry.parse_futex_data(self.process, entry_data, futex_addr):
                self.entries.append(entry)

    def detect_deadlock(self):
        """
        Detects circular wait conditions (deadlocks) among futex holders.

        This method builds a resource matrix based on futex entries and uses a search
        algorithm to identify potential deadlocks. It logs detected deadlocks to the logger.
        """
        res_matrix = []
        deadlock_path = [0] * SEARCH_DEPTH

        def is_holder(addr):
            """
            Checks if a given address is a holder in the resource matrix.

            Args:
                addr (int): The address to check.

            Returns:
                bool: True if the address is a holder, False otherwise.
            """
            return any(entry['holder'] == addr for entry in res_matrix)

        def is_deadlock(src, dest):
            """
            Checks for a deadlock scenario between two tasks.

            Args:
                src (int): The source task's address (initiator of the potential deadlock).
                dest (int): The destination task's address (resource held by source, needed by destination).

            Returns:
                bool: True if a deadlock is detected, False otherwise.
            """
            for i in range(SEARCH_DEPTH):
                deadlock_path[i] = 0
            for i in range(SEARCH_DEPTH):
                for entry in res_matrix:
                    if entry['holder'] and src in entry['waiters']:
                        if dest == entry['holder']:
                            deadlock_path[i] = dest
                            return True
                        src = entry['holder']
                        deadlock_path[i] = src
                        break
                else:
                    return False
            return False

        for entry in self.entries:
            if entry.object_type in ["mutex", "pi mutex"] and entry.owner:
                holder = entry.holder_tcb if entry.object_type == "pi mutex" else entry.owner
                res_matrix.append({
                    'holder': holder,
                    'mutex': entry.obj_addr,
                    'waiters': list(entry.waiters),
                    'wait_count': len(entry.waiters)
                })

        self.logger.info("\n" + "=" * 60)
        self.logger.info("Deadlocks detected:")
        self.logger.info("=" * 60)

        deadlock_count = 0
        for entry in res_matrix:
            if entry['holder'] == 0:
                continue
            for waiter in entry['waiters']:
                if not is_holder(waiter):
                    continue
                if is_deadlock(entry['holder'], waiter):
                    deadlock_count += 1
                    msg = f"Deadlock scenario {deadlock_count}: {waiter:x} => {entry['holder']:x}"
                    for pid in deadlock_path:
                        if pid:
                            msg += f" => {pid:x}"
                    self.logger.info(msg)

        if deadlock_count == 0:
            self.logger.info("None")

    def fetch_and_display(self):
        """
        Main entry point to fetch and display the futex list and detect deadlocks.

        This method orchestrates the reading of the futex buffer, resolving owners,
        identifying waiting threads, and then displaying the information along
        with any detected deadlocks.
        """
        self.read_futex_buffer()

        self.logger.info("Futex_addr    Object_type       Owner        Obj_Addr       Waiting_threads")

        for entry in self.entries:
            if entry.queue == 0:
                continue
            entry.resolve_owner(self.process)

            waiter = entry.queue
            y = 0
            while waiter and y < MAX_WAITERS_PER_ENTRY:
                task_name = get_task_name(waiter, entry.target, self.logger)
                entry.waiters.append(task_name)
                waiter_data = raw_read_memory(self.process, self.logger, waiter, 4)
                if not waiter_data:
                    break
                waiter = int.from_bytes(waiter_data, byteorder='little')
                y += 1

            self.logger.info(str(entry))

        self.detect_deadlock()
def FetchFutexList(debugger, logger):
    """
    Main entry point to fetch and display futex list and detect deadlocks.

    This function is intended to be called from an LLDB command.

    Args:
        debugger (lldb.SBDebugger): The current LLDB debugger instance.
        logger (object): The logger object for logging output.
    """
    original_formatter = save_formatter(logger)
    futex_list = FutexList(debugger, logger)
    futex_list.fetch_and_display()
    restore_formatter(logger, original_formatter)
    
register_command('futexlist', FetchFutexList)