#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file systemd.py
#
# @section Description
# This file contains methods which facilitate SYSTEMD debugging.

import os
import time
import lldb

from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from lldb_pkg.target.qcm6490.image.uefi import *
from utils.source_map import setSourceMap

def debugSYSTEMD(debugger, command, exe_ctx, result, inputarg = None, Module = None, path = None, step = None):
    """!   
    This function is used to define a command to debug Systemd, systemd-bootaa64.elf is 
    loaded and breakpoints are set at "efi_stub" for systemd-boot and systemd-stub and 
    for systemd-efi-stub breakpoint is set at efi_pe_entry.

    Usage : debug -d systemd -p <symbol_file_path>

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for 
                                debugging.
    @param command          -   String containing all the input arguments
                                for the used command. 
    @param exe_ctx          -   An execution context object carrying 
                                information of the process 
                                context on which the command is used.
    @param result           -   A return object which encapsulates 
                                success/failure information for the command
                                and output text that needs to be printed 
                                as a result of the command.

    @param inputarg         -   a variable used to denote whether the command
                                is being used from inside the script or being
                                used from lldb (by the user).

    """

    if (inputarg == None):
        logger.info (" --- Preparing for Debug of Systemd Image ---")
    debugger.HandleCommand("settings set plugin.process.gdb-remote.packet-timeout \
                           1000000000")

    if (Module == None):
        debugUEFI(debugger, command, exe_ctx, result, "run", None)
        debugUEFI(debugger, command, exe_ctx, result, "run", 'qcombds')
        debugger.HandleCommand("br del")
        debugger.HandleCommand("br s -n BdsStartEfiApplication -H")
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")
        debugger.HandleCommand("br s -n CoreLoadImageCommon -H")
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")
        debugger.HandleCommand("br s -n CoreLoadPeImage -H")
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")
        debugger.HandleCommand("br s -n PeCoffLoaderLoadImage -H")
        debugger.HandleCommand("c")
        frame = lldb.debugger.GetSelectedTarget().GetProcess().GetSelectedThread().GetSelectedFrame()
        systemd_offset = frame.EvaluateExpression("ImageContext.ImageAddress")
        systemd_offset = hex(int(systemd_offset.GetValue(), 10))
        if "systemd-bootaa64" in path:
            systemd_elf_path = path
        if os.path.exists(systemd_elf_path):
            systemd_dSYM_cmd = "{}.dSYM".format(systemd_elf_path)
            module_no = g_dbgContext.tgt.AddModule (systemd_elf_path, None, None, systemd_dSYM_cmd)
            g_dbgContext.tgt.SetModuleLoadAddress (module_no, int(systemd_offset, 16))
            time.sleep(3)
            # to prevent halting of the debug execution due to exception
            # when incorrect or no source path is set
            try:
                sourcePathSet =  g_dbgContext.sourcePath["systemd-boot"]
                if sourcePathSet:
                    setSourceMap(debugger,"systemd-boot", sourcePathSet )
            except:
                pass
        else:
            logger.error("Unable to find systemd-bootaa64.elf")
            sys.exit(1)
        debugger.HandleCommand("br del")
        debugger.HandleCommand("br s -n efi_main -H")
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")
        logger.info (" --- FBC: Systemd-boot entrypoint reached in debug mode")
    
    if Module == "systemd-stub":
        debugger.HandleCommand("br s -n PeCoffLoaderRelocateImage -H")
        debugger.HandleCommand("c")
        frame = lldb.debugger.GetSelectedTarget().GetProcess().GetSelectedThread().GetSelectedFrame()
        systemd_offset = frame.EvaluateExpression("ImageContext.ImageAddress")
        systemd_offset = hex(int(systemd_offset.GetValue(), 10))
        if "linuxaa64" in path:
            systemd_elf_path = path
        if (os.path.exists(systemd_elf_path)):
            systemd_dSYM_cmd = "{}.dSYM".format(systemd_elf_path)
            module_no = g_dbgContext.tgt.AddModule (systemd_elf_path, None, None, systemd_dSYM_cmd)
            g_dbgContext.tgt.SetModuleLoadAddress (module_no, int(systemd_offset, 16))   
            time.sleep(3)
            # to prevent halting of the debug execution due to exception
            # when incorrect or no source path is set
            try:
                sourcePathSet =  g_dbgContext.sourcePath["systemd-stub"]
                if sourcePathSet:
                    setSourceMap(debugger,"systemd-stub", sourcePathSet )
            except:
                pass
        else:
            logger.error("Unable to find linuxaa64.elf.stub")
            sys.exit(1)
        debugger.HandleCommand("br del")
        debugger.HandleCommand("br s -n efi_main -H")
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")
        logger.info (" --- FBC: Systemd-stub entrypoint reached in debug mode")

    if Module == "efi-stub":
        base_offset = "0x3f80000000"
        debugger.HandleCommand("br s -n CoreStartImage -H")
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")
        debugger.HandleCommand("br s -n RegisterMemoryProfileImage -H")
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")
        debugger.HandleCommand ('up')
        frame = lldb.debugger.GetSelectedTarget().GetProcess().GetSelectedThread().GetSelectedFrame()
        linux_efi_offset = frame.EvaluateExpression("Image.ImageContext.ImageAddress")
        linux_efi_offset = int(linux_efi_offset.GetValue(), 10)
        base_offset = int(base_offset, 16)
        offset = base_offset + linux_efi_offset
        if "vmlinux" in path:
            vmlinux_elf_path = path
        if (os.path.exists(vmlinux_elf_path)):
            vmlinux_dSYM_cmd = "{}.dSYM".format(vmlinux_elf_path)
            module_no = g_dbgContext.tgt.AddModule (vmlinux_elf_path, None, None, vmlinux_dSYM_cmd)
            g_dbgContext.tgt.SetModuleLoadAddress (module_no, offset)
            time.sleep(3)
            # to prevent halting of the debug execution due to exception
            # when incorrect or no source path is set
            try:
                sourcePathSet =  g_dbgContext.sourcePath["efi-stub"]
                if sourcePathSet:
                    setSourceMap(debugger,"efi-stub", sourcePathSet )
            except:
                pass
        else:
            logger.error("Unable to find vmlinux")
            sys.exit(1)
        debugger.HandleCommand("br s -n efi_pe_entry -H")
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")
        logger.info (" --- FBC: Systemd-efi-stub entrypoint reached in debug mode")


    