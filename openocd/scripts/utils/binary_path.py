#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file binary_path.py
#
# @section Description
# This file contains a script for setting the image binary path.

import os

from utils.debug_context import g_dbgContext
from utils.debug_context import logger


def SetBinaryPath(debugger, command, exe_ctx, result, image, binarypath):
    binarypath = binarypath.replace ("\\","/")

    valid_path = os.path.isfile(binarypath)
    binFileName = binarypath.rsplit('/',1)[-1]
    binarypath = os.path.dirname(binarypath)

    if (valid_path):
        g_dbgContext.openocdConfig.setAbsBinaryPath(image, binarypath, binFileName)

        result = g_dbgContext.openocdConfig.getBinaryPath(image, binFileName)
        if (result  == None):
            logger.error (" Invalid Image name \'{}\'".format(image))
            return
        else :  
            logger.info(" Binary Path set to : {}/{}".format(binarypath, binFileName))
            return
    else :
        logger.error (" Please provide valid binarypath and elf name")
        return