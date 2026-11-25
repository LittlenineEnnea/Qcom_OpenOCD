# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# All rights reserved.
# Confidential and Proprietary - Qualcomm Technologies, Inc.
#
# @file hexagon_utils_loader.py
#
# @section Description
# This file contains the implementation to locate and import the Hexagon
# Tools helper script hexagon_utils.py (tlb / pagetable / pv* / dvf commands)
# when running under an LLDB that did not already register them. The command
# class HexagonUtilsLoad in openocd_lldb.py registers the handler.
#

import os
import sys
import lldb

try:
    from utils.debug_context import logger
except:
    from logger import logger

# Install roots to search, per platform (Windows vs POSIX kept separate).
ROOTS = ((r"C:\Qualcomm\HEXAGON_Tools",)
         if (os.name == "nt" or sys.platform.startswith("win"))
         else ("/opt/Qualcomm/HEXAGON_Tools",
               "/pkg/qct/software/hexagon/releases/tools"))

# Sub-path, below <root>/<version>/, where hexagon_utils.py lives.
HU_SUBPATH = ("Tools", "lib", "site-packages", "hexagon_utils.py")


def load_hexagon_utils(debugger):
    """
    Finds hexagon_utils.py under the platform install roots and imports it.

    Single entry point: call this wherever the tlb/pagetable/pv* commands are
    needed. Never raises; filesystem errors on any single root just skip that
    root.

    Parameters:
    debugger (lldb.SBDebugger): The LLDB debugger instance.

    Returns:
    bool: True on a successful command script import, False if Hexagon Tools
    is not installed or LLDB reported an import error.
    """
    hu_path = None
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            versions = sorted(os.listdir(root), reverse=True)  # newest first
        except OSError as exc:
            logger.debug("hexagon_utils_loader: cannot list %s: %s" % (root, exc))
            continue
        for ver in versions:
            # Only accept a plain single-component directory name, so a
            # maliciously-named entry cannot escape the root via traversal.
            if (not ver or ver in (".", "..") or "/" in ver or "\\" in ver):
                continue
            cand = os.path.join(root, ver, *HU_SUBPATH)
            if os.path.isfile(cand):
                hu_path = cand
                break
        if hu_path:
            break

    if not hu_path:
        logger.debug("hexagon_utils_loader: hexagon_utils.py not found; "
                     "tlb/pagetable/pv* commands unavailable")
        return False

    ret = lldb.SBCommandReturnObject()
    # Quote the path to guard against spaces in the install location.
    debugger.GetCommandInterpreter().HandleCommand(
        'command script import "%s"' % hu_path, ret)
    if not ret.Succeeded():
        logger.warning("hexagon_utils_loader: failed to import %s: %s"
                       % (hu_path, (ret.GetError() or "").strip()))
        return False

    logger.info("hexagon_utils_loader: loaded Hexagon utilities from %s" % hu_path)
    return True


def hexagon_utils_load_command(debugger, command, exe_ctx, result, internal_dict):
    """
    Implementation of the hexagon_utils_load LLDB command handler that
    (re)loads hexagon_utils.py. Thin wrapper over load_hexagon_utils();
    registered as an LLDB command by the HexagonUtilsLoad class in
    openocd_lldb.py.

    Parameters:
    debugger (lldb.SBDebugger): The LLDB debugger instance.
    command (str): The command argument string (none expected).
    exe_ctx (lldb.SBExecutionContext): The execution context object.
    result (lldb.SBCommandReturnObject): The command return object.
    internal_dict (dict): The built-in identifiers dict (unused).
    """
    if load_hexagon_utils(debugger):
        result.AppendMessage("[hexagon_utils_loader] Hexagon utilities loaded "
                             "(tlb / pagetable / pv* / dvf)")
    else:
        result.AppendMessage("[hexagon_utils_loader] Hexagon Tools not found "
                             "or import failed")