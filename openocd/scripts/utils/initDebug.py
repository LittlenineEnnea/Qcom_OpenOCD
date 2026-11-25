#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file initDebug.py
#
# @section Description
# This file contains a script to initiate debugging using the input meta path, 
# instantiate the openocdConfig object, and source chipset-specific and 
# register-specific configuration values from XML files.

import os
import platform
import sys
import utils.openocd_config as openocd_config

from inspect import getsourcefile
from os.path import abspath
from utils.debug_context import g_dbgContext
from utils.debug_context import logger


def initDebug (inputMetaDir, userMeta):
    """!   
    This function is used to initiate debug with the input meta path and also
    to instantiate the openocdConfig object and also source chipset specific and
    register specific configuration values from xml files.

    @param inputMetaDir     -   The metadirectory path set by the user
    @param userMeta         -   This denotes if metapath was set by user
                                or not. Since by default we start with metapath
                                as  "" and userMeta as 0 which denotes that the
                                metapath was not set by user.

    """
    if userMeta:
        g_dbgContext.userMeta = 1
        if(os.path.exists(inputMetaDir)):
            g_dbgContext.contentsXmlPath = "{}/contents.xml".format(inputMetaDir)
            if(os.path.exists(str(g_dbgContext.contentsXmlPath))):
                logger.info("contents.xml is found")
                pass
            else:
                logger.error("Unable to access contents.xml, provide a valid \
meta path")
                logger.error("Stop & restart debug session")
                sys.exit(1)
        else:
            logger.error("meta path doesn't exists, provide a valid \
meta path")
            logger.error("Stop & restart debug session")
            sys.exit(1)
    osName = platform.system()
    if (osName == 'Windows'):
        g_dbgContext.tempDirectory = "C:/Temp/.openocd/"
    elif (osName in ('Linux', 'Darwin')):
        g_dbgContext.tempDirectory = "/tmp/.openocd/"

    # Default OPENOCD_DIR to <package>/bin (next to scripts/) when it is not
    # exported, e.g. VS Code on macOS was already running before
    # launch_vscode.sh exported it. Child processes inherit it from here.
    os.environ.setdefault('OPENOCD_DIR', os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'bin'))

    if (os.path.exists(g_dbgContext.tempDirectory) == False):
        try:
            os.mkdir(g_dbgContext.tempDirectory)
        except:
            logger.error("Directory creation failed : {}".format(g_dbgContext.tempDirectory))

    try:
        g_dbgContext.openocdDir = os.environ['OPENOCD_DIR']
    except:
        logger.error (" Please set OPENOCD_DIR environment variable and try again")
        return
    if (osName == 'Windows'):
        telematics_pkg_path = g_dbgContext.openocdDir.replace("\\bin","\\scripts\\ext_pkg")
    elif (osName == 'Linux'):
        telematics_pkg_path = g_dbgContext.openocdDir.replace("bin","scripts/ext_pkg")
    elif (osName == 'Darwin'):
        telematics_pkg_path = os.path.join(os.path.dirname(g_dbgContext.openocdDir.rstrip('/')), "scripts", "ext_pkg")
    sys.path.insert(0, telematics_pkg_path)
    currentDir = os.getcwd()
    # Get absolute path of openocd_gdb/lldb.py file
    scriptFile = abspath (getsourcefile(lambda:0))
    scriptsRootDir = os.path.dirname (scriptFile)
    os.chdir (scriptsRootDir)
    if inputMetaDir:
        metaDir = inputMetaDir
    else:
        metaDir = None
        g_dbgContext.userMeta = 0

    g_dbgContext.openocdConfig = openocd_config.openocdConfig(metaDir, g_dbgContext.userMeta)
    os.chdir (currentDir)
    
    if (inputMetaDir != ""):
        chipConfigPath = g_dbgContext.openocdConfig.getChipConfigInfo()
        regListPath = g_dbgContext.openocdConfig.getRegListInfo()
        if (os.path.exists(g_dbgContext.openocdConfig.contentXmlPath)):
            g_dbgContext.chipsetName = g_dbgContext.openocdConfig.chipsetName.lower()
            g_dbgContext.setPathForXML (chipConfigPath, regListPath)
            g_dbgContext.getInfoFromXML ()