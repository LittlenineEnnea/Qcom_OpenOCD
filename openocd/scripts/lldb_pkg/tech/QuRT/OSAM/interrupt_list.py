# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file interrupt_list.py
#
# @section Description
# This file contains the implementation of the interrupt list class
#

import lldb
import time
import logging
import struct

try:
    from lldb_pkg.tech.QuRT.OSAM.command_registry import register_command
    from utils.debug_context import logger
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
        utils_user_exception
    )

class QURTK_int_G:
    """
    Class to handle interrupt data and analysis for QURTK.
    """

    class LogEntry:
        """
        Represents a log entry for an interrupt.
        """

        def __init__(self):
            self.irq = None
            self.placement = None
            self.status = None
            self.type = None
            self.icb = None
            self.word0 = None
            self.word1 = None
            self.word2 = None
            self.cb = None
            self.ack = None
            self.isr_prio = None
            self.int_prio = None
            self.trigger = None
            self.valid_next = None
            self.next_intno = None

        def __str__(self):
            """
            Returns a string representation of the log entry.

            Returns:
                list: A list of string values representing the log entry.
            """
            return [
                f"{self.irq}",
                f"{self.placement}",
                f"{self.status}",
                f"{self.type}",
                f"{self.icb}",
                f"{self.word0}",
                f"{self.word1}",
                f"{self.word2}",
                f"{self.cb}",
                f"{self.ack}",
                f"{self.isr_prio}",
                f"{self.int_prio}",
                f"{self.trigger}",
                f"{self.valid_next}",
                f"{self.next_intno}"
            ]

    class QURTK_icb_entry:
        """
        Represents an ICB (Interrupt Control Block) entry.
        """

        def __init__(self):
            self.word0_data = None
            self.word1_data = None
            self.word2_data = None
            self.icb_location = None

    class QURTK_int_lookup_data:
        """
        Represents interrupt lookup data.
        """

        def __init__(self):
            self.max_configured = None
            self.total_configured = None
            self.configured_addr = None
            self.configured_raw_bytes = bytearray()
            self.icb_table_addr = None
            self.icb_table_raw_bytes = bytearray()
            self.icb_data = []  # List of QURTK_icb_entry objects

    class QURTK_interrupt_lookup:
        """
        Represents interrupt lookup data for TCM and DDR.
        """

        def __init__(self):
            self.tcm_lookup_addr = None
            self.tcm_lookup_data = QURTK_int_G.QURTK_int_lookup_data()
            self.ddr_lookup_addr = None
            self.ddr_lookup_data = QURTK_int_G.QURTK_int_lookup_data()

    def __init__(self):
        """
        Initializes the QURTK_int_G instance.
        """
        self.headers = [
            "IRQ", "Placement", "Status", "Type", "ICB", "Word0", "Word1", "Word2", "CB",
            "ACK", "ISR_PRIO", "INT_PRIO", "Trigger", "VALID_NEXT", "NEXT_INTNO"
        ]
        self.table = []
        self.max_configured = None
        self.max_island_int_allowed = None
        self.uepc_addr = None
        self.uepc_raw_bytes = bytearray()
        self.uepc_list = []  # List of interrupt states
        self.lookup = QURTK_int_G.QURTK_interrupt_lookup()
        self.log_entry_temp = QURTK_int_G.LogEntry()
        self.log_data = []
        self.ddr_icb_index = 0
        self.tcm_icb_index = 0

    def extract_log(self, target, icb_struct_size, irq_num,uepc_val,placement,icb_struct_type,int_trigger_addr):
    
        log_entry_temp = QURTK_int_G.LogEntry()
        if(placement == "TCM"):
            lookup_data = self.lookup.tcm_lookup_data
            icb_index = self.tcm_icb_index
        else:
            lookup_data = self.lookup.ddr_lookup_data
            icb_index = self.ddr_icb_index

        INTERRUPT_CFG_INT_TYPE_FASTINT = 0
        INTERRUPT_CFG_INT_TYPE_ISR = 1
        INTERRUPT_CFG_INT_TYPE_IST = 2
        INTERRUPT_CFG_INT_TYPE_KERNEL = 3

        config_chunk = int.from_bytes(
                        lookup_data.configured_raw_bytes[
                            ((irq_num // 32) * 8):(((irq_num // 32) * 8) + 8)
                        ],
                        byteorder='little'
                    )
        conf = config_chunk & (1 << (irq_num & 0x1F))
        if conf != 0:
            icb_instance = create_value_from_data(
                target, logger, "icb_instance",
                lookup_data.icb_table_raw_bytes[
                    icb_index:icb_index + icb_struct_size
                ],
                icb_struct_type
            )
            word0 = icb_instance.GetChildMemberWithName("generic_data").GetChildMemberWithName("word0").GetValueAsUnsigned()
            if word0 != 0:
                enabled = (uepc_val & 0x04) >> 2
                int_type = icb_instance.GetChildMemberWithName("int_type").GetValueAsUnsigned()
                ack = icb_instance.GetChildMemberWithName("ack").GetValueAsUnsigned()
                valid_next = icb_instance.GetChildMemberWithName("valid_next").GetValueAsUnsigned()
                prio = icb_instance.GetChildMemberWithName("prio").GetValueAsUnsigned()
                next_intno = icb_instance.GetChildMemberWithName("next_intno").GetValueAsUnsigned()
                intno = icb_instance.GetChildMemberWithName("intno").GetValueAsUnsigned()
                word1 = icb_instance.GetChildMemberWithName("generic_data").GetChildMemberWithName("word1").GetValueAsUnsigned()
                word2 = icb_instance.GetChildMemberWithName("generic_data").GetChildMemberWithName("word2").GetValueAsUnsigned()

                log_entry_temp.irq = f"{irq_num}"
                log_entry_temp.placement = placement

                if enabled != 0:
                    log_entry_temp.status = "Enabled"

                    if int_type == INTERRUPT_CFG_INT_TYPE_FASTINT:
                        log_entry_temp.type = "fastINT"
                        log_entry_temp.icb = hex(int(lookup_data.icb_table_addr) + icb_index)
                        log_entry_temp.word0 = f"{read_symbol_name(word0, target)}"
                        log_entry_temp.word1 = hex(word1)
                        log_entry_temp.word2 = f"{read_symbol_name(word2, target)}"

                    elif int_type == INTERRUPT_CFG_INT_TYPE_ISR:
                        log_entry_temp.type = "ISR"
                        log_entry_temp.icb = hex(int(lookup_data.icb_table_addr) + icb_index)
                        log_entry_temp.word0 = get_task_name(word0, target, logger)
                        log_entry_temp.word1 = hex(word1)
                        log_entry_temp.word2 = f"{read_symbol_name(word2, target)}"
                        log_entry_temp.cb = hex(word2)

                        if ack != 0:
                            log_entry_temp.ack = "WithACK"
                        else:
                            log_entry_temp.ack = "DelayACK"

                        value_bytes = target.GetProcess().ReadMemory(((word0 & 0xFFFFFFFC) + 20), 4, lldb.SBError())
                        int.from_bytes(value_bytes, byteorder='little')

                        log_entry_temp.isr_prio = hex((int.from_bytes(value_bytes, byteorder='little')) & 0xFF)
                        log_entry_temp.int_prio = hex(prio)

                        value_bytes = target.GetProcess().ReadMemory(int_trigger_addr + irq_num, 4, lldb.SBError())
                        trigger = int.from_bytes(value_bytes, byteorder='little')

                        log_entry_temp.trigger = trigger & 0x01
                        log_entry_temp.next_intno = next_intno
                        log_entry_temp.valid_next = valid_next

                    elif int_type == INTERRUPT_CFG_INT_TYPE_IST:
                        log_entry_temp.type = "IST"
                        log_entry_temp.icb = hex(int(lookup_data.icb_table_addr) + icb_index)
                        log_entry_temp.word0 = hex(word0)
                        log_entry_temp.word1 = hex(word1)
                        log_entry_temp.word2 = get_task_name(word2, target, logger)

                    elif int_type == INTERRUPT_CFG_INT_TYPE_KERNEL:
                        log_entry_temp.type = "Kernel"
                        log_entry_temp.icb = hex(int(lookup_data.icb_table_addr) + icb_index)
                        log_entry_temp.word0 = hex(word0)
                        log_entry_temp.word1 = hex(word1)
                        log_entry_temp.word2 = hex(word2)

                else:
                    log_entry_temp.status = "Disabled"

                self.log_data.append(log_entry_temp.__str__())
            if placement == "TCM":
                self.tcm_icb_index += icb_struct_size
            else:
                self.ddr_icb_index += icb_struct_size

    def analyze(self, target):
        """
        Analyzes the interrupt data for the given target.

        Parameters:
        target (lldb.SBTarget): The target to analyze.
        """
        self.ddr_icb_index = 0
        self.tcm_icb_index = 0

        int_trigger_addr = read_4_byte_symbol_value(target, "qdsp6ss_l2vic_int_type", logger)

        icb_struct_size = None
        icb_struct_type = target.FindFirstType("QURTK_icb_t")
        if not icb_struct_type:
            raise utils_user_exception("Type QURTK_icb_t not found")
        else:
            icb_struct_size = icb_struct_type.GetByteSize()

        for irq_num in range(0, int(self.max_configured)):
            byte = self.uepc_raw_bytes[irq_num // 2]
            uepc_val = (byte >> (4 * (irq_num % 2))) & 0x0F

            if uepc_val & 0x01:
                if uepc_val & 0x02:
                    self.extract_log(target, icb_struct_size, irq_num,uepc_val,"TCM",icb_struct_type,int_trigger_addr)
                else:
                    self.extract_log(target, icb_struct_size, irq_num,uepc_val,"DDR",icb_struct_type,int_trigger_addr)

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


def interrupt_list(debugger):
    """
    Main function to analyze and print interrupt data.

    Parameters:
    debugger (lldb.SBDebugger): The LLDB debugger instance.
    """
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()

    # Initialize the QURTK_int_G instance
    interrupt_global_data = QURTK_int_G()
    interrupt_global_addr = read_symbol_addr(target,"QURTK_intG",logger)

    struct_type = target.FindFirstType("QURTK_int_t")
    if not struct_type:
        raise utils_user_exception(f"Type QURTK_int_t not found exception raised")
    else:
        struct_size = struct_type.GetByteSize()
        interrupt_global_data_raw = raw_read_memory(process, logger, interrupt_global_addr, struct_size)
        if interrupt_global_data_raw is None:
            raise utils_user_exception(f"Could not read QURTK_intG raw exception raised")
        else:
            value = create_value_from_data(target, logger, "value", interrupt_global_data_raw, struct_type)

            interrupt_global_data.max_configured = value.GetChildMemberWithName("max_configured").GetValueAsUnsigned()
            interrupt_global_data.max_island_int_allowed = value.GetChildMemberWithName("max_island_int_allowed").GetValueAsUnsigned()
            interrupt_global_data.uepc_addr = value.GetChildMemberWithName("uepc").GetValueAsUnsigned()
            interrupt_global_data.lookup.tcm_lookup_addr = value.GetChildMemberWithName("lookup").GetChildMemberWithName("tcm").GetValueAsUnsigned()
            interrupt_global_data.lookup.ddr_lookup_addr = value.GetChildMemberWithName("lookup").GetChildMemberWithName("ddr").GetValueAsUnsigned()

            interrupt_global_data.uepc_raw_bytes = raw_read_memory(
                process, logger, interrupt_global_data.uepc_addr, (int(interrupt_global_data.max_configured) + 1) // 2
            )
            if interrupt_global_data.uepc_raw_bytes is None:
                raise utils_user_exception(f"Could not read uepc raw exception raised")

            icb_struct_size = None
            icb_struct_type = target.FindFirstType("QURTK_icb_t")
            if not icb_struct_type:
                raise utils_user_exception(f"Type QURTK_icb_t not found exception raised")

            else:
                icb_struct_size = icb_struct_type.GetByteSize()
            lookup_struct_type = target.FindFirstType("QURTK_int_lookup_data_t")
            if not lookup_struct_type:
                raise utils_user_exception(f"Type QURTK_int_lookup_data_t not found exception raised")
            else:
                lookup_size = lookup_struct_type.GetByteSize()
                lookup_data_raw_tcm = raw_read_memory(process, logger, interrupt_global_data.lookup.tcm_lookup_addr, lookup_size)
                if lookup_data_raw_tcm is None:
                    raise utils_user_exception(f"Could not read QURTK_int_lookup_t raw tcm exception raised")
                else:
                    lookup_value_tcm = create_value_from_data(target, logger, "lookup_value_tcm", lookup_data_raw_tcm, lookup_struct_type)
                    interrupt_global_data.lookup.tcm_lookup_data.max_configured = lookup_value_tcm.GetChildMemberWithName("max_configured").GetValueAsUnsigned()
                    interrupt_global_data.lookup.tcm_lookup_data.total_configured = lookup_value_tcm.GetChildMemberWithName("total_configured").GetValueAsUnsigned()
                    interrupt_global_data.lookup.tcm_lookup_data.configured_addr = lookup_value_tcm.GetChildMemberWithName("configured").GetValueAsUnsigned()
                    interrupt_global_data.lookup.tcm_lookup_data.icb_table_addr = lookup_value_tcm.GetChildMemberWithName("icb_table").GetValueAsUnsigned()

                    if interrupt_global_data.lookup.tcm_lookup_data.max_configured != 0:
                        interrupt_global_data.lookup.tcm_lookup_data.configured_raw_bytes = raw_read_memory(
                            process, logger, (interrupt_global_data.lookup.tcm_lookup_data.configured_addr),
                            ((int(interrupt_global_data.lookup.tcm_lookup_data.max_configured) * 2) // 8) + 8
                        )
                        if interrupt_global_data.lookup.tcm_lookup_data.configured_raw_bytes is None:
                            raise utils_user_exception(f"Could not read QURTK_int_lookup_t raw tcm exception raised")
                        interrupt_global_data.lookup.tcm_lookup_data.icb_table_raw_bytes = raw_read_memory(
                            process, logger, (interrupt_global_data.lookup.tcm_lookup_data.icb_table_addr),
                            (int(interrupt_global_data.lookup.tcm_lookup_data.total_configured) * icb_struct_size)
                        )
                        if interrupt_global_data.lookup.tcm_lookup_data.icb_table_raw_bytes is None:
                            raise utils_user_exception(f"Could not read ICB table raw tcm exception raised")

                lookup_data_raw_ddr = raw_read_memory(process, logger, interrupt_global_data.lookup.ddr_lookup_addr, lookup_size)
                if lookup_data_raw_ddr is None:
                    raise utils_user_exception(f"Could not read QURTK_int_lookup_t raw ddr exception raised")
                else:
                    lookup_value_ddr = create_value_from_data(target, logger, "lookup_value_ddr", lookup_data_raw_ddr, lookup_struct_type)
                    interrupt_global_data.lookup.ddr_lookup_data.max_configured = lookup_value_ddr.GetChildMemberWithName("max_configured").GetValueAsUnsigned()
                    interrupt_global_data.lookup.ddr_lookup_data.total_configured = lookup_value_ddr.GetChildMemberWithName("total_configured").GetValueAsUnsigned()
                    interrupt_global_data.lookup.ddr_lookup_data.configured_addr = lookup_value_ddr.GetChildMemberWithName("configured").GetValueAsUnsigned()
                    interrupt_global_data.lookup.ddr_lookup_data.icb_table_addr = lookup_value_ddr.GetChildMemberWithName("icb_table").GetValueAsUnsigned()

                    if interrupt_global_data.lookup.ddr_lookup_data.max_configured != 0:
                        interrupt_global_data.lookup.ddr_lookup_data.configured_raw_bytes = raw_read_memory(
                            process, logger, (interrupt_global_data.lookup.ddr_lookup_data.configured_addr),
                            ((int(interrupt_global_data.lookup.ddr_lookup_data.max_configured) * 2) // 8) + 8
                        )
                        if interrupt_global_data.lookup.ddr_lookup_data.configured_raw_bytes is None:
                            raise utils_user_exception(f"Could not read QURTK_int_lookup_t raw ddr exception raised")
                        interrupt_global_data.lookup.ddr_lookup_data.icb_table_raw_bytes = raw_read_memory(
                            process, logger, (interrupt_global_data.lookup.ddr_lookup_data.icb_table_addr),
                            (int(interrupt_global_data.lookup.ddr_lookup_data.total_configured) * icb_struct_size)
                        )
                        if interrupt_global_data.lookup.ddr_lookup_data.icb_table_raw_bytes is None:
                            raise utils_user_exception(f"Could not read ICB table raw ddr exception raised")

                interrupt_global_data.analyze(target)

    # Print the table
    interrupt_global_data.print_table()
    
register_command('interruptlist', interrupt_list)