#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file reset.py
#
# @section Description
# This file contains script for performing core reset(monitor reset halt).

import sys
import lldb
import importlib
from utils.debug_context import logger
from utils.debug_context import g_dbgContext
from utils.utilities import get_adi_version

def ResetCore(debugger: lldb.SBDebugger):

    # fetch chipname from debug context
    # g_dbgContext.chipsetName will be set if we run set_chipset or meta_dir command.

    chip_name = g_dbgContext.chipsetName.upper()
    cmd_intr: lldb.SBCommandInterpreter = debugger.GetCommandInterpreter()
    result: lldb.SBCommandReturnObject = lldb.SBCommandReturnObject()
    adi_version = get_adi_version()
   
    if chip_name == "":
        logger.error ("Please provide chipname before running qreset command.")
        logger.info ("Command to set chipname : set_chipset -s <chip_name>")
        sys.exit(1)

    if adi_version == "5": 
        target = "core0"

        cmd = [ "{} {}.apss.{} invoke-event reset-assert-pre".format(g_dbgContext.monitorPrefix, chip_name, target),
                "{} {}.apss.{} arp_reset assert 1".format(g_dbgContext.monitorPrefix, chip_name, target),
                "{} {}.apss.{} invoke-event reset-assert-post".format(g_dbgContext.monitorPrefix, chip_name, target),
                "{} dap init".format(g_dbgContext.monitorPrefix),
                "{} {}.apss.{} arp_reset deassert 1".format(g_dbgContext.monitorPrefix, chip_name, target) ]

        for command in cmd:
            cmd_intr.HandleCommand(command, result)
            if result.Succeeded():
                res_out = str(result.GetOutput())
                r = res_out.find("response:")
                res_out = res_out[(r + 10) :]
                res_out = res_out.replace("\n", "")
                if res_out == "OK":
                    logger.info(f"response: {res_out}")
                else:
                    logger.info(f"response: {res_out}")
                    logger.error("qreset failed")
                    sys.exit(1)
            else:
                logger.error("Establish connection to target, before trying qreset")
                sys.exit(1)

    elif adi_version == "6":
        module_name = f"lldb_pkg.target.{chip_name.lower()}.target_utils.qreset"
        try:
            qreset_module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            logger.error(f"qreset is not implemented for chipset '{chip_name.lower()}' ({module_name} not found)")
            sys.exit(1)

        qreset = getattr(qreset_module, "qreset", None)
        if qreset is None:
            logger.error(f"{module_name} does not define a qreset() function")
            sys.exit(1)

        if not qreset():
            logger.error("qreset failed")
            sys.exit(1)

    logger.info("qreset Successful")

