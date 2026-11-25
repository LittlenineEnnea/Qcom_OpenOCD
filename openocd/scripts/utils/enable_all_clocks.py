#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file enable_all_clocks.py
#
# @section Description
# This file contains a script to enable all clocks for a selected subsystem.

import os
import sys
import lldb
import platform
from os.path import abspath
from inspect import getsourcefile
from pathlib import Path
from utils.debug_context import logger
from utils.debug_context import g_dbgContext
from utils.dap_access import dapAccessGDB


def enable_all_clocks(debugger, command, result, input_file, subsystem):
    """!

    This function pase register dump file, reads the register values and copy
    the same in an output file.

    @param debugger         -   An instance of the SBDebugger Object that
                                provides access to SBTargets used for debugging.
    @param command          -   String containing all the input arguments
                                for the used command.
    @param result           -   A return object which encapsulates
                                success/failure information for the command
                                and output text that needs to be printed
                                as a result of the command.
    @param input_file        -   Path to register dump cmm
    @subsystem              -   subsystem name to enable clocks

    """
    cmd_intr = debugger.GetCommandInterpreter()
    osName = platform.system()
    try:
        with open(input_file, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        logger.error(f"Input file '{input_file}' not found")
        return
    scriptFile = abspath (getsourcefile(lambda:0))
    scriptsRootDir = os.path.dirname(os.path.dirname(scriptFile))
    # Ensure the temp directory exists else create new
    if (osName == 'Windows'):
        temp_dir = Path(r"C:\Temp")
    elif (osName in ('Linux', 'Darwin')):
        temp_dir = Path(os.path.join(scriptsRootDir, "temp"))

    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create temp directory: {e}")
        return

    # Extract the base name of the input file and change the extension to .py
    base_name = Path(input_file)
    output_file = temp_dir / (base_name.stem + ".py")

    python_script = []
    inside_if_block = False

    # Start traversing in script
    for line in lines:

        # Skip comment lines
        if line.strip().startswith(";"):
            # logger.debug("Skipping ;")
            continue
        if line.strip().startswith("("):
            # logger.debug("Skipping (")
            continue
        if line.strip().startswith(")"):
            # logger.debug("Skipping )")
            continue

        # Convert specific lines to Python syntax
        stripped_line = line.strip()

        if stripped_line.startswith("entry"):
            parts = stripped_line.split()
            # if len(parts) > 1:
            # python_script.append(f"{parts[1][1:]} = '{subsystem}'\n")
            # else:
            # python_script.append("entry = None\n")
            # continue
        elif stripped_line.startswith("if"):
            condition = stripped_line.split("(")[1].split(")")[0].replace("&", "")
            condition = condition.replace("==", "==").replace("||", "or")
            # Extract subsytem
            sub_system = []
            for part in condition.split("or"):
                if "==" in part:
                    sub_system.append(part.split("==")[1].strip('"'))

            if subsystem in sub_system:
                python_script.append(f"if {condition}:\n")
                inside_if_block = True
            else:
                inside_if_block = False

        elif inside_if_block:
            if stripped_line == ")":
                inside_if_block = False
            elif stripped_line.startswith("&"):
                assignment = stripped_line.replace("&", "").replace("=", " = ").strip()
                assignment = assignment.replace("%long", "value").replace(":", "=")

            # parse statements with per.set.field
            elif "per.set.field &access_mode" in stripped_line:
                parts = stripped_line.split()
                # Read data
                if len(parts) >= 5:
                    addr = parts[1].split(":")[1]
                    value = parts[3]
                    bitField = parts[4]

                    # convert values in hex
                    addr_int = int(addr, 16)
                    value_int = int(value, 16)
                    bitField_int = int(bitField, 16)

                    # try:
                    ret = dapAccessGDB(debugger, command, result, "Yes", None, None,
                                             addr_int, value_int, bitField_int)
                    # except Exception as e:
                    #     logger.error(f"DAP Access failed: {e}")
                    #     return

                    # Append data in script
                    python_script.append(
                        f"    dap_access -w {hex(addr_int)} -v {hex(value_int)} -m {hex(bitField_int)}\n"
                    )

            # parse statements with data.set
            elif "data.set &access_mode" in stripped_line:
                parts = stripped_line.split()
                # Read data
                if len(parts) >= 4:
                    addr = parts[1].split(":")[1]
                    value = parts[3]
                    # convert values in int
                    addr_int = int(addr, 16)
                    value_int = int(value, 16)

                    # try:
                    ret = dapAccessGDB(
                            debugger, command, result, "Yes", None, None, addr_int, value_int)
                    # except Exception as e:
                    #     logger.error(f"DAP Access failed: {e}")
                    #     return

                    # Append data in script
                    python_script.append(
                        f"    dap_access -w {hex(addr_int)} -v {hex(value_int)}\n"
                    )

            else:
                # Replace semicolons with hash symbols for comments
                stripped_line = stripped_line.replace(";", "#")
                # Add indentation for lines inside the if block
                python_script.append(f"    {stripped_line}\n")

        elif stripped_line.startswith("&"):
            assignment = stripped_line.replace("&", "").replace("=", " = ").strip()
            assignment = assignment.replace("%long", "value").replace(":", "=")
            # continue

    # Write the converted script to a new Python file
    try:
        with open(output_file, "w") as f:
            f.writelines(python_script)
    except Exception as e:
        logger.info(f"Failed to write script to file: {e}")
        return

    logger.info(
        f"Enabled Clocks for subsystem:{subsystem} and script has been created at: {output_file}"
    )
