# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file mergebuffer.py
#
# @section Description
# This file contains the implementation of the mergebuffer class.
#

import lldb

try:
    from lldb_pkg.tech.QuRT.OSAM.command_registry import register_command
    from lldb_pkg.tech.QuRT.OSAM.utils import (
        raw_read_memory,
        save_formatter,
        restore_formatter,
        read_4_byte_symbol_value,
        read_memory_uint32,
        read_symbol_addr,
        get_struct_field_offset,
        create_value_from_data,
        utils_user_exception,
    )
except Exception:
    from command_registry import register_command
    from utils import (
        raw_read_memory,
        save_formatter,
        restore_formatter,
        read_4_byte_symbol_value,
        read_memory_uint32,
        read_symbol_addr,
        get_struct_field_offset,
        create_value_from_data,
        utils_user_exception,
    )


# OpenOCD GDB-remote read packet ceiling per request.
ONE_KB = 0x400


class Packet:
    """
    Represents a single trace packet decoded from a per-hthread trace buffer.
    """

    def __init__(self):
        """
        Initialize a new Packet with default values.
        """
        self.pcycle_low = 0
        self.pcycle_high = 0
        self.trace_type = 0
        self.packet_size = 0
        self.param0 = 0
        self.param1 = 0
        self.hthread = None
        self.pcycle = 0
        self.event = ""
        self.tracing_data = ""

    def __str__(self):
        """
        Return a formatted string representation of the packet.

        :return: String representation of the packet.
        """
        return f"{self.hthread} {self.pcycle} {self.event} {self.tracing_data}"

    def parse_from_bytes(self, target, logger, packet_bytes, hthread):
        """
        Decode a single packet from a raw byte slice using the
        QURTK_trace_packet_t DWARF type.

        :param target: The lldb target object.
        :param logger: The logger object.
        :param packet_bytes: Raw bytes of the packet.
        :param hthread: HW thread index this packet belongs to.
        :return: True on success, False otherwise.
        """
        ptype = target.FindFirstType("QURTK_trace_packet_t")
        if not ptype.IsValid():
            return False
        value = create_value_from_data(target, logger, "pkt", packet_bytes, ptype)
        if value is None or not value.IsValid():
            return False
        self.pcycle_low = value.GetChildMemberWithName("pcycle_low").GetValueAsUnsigned()
        self.pcycle_high = value.GetChildMemberWithName("pcycle_high").GetValueAsUnsigned()
        self.trace_type = value.GetChildMemberWithName("trace_type").GetValueAsUnsigned()
        self.packet_size = value.GetChildMemberWithName("packet_size").GetValueAsUnsigned()
        self.param0 = value.GetChildMemberWithName("param0").GetValueAsUnsigned()
        self.param1 = value.GetChildMemberWithName("param1").GetValueAsUnsigned()
        self.pcycle = (self.pcycle_high << 32) | self.pcycle_low
        self.hthread = hthread
        return True

    def convert(self, logger):
        """
        Translate (trace_type, param0, param1) into a human-readable
        event description and tracing data.

        :param logger: The logger object (unused, kept for interface parity).
        """
        id_ = self.trace_type
        w0 = self.param0
        w1 = self.param1
        lower_w0 = w0 & 0xFFFF
        upper_w0 = (w0 & 0xFFFF0000) >> 16
        lower_w1 = w1 & 0xFFFF
        upper_w1 = (w1 & 0xFFFF0000) >> 16

        if id_ == 10 or id_ == 0:
            self.event = "Context Switch" if id_ == 0 else "VB Context Switch"
            if self.param1 == 0:
                self.tracing_data = "Idle Thread"
        elif id_ == 1:
            interrupt_events = {
                3: f"Interrupt Enable Int No = {w1}",
                4: f"Interrupt Disable Int No = {w1}",
                5: f"Interrupt Clear Int No = {w1}",
                6: f"Interrupt Enable_in_ack Int No = {w1}",
                7: f"Interrupt Disable_in_ack Int No = {w1}",
                9: f"Interrupt FastINT Wait Int No = {w1}",
                10: f"INT Arrived Int No = {w1}",
            }
            self.event = interrupt_events.get(w0, "")
            self.tracing_data = ""
        elif id_ == 2:
            self.event = "Futex Wait"
            self.tracing_data = f"futex_ID = {w0}, obj_addr = {w1}"
        elif id_ == 3:
            self.event = "Futex Resume"
            self.tracing_data = f"futex_ID = {w0}"
        elif id_ == 4:
            self.event = "Page Fault"
            self.tracing_data = f"faulting addr = {w1}"
        elif id_ == 5:
            if w0 == 0:
                self.event = "Event Wait"
                self.tracing_data = f"event id = {w1}"
            else:
                self.event = "Thread Resume"
                self.tracing_data = f"tcb = {w0}"
        elif id_ == 6:
            self.event = "Pi-Futex Wait"
            self.tracing_data = f"futex_ID = {w0}, obj_ptr = {w1}"
        elif id_ == 7:
            self.event = "Pi-Futex Resume"
            self.tracing_data = f"futex_ID = {w0}"
        elif id_ == 8:
            power_collapse_events = {
                1: "Power Collapse Prepared Successfully",
                2: "Power Collapse Shutdown Failed Interrupt pending",
                3: "Power Collapse Shutdown Successfully",
                4: "Power Collapse warm boot",
                5: "Power Collapse/ APCR exit",
                6: "Power Collapse Deep Sleep Shutdown sucessfully",
                7: "Power Collapse Quick Boot",
                8: "APCR enter successfully",
                11: "Island Enter",
                12: f"Island Exit Stage 1 DDR Access Duration {w1}",
                13: "Island Exit Stage 2",
                14: f"Island Exit Stage 1 completion duration {w1}",
                31: f"Futex Wait Entry    lock  = {w1}",
                32: f"Futex Resume Entry  lock  = {w1}",
                33: f"PI Futex Wait Entry lock  = {w1}",
                34: f"PI Futex Wait Resume Entry lock = {w1}",
            }
            self.event = power_collapse_events.get(w0, "")
        elif id_ == 9:
            self.event = "L1 Kernel Interrupt"
            self.tracing_data = f"num = {w0}"
        elif id_ == 11:
            isr_events = {
                1: f"ISR register                 Int No={upper_w0}, callback = {w1}",
                2: f"ISR De-register              Int No={w1}",
                6: f"Enable ISR Interrupt in Ack  Int No = {w1}",
                9: f"ISR Wait                     Int No = {w1}",
                11: f"ISR Queued                   Int No = {w1}",
                12: f"ISR from busy                Int No = {w1}",
                13: f"ISR from idle                Int No = {w1}",
                14: f"ISR Elevate Priority         Int No = {w1}",
                15: f"ISR Fired                    Int No = {upper_w0}, callback = {w1}",
                16: f"L2 Kernel Interrupt          num = {w1}",
            }
            self.event = isr_events.get(lower_w0, "")
        elif id_ == 12:
            ist_events = {
                1: f"Register IST Interrupt    Int No = {w1}",
                2: f"De-Register IST Interrupt Int No = {w1}",
                3: f"IST from busy             Int No = {w1}",
                4: f"IST from idle             Int No = {w1}",
            }
            self.event = ist_events.get(w0, "")
        elif id_ == 13:
            thread_event = {
                1: f"Thread Create with allocation  TCB = {w1}",
                2: f"Thread Exit with deallocation  TCB = {w1}",
            }
            self.event = thread_event.get(w0, "")
        elif id_ == 14:
            self.event = f"Kernel write word  address = {w0}, data = {w1}"
        elif id_ == 15:
            fastint_event = {
                1: f"Register FastInt Interrupt    Int No = {upper_w0}, callback = {w1}",
                2: f"De-Register FastInt Interrupt Int No = {w1}",
                12: f"FastInt from Busy            Int No = {w1}",
                13: f"FastInt from idle            Int No = {w1}",
                15: f"FastInt Interrupt Fired      Int No = {upper_w0}, callback = {w1}",
            }
            self.event = fastint_event.get(lower_w0, "")
        elif id_ == 25:
            self.event = f"Priority Set            thread = {w0}, prio = {w1}"
        elif id_ == 26:
            self.event = "Reschedule from Wait"
        elif id_ == 27:
            self.event = "Reschedule from LOW PRIO"
        elif id_ == 28:
            self.event = "Schedule Highest"
        elif id_ == 29:
            self.event = (
                f"Schedule New            thread = {w0}, "
                f"new_prio = {upper_w1}, me_prio = {lower_w1}"
            )
        elif id_ == 30:
            self.event = f"Schedule New from Sleep  tcb to schedule = {w0}"
        elif id_ == 31:
            self.event = f"Reschedule Bitmask       thread = {w0}"


