#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file pbl_load_fbc.py
#
# @section Description
# Script for CPUCP+APPS PBL bring-up.
#

import re
import time
import lldb
import traceback
from utils.telnet_client import TelnetClient
from utils.stop_wait import wait_for_breakpoint_hit
from utils.debug_context import g_dbgContext, logger

def parse_hex_response(response, addr: int, proc_name: str) -> int:
    """!
    @brief Parse the last 0x-prefixed hex value out of an OpenOCD telnet response.

    @param response   Raw response returned by TelnetClient.send_command
                       (bytes or str).
    @param addr       Address the response corresponds to, used only for
                       the error message if parsing fails.
    @param proc_name  Name of the command/proc that produced the response,
                       used only for the error message if parsing fails.

    @return The last 0x-prefixed hex value found in the response, as an int.
    """
    text = response.decode("utf-8", errors="ignore") if isinstance(response, bytes) else str(response)
    matches = re.findall(r"0x[0-9A-Fa-f]+", text)
    if not matches:
        raise ValueError(f"Unable to parse {proc_name} response for {hex(addr)}: {text!r}")
    return int(matches[-1], 16)


def axi_read32(telnet: TelnetClient, addr: int) -> int:
    """!
    @brief Recommended 32-bit AXI read, via the DAP.cxaxi mem_ap target.
    Requires axi_ap_init to have been called first.

    @param telnet  Open TelnetClient connection to OpenOCD's server.
    @param addr    AXI address to read (up to 64-bit).

    @return The 32-bit value read from addr.
    """
    response = telnet.send_command(f"DAP.cxaxi read_memory {hex(addr)} 32 1")
    return parse_hex_response(response, addr, "DAP.cxaxi read_memory")


def axi_write32(telnet: TelnetClient, addr: int, val: int):
    """!
    @brief Recommended 32-bit AXI write, via the DAP.cxaxi mem_ap target.
    See axi_read32 for setup requirements.

    @param telnet  Open TelnetClient connection to OpenOCD's server.
    @param addr    AXI address to write (up to 64-bit).
    @param val     32-bit value to write.
    """
    telnet.send_command(f"DAP.cxaxi write_memory {hex(addr)} 32 {{{hex(val)}}}")


def extract_nul_marked_value(text: str, caller: str, target_name: str):
    """!
    @brief Pull the NUL-marker-prefixed Tcl return value out of an OpenOCD
    telnet response.

    Warns (rather than silently treating it the same as a conclusive
    negative) when the marker itself can't be found, since callers use
    this result to decide whether to skip safety steps like suspend_debug
    before a reset -- a parse failure and a genuine "no" must not look
    identical in the log.

    @param text         Decoded telnet response text.
    @param caller       Name of the calling probe, for the warning message.
    @param target_name  Full target name the probe was issued against.

    @return The extracted value, or None if the NUL marker wasn't found.
    """
    match = re.search(r"\x00([^\r\n]*)", text)
    if match is None:
        logger.warning(
            f"{caller}: could not parse response for {target_name}, "
            f"treating as unknown (raw={text!r})"
        )
        return None
    return match.group(1).strip()


def is_target_examined(telnet: TelnetClient, target_name: str) -> bool:
    """!
    @brief Check whether an OpenOCD target has already been examined, via
    "<target_name> was_examined".

    OpenOCD's telnet server echoes the command, then emits the actual Tcl
    return value prefixed with a NUL (0x00) marker before the trailing
    CRLF/prompt -- this pulls out just that marked value rather than
    matching against the whole noisy response (command echo, async log
    lines, prompt).

    @param telnet       Open TelnetClient connection to OpenOCD's server.
    @param target_name  Full target name (e.g. "MAVROS.cpucp", "MAVROS.apss.core0").

    @return True if the target reports as examined, False if it reports
            otherwise or the probe response couldn't be parsed.
    """
    response = telnet.send_command(f"{target_name} was_examined")
    text = response.decode("utf-8", errors="ignore") if isinstance(response, bytes) else str(response)
    extracted = extract_nul_marked_value(text, "is_target_examined", target_name)
    return extracted == "1"


def is_target_suspended(telnet: TelnetClient, target_name: str) -> bool:
    """!
    @brief Check whether an OpenOCD target is currently in debug-suspended
    state, via "<target_name> curstate".

    Same NUL-marker extraction as is_target_examined -- OpenOCD's telnet
    server echoes the command, then emits the actual Tcl return value
    prefixed with a NUL (0x00) marker before the trailing CRLF/prompt.

    @param telnet       Open TelnetClient connection to OpenOCD's server.
    @param target_name  Full target name (e.g. "MAVROS.cpucp", "MAVROS.apss.core0").

    @return True if curstate reports "debug suspended", False if it
            reports otherwise or the probe response couldn't be parsed.
    """
    response = telnet.send_command(f"{target_name} curstate")
    text = response.decode("utf-8", errors="ignore") if isinstance(response, bytes) else str(response)
    extracted = extract_nul_marked_value(text, "is_target_suspended", target_name)
    return extracted == "debug suspended"


