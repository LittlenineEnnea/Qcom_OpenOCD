#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file repair_bin_generate.py
#
# @section Description
# This file contains script for generating repair binaries for source code 
# viewing.

import os
import shutil
import platform
from utils.debug_context import g_dbgContext

def repairBinGenerate (origElfPath, fileName):
    """!   
    This function is used to generate repair binaries that support source code 
    view. 

    @param origElfPath       -   original binarypath from the meta 
    @param  fileName         -   binary name of the file that needs fix
                               
    """
    
    copiedFile =  "{}{}".format( g_dbgContext.tempDirectory, fileName)
    shutil.copy(origElfPath, copiedFile)
    osName = platform.system()
    if (osName == 'Windows'):
        repairBin = os.path.join(g_dbgContext.openocdDir, "ranges_repair.exe")
    elif (osName in ('Linux', 'Darwin')):
        repairBin = os.path.join(g_dbgContext.openocdDir, "ranges_repair")
    os.system("{} {}".format(repairBin, copiedFile))
    newFilePath =  "{}{}.repair".format( g_dbgContext.tempDirectory, fileName)
    elf_path = newFilePath
    return elf_path