class MergeBuffer:
    """
    Reads QuRT trace buffers from QURTK_trace_buffers, merges packets across
    HW threads by pcycle, and prints them.
    """

    def __init__(self, debugger, logger):
        """
        Initialize the MergeBuffer and resolve required QuRT debug types.

        :param debugger: The lldb debugger object.
        :param logger: The logger object.
        """
        self.debugger = debugger
        self.logger = logger
        self.target = debugger.GetSelectedTarget()
        self.process = self.target.GetProcess()

        self.trace_cfg_type = self.target.FindFirstType("QURTK_trace_buffer_ptr")
        if not self.trace_cfg_type or not self.trace_cfg_type.IsValid():
            self.logger.error("[mergebuffer] Type 'QURTK_trace_buffer_ptr' not found.")
            self.trace_cfg_type = None

        self.entry_type = self.target.FindFirstType("QURTK_trace_buffers_t")
        if not self.entry_type or not self.entry_type.IsValid():
            self.logger.error("[mergebuffer] Type 'QURTK_trace_buffers_t' not found.")
            self.entry_type = None

        self.packet_type = self.target.FindFirstType("QURTK_trace_packet_t")
        if not self.packet_type or not self.packet_type.IsValid():
            self.logger.error("[mergebuffer] Type 'QURTK_trace_packet_t' not found.")
            self.packet_type = None

    def _read_top_level_config(self):
        """
        Read the QURTK_trace_buffers global to get trace_buffer_ptr and
        trace_buffer_size.

        :return: Tuple (trace_buffer_ptr, trace_buffer_size) or (None, None).
        """
        if self.trace_cfg_type is None:
            return None, None
        try:
            cfg_addr = read_symbol_addr(self.target, "QURTK_trace_buffers", self.logger)
        except utils_user_exception:
            self.logger.error("[mergebuffer] Symbol 'QURTK_trace_buffers' not found.")
            return None, None

        off_ptr = get_struct_field_offset(self.trace_cfg_type, "trace_buffer_ptr", self.logger)
        off_size = get_struct_field_offset(self.trace_cfg_type, "trace_buffer_size", self.logger)
        ptr_v = read_memory_uint32(self.process, self.logger, cfg_addr + off_ptr)
        size_v = read_memory_uint32(self.process, self.logger, cfg_addr + off_size)
        return ptr_v, size_v

    def _read_descriptors(self, ptr_v, max_hthreads):
        """
        Read max_hthreads QURTK_trace_buffers_t descriptors starting at ptr_v.

        :param ptr_v: Address of the first descriptor.
        :param max_hthreads: Number of HW threads.
        :return: List of descriptor dicts.
        """
        if self.entry_type is None:
            return []

        entry_size = self.entry_type.GetByteSize() or 0
        if entry_size == 0:
            self.logger.error("[mergebuffer] Entry size 0 for QURTK_trace_buffers_t.")
            return []

        off_addr = get_struct_field_offset(self.entry_type, "trace_buffer_addr", self.logger)
        off_idx = get_struct_field_offset(self.entry_type, "index", self.logger)

        descriptors = []
        for h in range(max_hthreads):
            entry_addr = ptr_v + h * entry_size
            buf_addr = read_memory_uint32(self.process, self.logger, entry_addr + off_addr)
            buf_idx = read_memory_uint32(self.process, self.logger, entry_addr + off_idx)
            descriptors.append({
                "hthread": h,
                "trace_buffer_addr": buf_addr,
                "index": buf_idx,
            })
        return descriptors

    def _read_buffer(self, addr, size):
        """
        Read `size` bytes at `addr` in <=1KB chunks (OpenOCD packet limit).

        :param addr: Start address.
        :param size: Number of bytes to read.
        :return: Bytes object on success, None on failure.
        """
        if size == 0 or addr == 0:
            return None
        out = bytearray(size)
        read = 0
        while read < size:
            chunk = min(ONE_KB, size - read)
            data = raw_read_memory(self.process, self.logger, addr + read, chunk)
            if data is None:
                return None
            out[read:read + chunk] = data
            read += chunk
        return bytes(out)

    def _parse_buffer(self, raw, hthread, packet_size):
        """
        Slice raw bytes into packet-sized chunks and decode each.

        :param raw: Raw buffer bytes.
        :param hthread: HW thread index.
        :param packet_size: Size of one QURTK_trace_packet_t.
        :return: List of Packet objects with non-zero pcycle.
        """
        packets = []
        if not raw or packet_size <= 0:
            return packets
        for off in range(0, len(raw), packet_size):
            chunk = raw[off:off + packet_size]
            if len(chunk) < packet_size:
                break
            pkt = Packet()
            if not pkt.parse_from_bytes(self.target, self.logger, chunk, hthread):
                continue
            pkt.convert(self.logger)
            if pkt.pcycle != 0:
                packets.append(pkt)
        return packets

    def run(self):
        """
        Top-level entry: read config, descriptors, buffers; merge and print.
        """
        original_formatter = save_formatter(self.logger)
        try:
            self._run_inner()
        finally:
            restore_formatter(self.logger, original_formatter)

    def _run_inner(self):
        """
        Core merge logic; wrapped by run() for formatter save/restore.
        """
        if (self.trace_cfg_type is None
                or self.entry_type is None
                or self.packet_type is None):
            self.logger.error(
                "[mergebuffer] Required QuRT debug types missing. "
                "Is the UKERNEL ELF (with debug info) loaded?")
            return

        ptr_v, size_v = self._read_top_level_config()
        if ptr_v is None or size_v is None:
            return
        if ptr_v == 0 or size_v == 0:
            self.logger.info(
                "[mergebuffer] QURTK_trace_buffers not initialised "
                f"(trace_buffer_ptr=0x{ptr_v:x}, trace_buffer_size={size_v}). "
                "Tracing has not started yet.")
            return

        try:
            max_hthreads = read_4_byte_symbol_value(
                self.target, "QURTK_MAX_HTHREADS", self.logger)
        except utils_user_exception:
            self.logger.error("[mergebuffer] Symbol 'QURTK_MAX_HTHREADS' not found.")
            return
        if max_hthreads is None or max_hthreads <= 0:
            self.logger.error(f"[mergebuffer] invalid QURTK_MAX_HTHREADS={max_hthreads}.")
            return

        per_thread_size = size_v // max_hthreads
        packet_size = self.packet_type.GetByteSize()

        descriptors = self._read_descriptors(ptr_v, max_hthreads)
        if not descriptors:
            return

        all_packets = []
        for d in descriptors:
            h = d["hthread"]
            addr = d["trace_buffer_addr"]
            if addr == 0:
                self.logger.info(f"[mergebuffer] hthread {h}: addr=0 — skipped.")
                continue
            raw = self._read_buffer(addr, per_thread_size)
            if raw is None:
                self.logger.error(
                    f"[mergebuffer] hthread {h}: failed to read trace buffer "
                    f"@ 0x{addr:x} (+0x{per_thread_size:x}).")
                continue
            all_packets.extend(self._parse_buffer(raw, h, packet_size))

        if not all_packets:
            self.logger.info(
                "[mergebuffer] No trace events have been recorded yet. "
                "Either tracing has not been enabled in QuRT, or you are "
                "stopped before any traceable event has occurred.")
            return

        all_packets.sort(key=lambda p: p.pcycle)
        for pkt in all_packets:
            if pkt.pcycle != 0:
                self.logger.info(f"{pkt}")


def trace_all(debugger, logger):
    """
    Entry point invoked by the qurt dispatcher.

    :param debugger: The lldb debugger object.
    :param logger: The logger object.
    """
    MergeBuffer(debugger, logger).run()


register_command("mergebuffer", trace_all)
