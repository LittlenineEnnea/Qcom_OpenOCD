#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file meta_info.py
#
# @section Description
# This file contains script for fetching and processing the metabuild, image 
# related and binary related information.

import os
import platform
import re
import sys
from pathlib import Path 
from xml.etree import ElementTree as et
from utils.debug_context import logger
from utils.debug_context import g_dbgContext


class binaryInfo:
    """!
    
    This class is used to get the Binary information related to different 
    images from the meta. 

    """

    def __init__(self, binaryName, metaRelativePath = None, absBinaryPath = None):
        """!

        The binaryInfo object base class initializer.

        @param __binaryName         -   The name of the binary
        @param __metaRelativePath   -   binary path derived based on the metapath
        @param __absBinaryPath      -   absolute binary path set by user
        @param __binaryPath         -   the actual path used in debug based on 
                                        whether metaRelativePath or absBinaryPath is
                                        set
        @param __imgBinList         -   List of binary file names used for debug
        @param __binPathList        -   List of binary file paths used for debug

        @return  An instance of the binaryInfo class.

        """
        self.__binaryName       = binaryName
        self.__metaRelativePath = metaRelativePath 
        self.__absBinaryPath    = absBinaryPath
        self.__binaryPath       = None
        self.__imgBinList       = []
        self.__binPathList      = {}

    def getMetaRelativePath (self):
        """!

        Fetches the relative path of the binary respective to the 
        metapath 

        @return  Returns binary path derived based on the metapath

        """
        return self.__metaRelativePath
    
    def getBinObjInfo (self):
        """!

        Populates and refreshes binary paths when modified by the user

        """

        self.__imgBinList.append(self.__binaryName)

        if ((self.__absBinaryPath != None ) and (self.__binaryName in self.__imgBinList)):
            self.__binaryPath = self.__absBinaryPath
            self.__binPathList [self.__binaryName] = self.__binaryPath
        else :
            self.__binaryPath = self.__metaRelativePath
            self.__binPathList [self.__binaryName] = self.__binaryPath


    def setAbsBinaryPath(self, absPath, binaryName = None):
        """!
        
        To set absolute binary path to any file path and update in 
        binaryInfo Object.

        @param absPath          -   absolute binary path set by user
        @param binaryName       -   The name of the binary

        """

        self.__binaryName = binaryName 
        self.__absBinaryPath = "{}/{}".format(absPath,binaryName)
        self.getBinObjInfo()


 
    def getBinaryPath(self, binaryName = None):
        """!

        This function is used to obtain binary path for given binary.
        
        @param binaryName       -   The name of the binary

        @return  Returns binary path 

        """

        if (binaryName == self.__binaryName):
            return self.__binaryPath


    
class ImageInfo:
    """!
    
    This class is used to get the Image information related to different images from the meta. 

    """
    def __init__ (self, imageName, buildPath):
        """!

        We are initializing, with image name and image path from meta info
        
        @param __imageName          -   The name of the image
        @param __buildPath          -   Image path used for debug 
                                
        @return  An instance of the ImageInfo class.

        """

        self.__imageName        = imageName
        self.__buildPath        = buildPath
        self.__buildFilesInfo   = {}
        self.__binaryInfoList   = {}

    def __getBinaryInfo (self):
        """!

        This function creates a list of binaryInfo objects with default 
        settings based on metapath. This needs to be used to repopulate if any 
        changes are made to the image or binary paths to update the binaryInfo 
        object.

        """

        binFileName = ''

        for fileName in self.__buildFilesInfo:
            relativePath =  self.__buildFilesInfo [fileName]
            binFileName = relativePath.rsplit('/',1)[-1]
            imagePath = self.__buildPath 
            meta_abs_bin_path = "{}{}".format(imagePath, relativePath) 
            # Creating object with default settings
            bin_info = binaryInfo(binFileName, meta_abs_bin_path)
            self.__binaryInfoList [binFileName] = bin_info
            bin_info.getBinObjInfo() 
    
    def setBuildFilesInfo (self, buildFilesInfo):
        """!

        This function modifies entries to buildFilesInfo list which holds list 
        of binaries and relative paths, this is populated while initializing 
        meta object.

        @param buildFilesInfo     -   Holds list of binaries and relative path

        """
        self.__buildFilesInfo = buildFilesInfo
    

    def setImageBasePath (self, imagePath):
        """!

        This function is used to set an engineering build path instead of meta 
        based path for given image.

        @param imagePath     -   Image path used for debug 
        
        """
        self.__buildPath = "{}".format(imagePath)
        self.__getBinaryInfo()

    def getImageBasePath (self):
        """!

        This function is used to fetch image path for given image
        
        @return  Returns image path used for debug
        
        """
        return self.__buildPath.rstrip('/')
    
    def setAbsBinPath(self, binaryPath = None, binaryName = None):
        """!

        This function is used to set absolute binary path to any file path
        and update in binaryInfo Object.

        @param binaryPath       -   absolute binary path set by user
        @param binaryName       -   The name of the binary
                
        """
        
        if binaryPath == None :
            logger.error (" Invalid engineering build path ") 
        for binName in self.__binaryInfoList :

            if (binaryName == binName):
                self.__binaryInfoList[binName].setAbsBinaryPath(binaryPath, binName)

    def getBinaryPath(self, binaryName = None):
        """!
        This function is used to obtain binary path for given binary in an image.

        @param binaryName       -   The name of the binary

        @return  Returns binary path if the binaryname input is valid.
        If not, prints an error log and returns.
                
        """
        if (binaryName in self.__buildFilesInfo):
            binaryPath = self.__binaryInfoList[binaryName].getBinaryPath(binaryName)
            return binaryPath

        else:
            logger.error (" Please provide a valid binary that belongs to the image")
            return 
    
    def getBinInfo(self):   
        """!
        This function is used to fetch information for all the binaries in the 
        meta.
                
        """
        for binaryName in self.__binaryInfo:
            self.__binaryInfoList[binaryName].getBinObjInfo()
    
    def populateBinInfoFromMeta(self):
        """!

        This function is used to reads contents.xml and populate binary
        information, adding it in binaryInfo object as well.    

        """
        self.__getBinaryInfo()

    
