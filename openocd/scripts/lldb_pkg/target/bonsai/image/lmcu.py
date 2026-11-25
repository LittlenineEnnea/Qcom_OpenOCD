#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file lmcu.py
#
# @section Description
# Script for LMCU image debugging.
#

import os
import time
from utils.debug_context import g_dbgContext, logger
from utils.source_map import setSourceMap
from utils.telnet import getTelnetHandle, closeTelnetHandle
from utils.telnet_client import TelnetClient


def find_symbol_addr(target, symbol_name):
    """!
    @brief Get the starting address of a symbol from the ELF.

    @param target       -   An instance of the created SBTarget.
    @param symbol_name  -   Name of the symbol whose address is to be retrieved.

    @return             -   A dict with keys name, module, file_address, load_address,
                            or None if the symbol is not found.
    """
    for m in target.module_iter():
        sc_list = m.FindSymbols(symbol_name)
        for i in range(sc_list.GetSize()):
            sym = sc_list.GetContextAtIndex(i).GetSymbol()
            if sym.IsValid() and sym.GetName() == symbol_name:
                addr = sym.GetStartAddress()
                return {
                    "name": sym.GetName(),
                    "module": addr.GetModule().GetFileSpec().GetFilename(),
                    "file_address": addr.GetFileAddress(),
                    "load_address": addr.GetLoadAddress(target),
                }
    return None


def lmcu_release_sequence(debugger, lmcu_config):
    """!
    @brief Write clock and DMControl registers to bring the LMCU subsystem out of reset.

    Clock registers are read from the lmcu_clocks element of the chip_config XML.
    DMControl registers are read from the dmcontrol element of the chip_config XML.
    Must be called after ADSP STEP 1 (ADSP out of reset) and before
    ADSP STEP 2 continues past main.

    @param debugger     -   An instance of the SBDebugger Object.
    @param lmcu_config  -   The lmcu_config XML element from chip_config.xml.
    """
    lmcu_clocks = lmcu_config.find("lmcu_debug_clocks")
    if lmcu_clocks is None:
        logger.error("Missing lmcu_debug_clocks configuration in chip_config.xml")
        return

    for clock in lmcu_clocks.findall("clock"):
        reg = clock.attrib.get("reg")
        value = clock.attrib.get("value")
        name = clock.attrib.get("name", reg)
        if not reg or not value:
            logger.error(f"Incomplete clock entry in lmcu_debug_clocks: {clock.attrib}")
            continue
        logger.info(f"Enabling clock {name}: reg={reg} value={value}")
        debugger.HandleCommand(f"dap_access -w {reg} -v {value}")
        time.sleep(0.01)

    dmcontrol_elem = lmcu_config.find("dmcontrol")
    if dmcontrol_elem is None:
        logger.error("Missing dmcontrol configuration in chip_config.xml")
        return

    lmcu_debug_base = lmcu_config.find("lmcu_debug_base")
    if lmcu_debug_base is None:
        logger.error("Missing lmcu_debug_base configuration in chip_config.xml")
        return

    base_addr = int(lmcu_debug_base.attrib.get("addr"), 16)
    offset_addr = int(dmcontrol_elem.attrib.get("offset_addr"), 16)
    dmcontrol_reg = hex(base_addr + offset_addr)
    dmcontrol_data_1 = dmcontrol_elem.attrib.get("dmcontrol_data_1")
    dmcontrol_data_2 = dmcontrol_elem.attrib.get("dmcontrol_data_2")

    logger.info(f"Writing DMControl reg={dmcontrol_reg} data_1={dmcontrol_data_1}")
    debugger.HandleCommand(f"dap_access -w {dmcontrol_reg} -v {dmcontrol_data_1}")
    time.sleep(0.01)
    logger.info(f"Writing DMControl reg={dmcontrol_reg} data_2={dmcontrol_data_2}")
    debugger.HandleCommand(f"dap_access -w {dmcontrol_reg} -v {dmcontrol_data_2}")
    time.sleep(0.01)


