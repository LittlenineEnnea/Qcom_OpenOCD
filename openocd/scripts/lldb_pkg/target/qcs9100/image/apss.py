#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file apss.py
#
# @section Description
# This file contains a script for APSS image debugging.

import os
import lldb
import sys
import time
import subprocess
import threading

from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.source_map import setSourceMap

def disable_modem(debugger):
    subprocess.call("adb wait-for-device",shell=True)
    result = subprocess.run("adb devices", stdout=subprocess.PIPE)
    result = str(result.stdout.decode("utf-8"))
    result = result.replace('List of devices attached','').replace('\n','').replace('\r','').replace('\t',' ')
    index = result.index(" ")
    result = result[:index]
    if result:
        result = subprocess.run("adb root", stdout=subprocess.PIPE)
        result = subprocess.run("adb shell mount -o remount,rw /", stdout=subprocess.PIPE)
        result = subprocess.run("adb shell cd /lib/firmware/qcom/qcm6490/ && rm -rf modem*", stdout=subprocess.PIPE)
        result = subprocess.run("adb shell cd /lib/firmware/qcom/qcm6490/ && ls", stdout=subprocess.PIPE)
        result = subprocess.run("adb shell cd /lib/firmware/qcom/qcs6490/ && rm -rf modem*", stdout=subprocess.PIPE)
        result = subprocess.run("adb shell cd /lib/firmware/qcom/qcs6490/ && ls", stdout=subprocess.PIPE)
        logger.info("----- APSS stage1 Completed -----")

def debugAPSS(debugger, command, exe_ctx, result, inputarg = None, Module = None, path = None):
    """!   
    This function is used to define a command to debug APSS, vmlinux is 
    loaded Kernel will stop at "start_kernel" function.

    Usage : debug -d apss -p <vmlinux_path>

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
    args = command.split()
    step = args[5]
    connect_cmd = "connect_remote -s {}".format("apss")

    # if called from inside a script/another function skip this print
    if (inputarg == None):  
        logger.info (" --- Preparing for Debug of APSS  Image ---")
    debugger.HandleCommand("settings set plugin.process.gdb-remote.packet-timeout \
                           200000000")    
    if "vmlinux" in path:
        vmlinux_elf_path = path

    if (os.path.exists(vmlinux_elf_path)):
        g_dbgContext.tgt = debugger.CreateTarget(vmlinux_elf_path)
        time.sleep(8)
    else:
        logger.error("Unable to find vmlinux")
        sys.exit(1)

    # to prevent halting of the debug execution due to exception
    # when incorrect or no source path is set
    try :
        sourcePathSet =  g_dbgContext.sourcePath["apss"]
        setSourceMap(debugger,"apss", sourcePathSet )
    except:
        pass
    debugger.HandleCommand(connect_cmd)
    if step == '2':
        logger.info(" --- FBC: APSS halted in debug mode")
    else:
        target = debugger.GetSelectedTarget()
        frame = target.GetProcess().GetSelectedThread().GetSelectedFrame()
        var = frame.FindVariable("a")
        if var.IsValid():
            frame.EvaluateExpression("a = 1")
        else:
            logger.error("unable to brake out of while loop")
            sys.exit(1)
        t1 = threading.Thread(target = disable_modem, args=(debugger))
        t1.start()
        debugger.HandleCommand("c")
        t1.join()