class metaInfo:
    """!
    This class is used to access image and binary information belonging to 
    different images from the meta. 
    
    """

    def __init__(self, metaDir, userMeta):
        """!

        We are initializing metaInfo Object with meta build path as input

        @param  metaDir             -   The metadirectory path set by the user
                                
        @return  An instance of the metaInfo class.

        """
        try:
            self.__metaPath     = metaDir.replace ('\\','/')
        except:
            self.__metaPath     = None
        self.__userMeta         = userMeta
        self.__chipsetName      = None
        self.__imageInfo        = {}
        self.__getMetaInfo ()

    def __getMetaInfo (self):
        """!

        This function is used to read contents.xml and populate image and 
        binary related information based on the meta provided.
        It also initialises imageInfo object based on the information
        read from contents.xml.
                                
        """
        if (self.__userMeta == 1):
            contentsXmlFilePath = str(g_dbgContext.contentsXmlPath)
            xmlTree = et.parse (contentsXmlFilePath)
            xmlRoot = xmlTree.getroot()
        else:
            logger.info (" Provide Chipset name using below commands")
            logger.info (" Step-1 Chipset name : set_chipset -s <chipset_name>")
            logger.info (" (optional) Step-2 Meta : meta_dir -s <Meta_path using '/' directory separator>")
            return
        
        # Get Chipset Name 
        productInfo = xmlRoot.find('product_info')
        self.__chipsetName = self.get_chipset_name (xmlRoot)
        
        # Get Image Information
        builds_flat = xmlRoot.find('builds_flat')
        for build in list (builds_flat):
            img_name = build.find ('name').text
            
            opSys = platform.system()
            if opSys == "Windows":
                img_path = build.find ('windows_root_path').text
            else:
                img_path = build.find ('linux_root_path').text
            img_path = img_path.replace ('\\','/')
            fileInfo = {} 
            
            for elem in list (build):
                if (elem.tag == "file_ref"):

                    #   look for file_name in file ref elements of contents.xml
                    fileName = elem.find ('file_name').text 
                    #   extract the file name only by using regex
                    fileName = re.sub (r'\${([^:]+):','', fileName)
                    fileName = re.sub ('}','', fileName)
                    
                    #   look for file_path in file ref elements of contents.xml                    
                    fileDir = elem.find ('file_path').text
                    fileDir = re.sub (r'\${([^:]+):','', fileDir)
                    fileDir = re.sub ('}','', fileDir)
                    
                    #   append "/"" if the filename does not conclude with "/""
                    if (fileDir.endswith("/")):
                        filePath = "{}{}".format(fileDir,fileName)
                    else :
                        filePath = "{}/{}".format(fileDir,fileName)

                    
                    # replacing CLANG* with the actual CLANG version path
                    if "CLANG*" in filePath:
                        tmpIndex = filePath.find("CLANG*")
                        preTmpPath = filePath[:tmpIndex]
                        index = preTmpPath.rfind("/")
                        subString = filePath[(index + 1): tmpIndex]
                        preTmpPath = filePath[: (index + 1)]
                        tmpPath = img_path + preTmpPath
                        try:
                            dirList = os.listdir(tmpPath)
                        except:
                            logger.error(f"path doesn't exist {tmpPath}")
                        for text in dirList:
                            if subString in text:
                                subString = text 
                        postTmpPath = filePath[tmpIndex:]
                        index = postTmpPath.find("/")
                        postTmpPath = postTmpPath[index:]
                        filePath = preTmpPath + subString + postTmpPath

                    fileInfo[fileName] = filePath


            ii = ImageInfo(img_name, img_path) 
            ii.setBuildFilesInfo (fileInfo)  
            self.__imageInfo [img_name] = ii
            ii.populateBinInfoFromMeta()

    def get_chipset_name (self, contents_xml_root):
        """!

        This function is used to read chipset name from contents.xml
        
        @param contents_xml_root - xml tree object for contents.xml

        @return  Returns chipset name

        """
        os_name = platform.system()
        try:
            g_dbgContext.openocdDir = os.environ['OPENOCD_DIR']
        except:
            logger.error ("Please set OPENOCD_DIR environment variable and try again")
            return
        p = Path(g_dbgContext.openocdDir)

        if g_dbgContext.cli is None:
            if os_name == 'Windows':
                tmp = r"\.vscode\settings.json"
                tmp_path = Path(tmp.lstrip("\\/"))
                settings_json_path = p.parent / tmp_path

                with open(settings_json_path) as f:
                    text = f.read()
                match = re.search(r'"CHIPSET"\s*:\s*"([^"]+)"', text)

                if match:
                    chipset_name = match.group(1)
                    chipset_name = chipset_name.lower()
                else:
                    logger.error("CHIPSET not found in settings.json")
            else:
                tmp = r"/.vscode/settings.json"
                tmp_path = Path(tmp.lstrip("\\/"))
                settings_json_path = p.parent / tmp_path

                with open(settings_json_path) as f:
                    text = f.read()
                match = re.search(r'"CHIPSET"\s*:\s*"([^"]+)"', text)

                if match:
                    chipset_name = match.group(1)
                    chipset_name = chipset_name.lower()
                else:
                    logger.error("CHIPSET not found in settings.json")
        else:
            chipset_name = g_dbgContext.chipsetName

        if os_name == 'Windows':
            config_dir_rel_path = r"\scripts\lldb_pkg\target"
            cfg_tmp_path = Path(config_dir_rel_path.lstrip("\\/"))
            config_dir_abs_path = p.parent / cfg_tmp_path
            supported_chipsets = os.listdir(config_dir_abs_path)
        else:
            config_dir_rel_path = r"/scripts/lldb_pkg/target"
            cfg_tmp_path = Path(config_dir_rel_path.lstrip("\\/"))
            config_dir_abs_path = p.parent / cfg_tmp_path
            supported_chipsets = os.listdir(config_dir_abs_path)

        chipset = None
        build_name = 'glue'

        for build in contents_xml_root.findall(".//build"):
            name_node = build.find("name")
            if name_node is not None and (name_node.text or "").strip() == build_name:
                chipset_node = build.find("chipset")
                if chipset_node is not None and chipset_node.text:
                    chipset = chipset_node.text.split(",", 1)
                    chipset = [item.lower() for item in chipset]
                break

        if chipset_name.lower() in chipset:
            chipset = chipset_name
            return chipset
        elif(chipset_name in supported_chipsets):
            chipset = chipset_name
            return chipset
        else:
            logger.warning("chipset and meta doesn't match")
            return
            
    def getMetaPath (self):
        """!

        This function is used to fetch the meta build path used for debug.

        @return  Returns meta build path 
                                
        """
        return self.__metaPath

    def setImageBasePath (self, imageName, imagePath):
        """!

        This function is used to set engineering build path instead of metapath
        for given image.

        @param imageName            -   The name of the image
        @param imagePath            -   Image path used for debug 
                                
        """
        imageName = imageName.lower()
        try :
            ii = self.__imageInfo [imageName]
            ii.setImageBasePath(imagePath)
        except :
            logger.error("Please check if the image name and image path are valid.")


    def getImageBasePath (self, imageName):
        """!

        This function is used to fetch image path for given image name.

        @param imageName            -   The name of the image
        
        @return  Returns image path if the image name input is valid.
        If not, prints an error log and returns.            
        """
        imageName = imageName.lower()
        
        try :
            ii = self.__imageInfo [imageName]
            return ii.getImageBasePath()
        except :
            logger.error("Invalid image name")
            return "ERROR"
        

    def getChipsetName (self):

        """!

        This function is used to fetch the chipset name used for debug.

        @return  Returns chipset name
                                
        """
        return self.__chipsetName

    def setAbsBinaryPath (self, imageName, binaryPath = None, binaryName = None):
        """!

        This function is used to set absolute binary path to any file path and
        update in binaryInfo object.

        @param imageName        -   The name of the image which the binary
                                    belongs to.
        @param binaryPath       -   absolute binary path set by user
        @param binaryName       -   The name of the binary
                                                
        """
        imageName = imageName.lower()
        imageInfo = self.__imageInfo [imageName]
        imageInfo.setAbsBinPath( binaryPath, binaryName)
        

    def getBinaryPath(self, imageName = None, binaryName = None):
        """!
        This function is used to obtain binary path for given binary in an 
        image.

        @param imageName        -   The name of the image
        @param binaryName       -   The name of the binary

        @return  Returns binary path if the imagename and binaryname input
        is valid.
        If not, prints an error log and returns.
                
        """

        imageName = imageName.lower()
        try :
            imageInfo = self.__imageInfo [imageName]
            binaryPath = imageInfo.getBinaryPath(binaryName)
            return binaryPath
        except:
            logger.error("Invalid image name")
            return "ERROR"
        
    def getImgBinInfo(self):
        """!
        This function is used to fetch information for all the binaries in the
        meta.
                
        """
        for index in self.__imageInfo:
            self.__imageInfo[index].getBinInfo()