#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file cpucp_init_maili.py
#
# @section Description
# Script for CPUCP+APPS PBL bring-up.
#

import re
import time
import traceback
import lldb
from utils.debug_context import g_dbgContext, logger
from utils.telnet_client import TelnetClient
from utils.stop_wait import wait_for_breakpoint_hit

GDB_PORT_CPUCP = 3104

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


def get_reset_scratch_config(chip_name: str):
    """!
    @brief Look up the <reset_scratch_config> block for the active chipset
    from chip_config.xml.

    @param chip_name  Chip name to match against chip_config.xml's
                       <chipset name="..."> attribute (case-insensitive).

    @return The reset_scratch_config ElementTree.Element, or None if the
            active chipset has no such configuration.
    """
    for chip in g_dbgContext.chipInfo.findall("chipset"):
        if chip.get("name", "").lower() != chip_name.lower():
            continue

        return chip.find("reset_scratch_config")

    return None


def clear_dload_cookie(telnet: TelnetClient, chip_name: str) -> bool:
    """!
    @brief Clear the sticky SBL dload/minidump cookie bits in
    TCSR_BOOT_MISC_DETECT via a read-modify-write, preserving every other
    bit in the register.

    XBL treats this register as retained across an SRST-only reset (it is
    normally cleared only by a full PMIC power-on-reset or by explicit SBL/
    Sahara handling). If a prior debug/crash/ramdump session left the dload
    or minidump bit set, the very next boot latches BOOT_MODE_RAMDUMP at
    the top of SBL1 -- before DDR init even runs -- which cancels normal
    image loads and diverts into the ramdump/dload path instead of
    continuing to qsee_jump_func. Requires axi_ap_init to have already run
    on this telnet connection.

    @param telnet     Open TelnetClient connection to OpenOCD's server.
    @param chip_name  Chip name to match against chip_config.xml's
                       <chipset name="..."> attribute (case-insensitive).

    @return True on success (including when the active chipset has no
            reset_scratch_config -- nothing to clear), False on error.
    """
    reset_scratch_config = get_reset_scratch_config(chip_name)
    if reset_scratch_config is None:
        logger.info("qreset: no reset_scratch_config for this chipset, skipping dload-cookie clear")
        return True

    tcsr_elem = reset_scratch_config.find("tcsr_boot_misc_detect")
    if tcsr_elem is None:
        logger.info("qreset: no tcsr_boot_misc_detect element, skipping dload-cookie clear")
        return True

    addr = int(tcsr_elem.attrib["addr"], 16)
    clear_mask = int(tcsr_elem.attrib["clear_mask"], 16)

    try:
        current_value = axi_read32(telnet, addr)
        new_value = current_value & ~clear_mask
        if new_value != current_value:
            logger.info(
                f"qreset: clearing dload/minidump cookie in TCSR_BOOT_MISC_DETECT "
                f"(0x{addr:x}): 0x{current_value:x} -> 0x{new_value:x}"
            )
            axi_write32(telnet, addr, new_value)
        else:
            logger.info("qreset: dload/minidump cookie already clear in TCSR_BOOT_MISC_DETECT")
    except Exception:
        logger.error(f"qreset: failed to clear dload/minidump cookie:\n{traceback.format_exc()}")
        return False

    return True


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
    @param target_name  Full target name (e.g. "MAILI.cpucp", "MAILI.apss.core3").

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
    @param target_name  Full target name (e.g. "MAILI.cpucp", "MAILI.apss.core3").

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

    init_cpucp_maili() ends by telling OpenOCD to shut down once CPUCP's
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
    while time.time() - start < timeout_sec:
        try:
            with TelnetClient() as telnet:
                if is_target_examined(telnet, apss_target_name):
                    break
                else:
                    telnet.send_command(f"{apss_target_name} arp_examine")
                    return True
                
        except Exception:
            pass
        time.sleep(poll_interval)

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

