#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file image_path.py
#
# @section Description
# This file contains script for setting and fetching the image path.

import os

from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from pathlib import PurePosixPath


def GetImagePath(debugger, command, exe_ctx, result, image, imagepath):
    if (imagepath == None):
        result = g_dbgContext.openocdConfig.getImageBasePath(image)
        # log error is printed as a part of exception handling in meta info
        if (result == "ERROR"):
            return
        else : 
            logger.info(" Path is set to  : {}".\
                         format(g_dbgContext.openocdConfig.getImageBasePath(image)))
        
        return


def SetImagePath(debugger, command, exe_ctx, result, image, imagepath):  
    valid_path = os.path.exists(imagepath)
        
    if (valid_path):
        imagepath = str(PurePosixPath(imagepath)) + "/"
        g_dbgContext.openocdConfig.setImageBasePath (image, imagepath)
        result = g_dbgContext.openocdConfig.getImageBasePath (image)
        if (result  == None):
            logger.info (" Invalid Image name \'{}\'".format(image))
            return
        else :  
            logger.info(" Image Path set to : {}".format(imagepath))
            return
    else :
        logger.error (" Please provide valid Image path ")
        return 