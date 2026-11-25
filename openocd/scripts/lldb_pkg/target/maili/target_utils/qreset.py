#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file qreset.py
#
# @section Description
# Full board reset helper. Sends OpenOCD reset/DAP/AXI commands one at a
# time over telnet.
#

import time
import traceback
from utils.telnet_client import TelnetClient
from utils.debug_context import g_dbgContext, logger
from lldb_pkg.target.maili.target_utils.pbl_load_fbc import parse_hex_response

def axi_ap_init(telnet: TelnetClient, chip_name: str):
    """!
    @brief One-time setup: point "dap apsel" at the AXI AP and program its
    default CSW via "dap apcsw", so axi_read32/axi_write32 never need CSW
    touched by hand again for normal reads/writes.

    @param telnet     Open TelnetClient connection to OpenOCD's server.
    @param chip_name  Chip name prefix used for "<chip_name>.dap ..." commands.
    """
    
    # This SoC's AXI-AP CSW protection/attribute bits:
    #   bit29:28 (Prot ARPROT1:0)     = 0:1  -> secure, privileged
    #   bit23    (SPIDEN/SDeviceEn)   = 1
    #   bit14:13 (impl-defined, SoC)  = 1:1
    # DbgSwEnable (bit31) intentionally left 0, matching this SoC's convention.
    AP_NUM_AXI = 0x24000
    AP_AXI_CSW_DEFAULT = 0x10806000
    AP_AXI_CSW_MASK = 0x30806000

    telnet.send_command(f"{chip_name}.dap apsel {hex(AP_NUM_AXI)}")
    telnet.send_command(f"{chip_name}.dap apcsw {hex(AP_AXI_CSW_DEFAULT)} {hex(AP_AXI_CSW_MASK)}")


