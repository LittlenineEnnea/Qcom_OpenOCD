#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file tme_logs.py
#
# @section Description
# Script for collecting and parsing TME logs via LLDB.
#

import os
import sys
# import time
import lldb
import subprocess
from datetime import datetime
from utils.debug_context import g_dbgContext, logger


def get_python_executable():
    """
    Get the actual Python executable path.
    When running in LLDB, sys.executable might point to codelldb.exe.
    This function finds the real Python interpreter.
    """
    import shutil

    python_exe = shutil.which('python')
    if python_exe:
        return python_exe

    python_exe = shutil.which('python3')
    if python_exe:
        return python_exe

    logger.warning("Could not find python executable in PATH, using sys.executable")
    return sys.executable


def collect_tme_logs(debugger, chipset, subsystem, output_path=None):
    """
    Collect TME logs by reading memory and saving to binary file.

    @param debugger         LLDB debugger instance
    @param chipset          Chipset name (e.g., 'poros')
    @param subsystem        Subsystem name (e.g., 'tme')
    @param output_path      Optional custom output path for raw logs

    @return Path to the collected raw logs file
    """

    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = r"C:\Temp\.openocd"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"Raw_Tme_logs_{timestamp}")

    # logger.info(f"Collecting TME logs to: {output_path}")
    log_buffer_addr = 0x401800
    log_buffer_size = 0x4000 
    start_addr = log_buffer_addr
    end_addr = log_buffer_addr + log_buffer_size
    memory_read_cmd = f"memory read --outfile {output_path} --force --binary {log_buffer_addr} {end_addr}"

    try:
        debugger.HandleCommand(memory_read_cmd)
        # logger.info(f"TME logs collected successfully at: {output_path}")
        return output_path
    except Exception as e:
        logger.error(f"Failed to collect TME logs: {e}")
        return None


def parse_tme_logs(raw_logs_path, output_path=None, dict_path=None):
    """
    Parse TME logs using the tme_log_parser.py script.

    @param raw_logs_path    Path to raw TME logs file
    @param output_path      Optional custom output path for parsed logs
    @param dict_path        Optional custom path to TME log dictionary

    @return Path to the parsed logs file
    """

    if not os.path.exists(raw_logs_path):
        logger.error(f"Raw logs file not found: {raw_logs_path}")
        return None

    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = r"C:\Temp\.openocd"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"Tme_parsed_logs_{timestamp}")

    boot_image_path = g_dbgContext.openocdConfig.getImageBasePath ("boot")
    boot_image_path = boot_image_path.replace("\n","")
    # logger.error(f"boot path {boot_image_path}")
    Chipset_name = g_dbgContext.chipsetName.lower()
    # Fetching the InitOffsets
    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname=chip.get('name')
        if (chipname.upper() == Chipset_name.upper()):
            riscv_config = chip.find("riscv_config")

    tme_config = riscv_config.find("tme_config") if riscv_config else None
    if tme_config is None:
        logger.error("Missing TME configuration")
        return
    
    tme_debug_base_elem = tme_config.find("tme_log_dict")

    if dict_path is None:
        file_path = tme_config.find("tme_log_dict").attrib['path']
        dict_path = os.path.join(boot_image_path, file_path)
        # logger.error(f"dict file path {dict_path}")

    file_path = tme_config.find("tme_log_parser").attrib['path']
    parser_script = os.path.join(boot_image_path, file_path)

    if not os.path.exists(parser_script):
        logger.error(f"TME log parser script not found: {parser_script}")
        return None

    if not os.path.exists(dict_path):
        logger.error(f"TME log dictionary not found: {dict_path}")
        return None

    logger.info(f"Parsing TME logs using: {parser_script}")

    try:
        # parser_dir = os.path.dirname(parser_script)

        raw_logs_abs = os.path.abspath(raw_logs_path)
        output_abs = os.path.abspath(output_path)
        dict_abs = os.path.abspath(dict_path)
        python_exe = get_python_executable()

        cmd = [
            python_exe,
            parser_script,
            "--input", raw_logs_abs,
            "--output", output_abs,
            "--dict", dict_abs
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            logger.error(f"Parser failed with return code {result.returncode}")
            logger.error(f"stderr: {result.stderr}")
            return None

        # logger.info(f"TME logs parsed successfully at: {output_path}")
        return output_path

    except subprocess.TimeoutExpired:
        logger.error("TME log parser timed out after 60 seconds")
        return None
    except Exception as e:
        logger.error(f"Failed to parse TME logs: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def tme_logs_command(debugger, chipset, subsystem, output_path=None, dict_path=None):
    """
    Main command to collect and parse TME logs.

    @param debugger         LLDB debugger instance
    @param chipset          Chipset name (e.g., 'poros')
    @param subsystem        Subsystem name (e.g., 'tme')
    @param output_path      Optional custom output path for parsed logs
    @param dict_path        Optional custom path to TME log dictionary
    """
    error = lldb.SBError()
    g_dbgContext.tgt = debugger.GetSelectedTarget()
    tme_elf_name = "firmware.elf"
    tme_elf_path = g_dbgContext.openocdConfig.getBinaryPath(
                   "boot", tme_elf_name
                   )
    tme_dSYM_cmd = "{}.dSYM".format(tme_elf_path)
    module = g_dbgContext.tgt.AddModule (tme_elf_path, None, None, tme_dSYM_cmd)
    g_dbgContext.tgt.SetModuleLoadAddress (module, 0)
    if error.Success():
        logger.info(f" {tme_elf_name} symbols load success")
    else:
        logger.error(f" Failed to load {tme_elf_name} symbols: {error.GetCString()}")

    logger.info(f"Starting TME logs collection for {chipset} {subsystem}")

    raw_logs_path = collect_tme_logs(debugger, chipset, subsystem)

    if raw_logs_path is None:
        logger.error("Failed to collect TME logs")
        return

    parsed_logs_path = parse_tme_logs(raw_logs_path, output_path, dict_path)

    if parsed_logs_path is None:
        logger.error("Failed to parse TME logs")
        return

    logger.info(f"TME Raw logs collection and parsing completed successfully")
    logger.info(f"TME Raw logs collected successfully at: {raw_logs_path}")
    logger.info(f"TME logs parsed successfully at: {parsed_logs_path}")