def wait_for_openocd_relaunch(apss_target_name: str, timeout_sec: float = 60, poll_interval: float = 0.5) -> bool:
    """!
    @brief Poll until a relaunched OpenOCD instance is accepting telnet
    connections and reports the APSS target as examined.

    pbl_load_fbc() ends by telling OpenOCD to shut down once CPUCP's
    job (bringing the APSS target out of reset) is done. OpenOCD is run
    under a supervisor that immediately relaunches it and re-attaches to
    the APSS target; callers need that new instance to actually be up and
    re-attached before issuing their own gdb-remote connect, so this
    replaces a blind sleep with a real readiness check.

    @param apss_target_name  Full APSS target name to poll was_examined on.
    @param timeout_sec       Maximum time to wait for OpenOCD to come back.
    @param poll_interval     Delay between connection attempts.

    @return True once the new instance is up and the APSS target is
            examined, False if timeout_sec elapses first.
    """
    start = time.time()
    last_error = None
    while time.time() - start < timeout_sec:
        try:
            with TelnetClient() as telnet:
                if is_target_examined(telnet, apss_target_name):
                    return True
                telnet.send_command(f"{apss_target_name} arp_examine")

        except Exception as e:
            last_error = e
        time.sleep(poll_interval)

    if last_error is not None:
        logger.warning(f"wait_for_openocd_relaunch: still failing after timeout: {last_error}")
    return False


def get_cpucp_config(chip_name: str):
    """!
    @brief Look up the <cpucp_config> block for the active chipset from
    chip_config.xml.

    @return The cpucp_config ElementTree.Element, or None if the active
            chipset has no such configuration.
    """
    for chip in g_dbgContext.chipInfo.findall("chipset"):
        if chip.get("name", "").lower() != chip_name.lower():
            continue

        riscv_config = chip.find("riscv_config")
        if riscv_config is None:
            return None

        return riscv_config.find("cpucp_config")

    return None

def pbl_load_fbc(apss_core_id: int = 0, flashing: bool = False):
    """!
    @brief Bring CPUCP out of PBL ROM reset, set a one-shot breakpoint at
    its fixed PBL-cease address, and hand both cores back once CPUCP
    reaches that point. CPUCP's job then done, this shuts OpenOCD down and
    waits for the supervised relaunch to come back up and re-attach to the
    APSS target before returning.

    @param apss_core_id   Core index used for the APSS target to
                          arp_examine/resume.
    @param flashing       When True, skip waiting at the PBL-cease
                          breakpoint (used for flashing flows).

    @return True once APSS bring-up succeeded and the relaunched OpenOCD
            instance has re-attached to it, False if any step failed.
    """

    chip_name = g_dbgContext.chipsetName.lower()

    debugger: lldb.SBDebugger = lldb.debugger

    apss_target_name = f"{chip_name.upper()}.apss.core{apss_core_id}"
    cpucp_target_name = f"{chip_name.upper()}.cpucp"

    g_dbgContext.getDbgPortNum("cpucp")
    gdb_port_cpucp = g_dbgContext.port

    cpucp_config = get_cpucp_config(chip_name)
    if cpucp_config is None:
        logger.error("Missing cpucp_config configuration")
        return False

    cpucp_pbl_config = cpucp_config.find("cpucp_pbl_config")
    if cpucp_pbl_config is None:
        logger.error("Missing cpucp_pbl_config configuration")
        return False

    cpucp_pbl_cease_elem = cpucp_pbl_config.find("cpucp_pbl_cease")
    if cpucp_pbl_cease_elem is None:
        logger.error("Missing cpucp_pbl_cease configuration")
        return False
    cpucp_pbl_cease = int(cpucp_pbl_cease_elem.attrib["addr"], 16)

    cpucp_process: lldb.SBProcess = None
    bring_up_succeeded = False
    try:
        with TelnetClient() as telnet:

            if is_target_examined(telnet, apss_target_name):
                telnet.send_command(f"{apss_target_name} aarch64 suspend_debug")
            debugger.HandleCommand("settings set auto-confirm on")
            debugger.HandleCommand("settings set plugin.process.gdb-remote.packet-timeout 10000000")

            if is_target_examined(telnet, cpucp_target_name):
                if is_target_suspended(telnet, cpucp_target_name):
                    telnet.send_command(f"{cpucp_target_name} riscv resume_debug")
            else:
                telnet.send_command(f"{cpucp_target_name} arp_examine")

            debugger.HandleCommand(f"gdb-remote {g_dbgContext.getGdbRemoteHost()}:{gdb_port_cpucp}")

            cpucp_target: lldb.SBTarget = debugger.GetSelectedTarget()
            if not cpucp_target.IsValid():
                logger.error(f"gdb-remote connect to CPUCP (port {gdb_port_cpucp}) failed: no valid target")
                return False

            cpucp_process = cpucp_target.GetProcess()
            if not cpucp_process.IsValid():
                logger.error(f"gdb-remote connect to CPUCP (port {gdb_port_cpucp}) failed: no valid process")
                return False

            if not flashing:
                cpucp_bp = cpucp_target.BreakpointCreateByAddress(cpucp_pbl_cease)
                cpucp_bp.SetOneShot(True)
                cpucp_process.Continue()
                if not wait_for_breakpoint_hit(cpucp_process):
                    logger.error("Timeout waiting for CPUCP PBL cease")
                    return False

            if is_target_examined(telnet, apss_target_name):
                telnet.send_command(f"{apss_target_name} aarch64 resume_debug")
            else:
                telnet.send_command(f"{apss_target_name} arp_examine")
                telnet.send_command(f"{apss_target_name} arp_halt")

            debugger.SetAsync(True)
            cpucp_process.Continue()
            debugger.SetAsync(False)
            cpucp_process.Detach()
            telnet.send_command(f"{chip_name.upper()}.cpucp riscv suspend_debug")
            telnet.send_command(f"shutdown")
            bring_up_succeeded = True
    except Exception:
        logger.error(f"Error executing CPUCP PBL bring-up flow:\n{traceback.format_exc()}")

    if not bring_up_succeeded:
        return False

    if not wait_for_openocd_relaunch(apss_target_name):
        logger.error("Timed out waiting for OpenOCD to relaunch and re-attach to APSS")
        return False

    return True