def get_cpucp_config(chip_name: str):
    """!
    @brief Look up the <cpucp_config> block for the active chipset from
    chip_config.xml.

    @param chip_name  Chip name to match against chip_config.xml's
                       <chipset name="..."> attribute (case-insensitive).

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


def wait_cpucp_dm_halted(telnet: TelnetClient, dmstatus_addr: int, timeout_sec: float = 2.0, poll_interval: float = 0.01) -> bool:
    """!
    @brief Poll CPUCP's Debug Module dmstatus register for ALLHALTED
    (bit9), replacing a blind post-deassert-srst sleep with a real
    readiness check.

    @param telnet         Open TelnetClient connection to OpenOCD's server.
    @param dmstatus_addr  Address of CPUCP's dmstatus register.
    @param timeout_sec    Maximum time to wait for ALLHALTED.
    @param poll_interval  Delay between polls.

    @return True once dmstatus reports ALLHALTED, False if timeout_sec
            elapses first.
    """
    start = time.time()
    dmstatus = 0
    while time.time() - start < timeout_sec:
        response = telnet.send_command(f"DAP.cxapb read_memory {hex(dmstatus_addr)} 32 1")
        try:
            dmstatus = parse_hex_response(response, dmstatus_addr, "DAP.cxapb read_memory")
        except ValueError:
            # OpenOCD can still be busy right after srst deassert (DP
            # power-up ack, examine callbacks) and return an empty/partial
            # response to this poll -- treat that as "not ready yet"
            # rather than a fatal error, and just retry.
            time.sleep(poll_interval)
            continue

        if dmstatus & 0x200:
            logger.info(f"qreset: CPUCP DM reports ALLHALTED (dmstatus=0x{dmstatus:x})")
            return True
        time.sleep(poll_interval)

    logger.warning(f"qreset: timed out waiting for CPUCP ALLHALTED (last dmstatus=0x{dmstatus:x})")
    return False


def qreset() -> bool:
    """!
    @brief Full board reset: toggles SRST/TRST, reinitializes the DAP,
    re-arms CPUCP's debug module (see inline note below), deasserts SRST,
    then re-inits the AXI-AP and the OpenOCD "init" sequence so subsequent
    arp_examine/gdb-remote calls see a freshly reset, DAP-ready target.

    Opens and closes its own TelnetClient connection, independent of any
    caller's connection, and resolves the active chipset via
    g_dbgContext.chipsetName.

    @return True on success, False if the active chipset's cpucp_config
            (cpucp_debug_base / dmcontrol) is missing from chip_config.xml.
    """
    chip_name = g_dbgContext.chipsetName.upper()

    cpucp_config = get_cpucp_config(chip_name)
    if cpucp_config is None:
        logger.error("qreset: missing cpucp_config configuration")
        return False

    debug_base_elem = cpucp_config.find("cpucp_debug_base")
    if debug_base_elem is None:
        logger.error("qreset: missing cpucp_debug_base element")
        return False
    cpucp_debug_base = int(debug_base_elem.attrib["addr"], 16)

    dmcontrol_elem = cpucp_config.find("dmcontrol")
    if dmcontrol_elem is None:
        logger.error("qreset: missing dmcontrol element")
        return False
    cpucp_dmcontrol_addr = cpucp_debug_base + int(dmcontrol_elem.attrib["offset_addr"], 16)
    dmcontrol_reset_val = int(dmcontrol_elem.attrib["dmcontrol_data_1"], 16)
    dmcontrol_activate_val = int(dmcontrol_elem.attrib["dmcontrol_data_2"], 16)
    dmcontrol_halt_on_reset_val = int(dmcontrol_elem.attrib["dmcontrol_data_3"], 16)

    telnet = TelnetClient()
    try:
        logger.info("qreset: asserting srst")
        telnet.send_command("adapter assert srst")
        # time.sleep(0.01)
        
        if g_dbgContext.debug_protocol == "jtag":
            logger.info("qreset: asserting trst")
            telnet.send_command("adapter assert trst")
            time.sleep(0.01)

            logger.info("qreset: deasserting trst")
            telnet.send_command("adapter deassert trst")

        logger.info("qreset: dap init")
        telnet.send_command("dap init")

        # DP CTRL/STAT (offset 0x4): assert CDBGPWRUPACK (bit29) and clear the
        # STICKYERR/STICKYCMPL flags (bits 5/1), so the DAP starts the rest of
        # this reset from a clean, power-acknowledged state instead of
        # potentially carrying forward stale error/compare bits from before
        # the reset.
        logger.info("qreset: clearing DP CTRL/STAT sticky bits")
        telnet.send_command(f"{chip_name}.dap dpreg 0x4 0x50000022")

        logger.info(f"qreset: resetting CPUCP dmcontrol (0x{cpucp_dmcontrol_addr:x})")
        telnet.send_command(f"DAP.cxapb mww {hex(cpucp_dmcontrol_addr)} {hex(dmcontrol_reset_val)}")

        logger.info("qreset: activating CPUCP dmcontrol")
        telnet.send_command(f"DAP.cxapb mww {hex(cpucp_dmcontrol_addr)} {hex(dmcontrol_activate_val)}")

        logger.info("qreset: arming CPUCP dmcontrol halt-on-reset")
        telnet.send_command(f"DAP.cxapb mww {hex(cpucp_dmcontrol_addr)} {hex(dmcontrol_halt_on_reset_val)}")

        logger.info("qreset: deasserting srst")
        telnet.send_command("adapter deassert srst")
        wait_cpucp_dm_halted(telnet, cpucp_dmcontrol_addr + 0x4)

        logger.info("qreset: re-initializing AXI-AP")
        axi_ap_init(telnet, chip_name)

        logger.info("qreset: openocd init")
        telnet.send_command("init")

        telnet.send_command(f"{chip_name.upper()}.cpucp arp_examine")
        
    except Exception:
        logger.error(f"qreset: error executing reset sequence:\n{traceback.format_exc()}")
        return False
    finally:
        telnet.close()

    return True

