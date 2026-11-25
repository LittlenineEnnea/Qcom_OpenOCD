#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file get_codelldb_lldb_path.py
#
# @section Description
# This file contains script for adding delay of specified seconds.

import re
import os
import json
import time
import platform
import subprocess
import argparse

from utils.debug_context import logger


def get_lldb_path():
    """!
    This function is used to get codelldb lldb path.
    @param func_name : get_lldb_path
    @param ret_val : lldb_path
    """
    system = platform.system()
    if system == "Windows":
        home_dir = os.environ["USERPROFILE"]
    elif system == "Linux":
        home_dir = os.environ["HOME"]
    elif system == "Darwin":
        # The 'code' CLI is often not on PATH on macOS, so look for the
        # CodeLLDB extension directly and take the newest installed version.
        import glob
        pattern = os.path.join(os.environ["HOME"], ".vscode", "extensions",
                               "vadimcn.vscode-lldb-*", "lldb", "bin", "lldb")
        candidates = sorted(glob.glob(pattern),
                            key=lambda p: [int(x) if x.isdigit() else 0 for x in
                                           re.split(r"[.-]", p.split("vadimcn.vscode-lldb-")[1].split(os.sep)[0])])
        if not candidates:
            print("Error: Unable to find CodeLLDB extension installation")
            return None
        return candidates[-1]
    else:
        print("Error: ", system, " OS not supported !!")
        return None

    try:
        # Get the list of VSCode extensions with their versions
        command = "code --show-versions --list-extensions"
        process = subprocess.run(
            command, shell=True, capture_output=True, text=True, check=True
        )
        output = process.stdout
    except Exception as e:
        print(f"Error: {e}")
        return None

    # extract the CodeLLDB extension version
    ss = re.search("vadimcn.vscode-lldb@(.+)", output, re.MULTILINE)
    codelldb_version = ss.group(1)

    if codelldb_version == None:
        print("Error: Unable to find CodeLLDB extension installation")
        return None

    # construct the path to the lldb binary
    lldb_path = os.path.join(
        home_dir,
        ".vscode",
        "extensions",
        "vadimcn.vscode-lldb-{}".format(codelldb_version),
        "lldb",
        "bin",
        "lldb",
    )
    return lldb_path


def run_lldb_commands(lldb_path, lldb_command):
    """!
    This function is used to run lldb command to start openocd.
    @param func_name : run_lldb_commands
    @param ret_val : None
    """
    # Run the lldb command with required arguments to start the OpenOCD server.
    try:
        subprocess.run(
            [
                lldb_path,
                "-o",
                "command script import openocd_lldb.py",
                "-o",
                lldb_command,
                "-o",
                "custom_wait -s 1",
                "-o",
                "quit",
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    lldb_path = get_lldb_path()
    logger.info(lldb_path)

    parser = argparse.ArgumentParser(description="Get LLDB arguments.")
    parser.add_argument("--arg1", type=str, help="OpenOCD command")
    args = parser.parse_args()
    logger.info(f"Argument 1: {args.arg1}")
    lldb_command = args.arg1

    run_lldb_commands(lldb_path, lldb_command)
    time.sleep(10)