def debugLMCU(debugger, command, exe_ctx, result, inputarg=None):
    """!
    @brief Orchestrate a full LMCU debug session.

    Sequence:
      -# ADSP STEP 1 : q6Entry sets debug cookie, target boots, pil_debugger
                       brings ADSP out of reset via CTI.
      -# LMCU power  : lmcu_release_sequence writes DAP registers to enable
                       LMCU subsystem clocks and power.
      -# ADSP STEP 2 : debugRootPd(adsp, main) halts ADSP at main, then
                       continues so LMCU comes out of reset.
      -# LMCU attach : Load ELF, examine target, set PC to __start, connect.

    Usage:  debug -d lmcu

    @param debugger  -   An instance of the SBDebugger Object that provides
                         access to SBTargets used for debugging.
    @param command   -   String containing all the input arguments for the command.
    @param exe_ctx   -   An execution context object carrying process context.
    @param result    -   A return object encapsulating success/failure and output text.
    @param inputarg  -   Denotes whether the command is invoked from a script or
                         directly from lldb.
    """
    from lldb_pkg.target.bonsai.image.q6 import q6Entry, debugRootPd
    from lldb_pkg.target.bonsai.image.debugwithpil import pil_debugger

    subsys = "lmcu"
    chip_name = g_dbgContext.chipsetName.lower()

    # Extract lmcu_config from chip_config XML once — shared by release sequence and attach
    lmcu_config = None
    for chip in g_dbgContext.chipInfo.findall("chipset"):
        if chip.get("name").upper() == chip_name.upper():
            riscv_config = chip.find("riscv_config")
            if riscv_config is None:
                logger.error("Missing riscv_config configuration in chip_config.xml")
                return
            lmcu_config = riscv_config.find("lmcu_config")
            if lmcu_config is None:
                logger.error("Missing lmcu_config configuration in chip_config.xml")
                return
            break

    if lmcu_config is None:
        logger.error(f"Chipset '{chip_name}' not found in chip_config.xml")
        return

    # ADSP STEP 1: set debug cookie, boot target, bring ADSP out of reset
    logger.info("LMCU debug: running ADSP STEP 1 (q6Entry + pil_debugger)...")
    q6Entry(debugger, command, exe_ctx, result, subsystem="adsp")

    # Resume APSS asynchronously so pil_debugger can poll CTI while APSS runs.
    # SetAsync(True) makes process.Continue() return immediately instead of
    # blocking until the next halt.
    debugger.SetAsync(True)
    debugger.GetSelectedTarget().GetProcess().Continue()
    pil_debugger(debugger, command, result, subsystem="adsp", poll_for_subsys="True")
    debugger.SetAsync(False)
    logger.info("LMCU debug: ADSP is out of reset.")

    # LMCU power-on: clock enables + DMControl writes to bring LMCU out of reset
    logger.info("LMCU debug: applying LMCU release sequence...")
    lmcu_release_sequence(debugger, lmcu_config)
    logger.info("LMCU debug: LMCU release sequence done.")

    # ADSP STEP 2: run to main, then continue so LMCU comes out of reset
    logger.info("LMCU debug: running ADSP STEP 2 (root_pd_debug to main)...")
    tln = None
    try:
        tln = getTelnetHandle()
        debugRootPd(debugger, "adsp", "main", tln)
    finally:
        if tln is not None:
            closeTelnetHandle()

    # ADSP is now halted at main. Resume asynchronously — there is no further
    # breakpoint on ADSP, so a synchronous continue would block forever.
    # LMCU will come out of reset while ADSP runs freely.
    debugger.SetAsync(True)
    debugger.GetSelectedTarget().GetProcess().Continue()
    debugger.SetAsync(False)
    logger.info("LMCU debug: ADSP continued past main, LMCU should now be out of reset.")

    # LMCU attach
    lmcu_elf_path_elem = lmcu_config.find("lmcu_elf_path")
    if lmcu_elf_path_elem is None:
        logger.error("Missing lmcu_elf_path configuration in chip_config.xml")
        return

    lmcu_elf_file = lmcu_elf_path_elem.attrib.get("lmcu_elf_file")
    if not lmcu_elf_file:
        logger.error("Missing 'lmcu_elf_file' attribute in lmcu_elf_path configuration")
        return

    elf_path = g_dbgContext.openocdConfig.getBinaryPath('lpaicp', lmcu_elf_file)
    
    gdb_remote_connection = f"connect_remote -s {subsys}"

    try:
        with TelnetClient() as telnet:
            telnet.send_command(f"targets {chip_name.upper()}.{subsys}")
            time.sleep(0.02)
            telnet.send_command(f"{chip_name.upper()}.{subsys} arp_examine")
    except Exception as e:
        logger.error(f"Error examining LMCU target: {e}")
        return

    debugger.HandleCommand("settings set auto-confirm on")

    try:
        if os.path.exists(elf_path):
            target = debugger.CreateTarget(elf_path)
            logger.info(f"Successfully loaded ELF: {elf_path}")

            start_info = find_symbol_addr(target, "__start")
            if not start_info:
                logger.error(f"Symbol __start not found in ELF: {elf_path}")
                return

            start_addr = start_info["file_address"]
            logger.info(f"__start in {start_info['module']}: file=0x{start_addr:x}")

            with TelnetClient() as telnet:
                telnet.send_command(f"reg pc {hex(start_addr)}")
    except Exception as e:
        logger.error(f"Failed to load ELF {elf_path}: {type(e).__name__}: {e}")
        return

    try:
        source_path = g_dbgContext.sourcePath[subsys]
        if source_path is not None and os.path.exists(source_path):
            setSourceMap(debugger, subsys, source_path)
            logger.info(f"Source mapping set for {subsys}: {source_path}")
        else:
            logger.error(f"{source_path} - No such file or directory")
    except Exception as e:
        logger.warning(f"Unable to set source map: {type(e).__name__}: {e}")

    try:
        logger.info(f"Establishing gdb connection to {subsys.upper()} target...")
        debugger.HandleCommand(gdb_remote_connection)
        logger.info("GDB remote connection established successfully.")
        logger.info(f"---- {subsys.upper()} entry point reached ----")
    except Exception as e:
        logger.error("Unable to establish GDB connection.")

