#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file openocd_config.py
#
# @section Description
# This file contains script for configuring OpenOCD.

import os
import logging
import sys
import utils.chipset_cfg as chipset_cfg
import utils.meta_info as meta_info  
from utils.debug_context import g_dbgContext 
from utils.debug_context import logger

class openocdConfig:

    """!
    This class is used to get the chipset information from contents.xml for running openocd.     
    """

    def __init__ (self, metaDir, userMeta):
        
        """!

        The openocdConfig object base class initializer.

        @param metaDir          -   The metadirectory path set by the user
        @param userMeta         -   This denotes if metapath was set by user
                                    or not.

        """
        self.openocdPath        =   None
        try:
            self.metaDir        =   metaDir.replace ("\\","/")
        except:
            self.metaDir        =   None
            pass
        self.userMeta           =   userMeta
        self.__metaInfo         =   meta_info.metaInfo(metaDir, userMeta)
        self.__chipConfigInfo   =   None
        self.__regListInfo      =   None
        self.contentXmlPath     =   str(g_dbgContext.contentsXmlPath)
        self.initConfig()

    def initConfig(self):
        """!
        This function is used to  determine where contents.xml, chip_config.xml
        and reg_list.xml can be found to be used for debug. This is done based
        on set metadirectory path.
        """

        self.chipsetName = self.__metaInfo.getChipsetName()
        relativeScriptPath = '/common/Core/openocd/scripts'

        if ((os.path.exists(self.contentXmlPath)) & (self.userMeta == 0)):
            configXmlPath = "{}".format(self.metaDir, relativeScriptPath)
            configXmlPath = os.path.exists(configXmlPath)
            
            if (configXmlPath == True):
                self.setChipsetConfig (configXmlPath)
            else:
                try :
                    self.openocdPath = os.environ['OPENOCD_DIR']
                except : 
                    logging.error ("Please set OPENOCD_DIR environment variable")
                    logger.error("Stop & restart debug session")
                    sys.exit(1)
                
                self.openocdPath = self.openocdPath.replace("bin","scripts")
                self.openocdPath = self.openocdPath.replace('\\','/')
                self.setChipsetConfig (self.openocdPath)

        else:
            try :
                self.openocdPath = os.environ['OPENOCD_DIR']
            except : 
                logging.error (" Please set OPENOCD_DIR environment variable")
                logger.error("Stop & restart debug session")
                sys.exit(1)
            self.openocdPath = self.openocdPath.replace("bin","scripts")
            self.openocdPath = self.openocdPath.replace('\\','/')
            if (self.userMeta == 0):
                return
            if (self.userMeta == 1):
                self.setChipsetConfig (self.openocdPath)
        return

    def setChipsetConfig(self, openocdPath = None):
        """!
        This function is used to set the xml paths for chip_config and 
        reg_list used for debug

        @param openocdPath      -       The file path where chip_config.xml
                                        and reg_list.xml can be found

        """
        self.openocdPath = self.openocdPath.replace('scripts','')
        self.__chipConfigInfo = chipset_cfg.ChipsetConfig\
            (self.chipsetName, '{}/scripts/lldb_pkg/target/{}/chip_config.xml'.format(self.openocdPath, self.chipsetName.lower()))

        self.__regListInfo = chipset_cfg.ChipsetConfig\
            (self.chipsetName, '{}/reg_list.xml'.format(openocdPath))
        return

    def setImageBasePath (self, imageName, imagePath):
        """!
        This function is used to set the image paths for given valid image name
        used for debug

        
        @param imageName            -   The file path where chip_config.xml
                                        and reg_list.xml can be 
        @param imagePath            -   Image path used for debug 


        """
        self.__metaInfo.setImageBasePath (imageName, imagePath)


    def getImageBasePath (self, imageName):
        """!
        This function is used to fetch image path for given image.

        @param imageName            -   The name of the image

        @return  Returns image path if the image name input is valid.
        If not, prints an error log and returns. 

        """
        return self.__metaInfo.getImageBasePath (imageName)
    
    def getMetaPath (self):
        """!
        This function is used to fetch the metapath that MetaInfo object 
        was initialised with.

        @return  Returns meta build path 


        """
        return self.__metaInfo.getMetaPath()
    
    def setAbsBinaryPath (self, imageName, imagePath, binaryName = None):
        """!

        This function is used to set absolute binary path to any file path and
        update in binaryInfo object.

        @param imageName        -   The name of the image which the binary
                                    belongs to.
        @param binaryPath       -   absolute binary path set by user
        @param binaryName       -   The name of the binary
                                                
        """
        self.__metaInfo.setAbsBinaryPath (imageName, imagePath, binaryName)

    def getBinaryPath(self, imageName, binaryName):
        """!
        This function is used to obtain binary path for given binary in an 
        image.

        @param imageName        -   The name of the image
        @param binaryName       -   The name of the binary

        @return  Returns binary path if the imagename and binaryname input
        is valid.
        If not, prints an error log and returns.
                
        """
        return self.__metaInfo.getBinaryPath(imageName, binaryName)
    
    
    def getChipConfigInfo (self):
        """!
        This function returns the path where chip_config.xml file is stored

        @return     Returns the path of chip_config.xml

        """
        return self.__chipConfigInfo.xmlPath
    
    def getRegListInfo (self):
        """!
        This function returns the path where reg_list.xml file is stored

        @return     Returns the path of reg_list.xml


        """
        return self.__regListInfo.xmlPath     
    