def pbl_load_fbc(cluster_id: int = 0, apss_core_id: int = 3):
    """!
    @brief Bring CPUCP out of PBL ROM reset, arm APPS' PBL-exit
    breakpoint via the QDSS NCC SRST method, and hand both cores back once
    CPUCP reaches its PBL cease point. CPUCP's job then done, this shuts
    OpenOCD down and waits for the supervised relaunch to come back up and
    re-attach to the APSS target before returning.

    @param cluster_id     Cluster index used for the QDSS NCC SRST address.
    @param apss_core_id   Core index used for the QDSS NCC SRST address and
                          the APSS target to arp_examine.

    @return True once APSS bring-up succeeded and the relaunched OpenOCD
            instance has re-attached to it, False if any step failed.
    """

    chip_name = g_dbgContext.chipsetName.lower()
    
    debugger: lldb.SBDebugger = lldb.debugger

    apss_target_name = f"{chip_name.upper()}.apss.core{apss_core_id}"
    cpucp_target_name = f"{chip_name.upper()}.cpucp"

    cpucp_config = get_cpucp_config(chip_name)
    if cpucp_config is None:
        logger.error("Missing cpucp_config configuration")
        return False

    cpucp_pbl_config = cpucp_config.find("cpucp_pbl_config")
    if cpucp_pbl_config is None:
        logger.error("Missing cpucp_pbl_config configuration")
        return False

    rom_base_elem = cpucp_pbl_config.find("cpucp_rom_base")
    if rom_base_elem is None:
        logger.error("Missing cpucp_rom_base element")
        return False
    CPUCP_ROM_BASE = int(rom_base_elem.attrib["addr"], 16)

    # QDSS register method to break at the APPS reset handler
    # QDSS_APSS_APSS_CPUCP_DBG_RCE_NCCm_COREn = ncc_base + (cluster_mult*m) + (core_mult*n)
    ncc_srst_elem = cpucp_pbl_config.find("qdss_ncc_srst")
    if ncc_srst_elem is None:
        logger.error("Missing qdss_ncc_srst element")
        return False
    ncc_base = int(ncc_srst_elem.attrib["addr"], 16)
    ncc_cluster_mult = int(ncc_srst_elem.attrib["cluster_mult"], 16)
    ncc_core_mult = int(ncc_srst_elem.attrib["core_mult"], 16)
    ncc_srst_req_val = int(ncc_srst_elem.attrib["val"], 16)
    qdss_apss_apss_cpucp_dbg_rce_nccm_coren_addr = ncc_base + (ncc_cluster_mult * cluster_id) + (ncc_core_mult * apss_core_id)

    cpucp_process: lldb.SBProcess = None
    bring_up_succeeded = False
    try:
        with TelnetClient() as telnet:

            if is_target_examined(telnet, apss_target_name):
                telnet.send_command(f"{apss_target_name} aarch64 suspend_debug")
            debugger.HandleCommand("settings set auto-confirm on")
            debugger.HandleCommand("settings set plugin.process.gdb-remote.packet-timeout 10000000")

            if not clear_dload_cookie(telnet, chip_name):
                return False
            # time.sleep(0.5)

            if is_target_examined(telnet, cpucp_target_name):
                if is_target_suspended(telnet, cpucp_target_name):
                    telnet.send_command(f"{cpucp_target_name} riscv resume_debug")
            else:
                telnet.send_command(f"{cpucp_target_name} arp_examine")

            # time.sleep(0.5)
            debugger.HandleCommand(f"gdb-remote {g_dbgContext.getGdbRemoteHost()}:{GDB_PORT_CPUCP}")

            cpucp_target: lldb.SBTarget = debugger.GetSelectedTarget()
            if not cpucp_target.IsValid():
                logger.error(f"gdb-remote connect to CPUCP (port {GDB_PORT_CPUCP}) failed: no valid target")
                return False

            cpucp_process = cpucp_target.GetProcess()
            if not cpucp_process.IsValid():
                logger.error(f"gdb-remote connect to CPUCP (port {GDB_PORT_CPUCP}) failed: no valid process")
                return False

            pc = cpucp_process.GetSelectedThread().GetSelectedFrame().GetPC()
            if pc != CPUCP_ROM_BASE:
                logger.error(f"Ensure system is in UP state (PC=0x{pc:x}, expected 0x{CPUCP_ROM_BASE:x})")
                return False

            # PBL Debug Table starts @ rom_entry + 0x20 (APPS) / +0x1C (CPUCP).
            # Table entries: 00 pbl_recoverable_error_handler,
            #                01 pbl_non_recoverable_error_handler, 02 pbl_exit
            cpucp_pbl_cease = axi_read32(telnet, CPUCP_ROM_BASE + 0x1C + 0x4)
            if cpucp_pbl_cease == 0:
                logger.error("cpucp_pbl_cease read back 0x0 -- PBL debug table not populated yet")
                return False

            # Initialize the PSS boot cluster for now manually
            axi_write32(telnet, 0x18B36444, 0x0)

            axi_write32(telnet, qdss_apss_apss_cpucp_dbg_rce_nccm_coren_addr, ncc_srst_req_val)

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

