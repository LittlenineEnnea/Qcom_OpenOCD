#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file debug_context.py
#
# @section Description
# This file defines a class with an __init__ method to initialize 
# chipset-related information from chip_config.xml, and other methods 
# for setting the required paths for chipset debugging.

import xml.etree.ElementTree as ET
import logging
import sys

class debugContext :
    """!
    
    This class provides access to variables used across functions. 

    """

    def __init__(self):
        """!

        The debugContext object base class initializer.

        @return  An instance of the debugContext class.

        """

        ## gdb connection parameters
        self.port               =   None
        self.gdbRemoteHost      =   None
        self.monitorPrefix      =   'process plugin packet monitor'
        self.chipsetName        =   ""

        ## openocdConfig object 
        self.openocdConfig      =   None
        ## telnet handle
        self.telnetHandle       =   None
        ## xml file info
        self.chipInfo           =   None
        self.regInfo            =   None
        self.contentsXmlPath    =   None
        self.userMeta           =   None

        # uefi dbg context variables
        self.uefiDbgContext     =    {} 
        self.category           =   []
        self.tgt                =   None
        self.getDbgContextUEFI()

        # logging handler
        self.logger             =   None
        self.tempDirectory      =   None
        self.openocdDir         =   None
        self.sourcePath         =   {}

        # Debug adapter Info
        self.debug_adapter = None
        self.debug_protocol = None

        self.cur_time = None
        self.pre_time = None
        self.cli = None
     
    def setPathForXML(self, chipInfo = None, regListInfo = None): 
        """!

        This function  is used to source paths for chipInfo and regInfo paths.

        @param chipInfo         -   The chip_config.xml path 
        @param regListInfo      -   The reg_list.xml path 

        """
        self.chipInfo = chipInfo
        self.regInfo = regListInfo
    
    def getInfoFromXML (self):
        """!

        This function is used to fetch xml elements as chipInfo and regInfo 
        and use as global variables

        @param chipInfo         -   The chip_config.xml path 
        @param regListInfo      -   The reg_list.xml path 

        """

        if (self.chipInfo and self.regInfo):
            chipConfigPath = self.chipInfo
            file = ET.parse(str(chipConfigPath))
            self.chipInfo = file.getroot()

            regListPath = self.regInfo
            regListFile = ET.parse(str(regListPath))
            self.regInfo = regListFile.getroot()

        else :
            logger.error("unable to fetch chip_config.xml and reg_list.xml")


    def getDbgPortNum(self,  subsystem = None ):
        """!

        This function is used to fetch debug port number for given subsystem
        from chip_config.xml

        @param subsystem        -   The subsystem to be debugged for which 
                                    gdb debug port number will be fetched

        """
        try : 
            for chip in self.chipInfo.findall('chipset'):
                chipname=chip.get('name')
                if (chipname.upper() == self.chipsetName.upper()):
                    portConfig = chip.find('arch')
                    port = portConfig.find(str(subsystem).lower())
                    port_num = int(port.attrib['gdbPort'])
                    self.port = port_num
            return port_num
        except :
            logger.error(" invalid port information")
            sys.exit(1)
 
    def getGdbRemoteHost(self):
        """!

        This function is used to fetch the configured GDB_REMOTE_HOST value,
        set via the "gdb_remote_host" command, so that gdb-remote/telnet
        connections can target an OpenOCD instance running on a different
        machine. Defaults to "localhost" when never set, preserving today's
        co-located behavior.

        @return  Returns the configured remote host, or "localhost"

        """
        return self.gdbRemoteHost or "localhost"

    def setContentsXmlPath(self, xmlPath = None):
        """!

        This function is used to set contents.xml path as contentsXmlPath
        and use as global variables

        @param xmlPath        -     The file path where contents.xml can be 
                                    found

        """
        self.contentsXmlPath = xmlPath
     
    def getContentsXmlPath(self):
        """!

        This function is used to get contents.xml path

        @return Returns the file path where contents.xml can be found

        """
        return self.contentsXmlPath
     
    def getMonitorPrefix (self):
        """!

        This function is used to fetch global variable monitorPrefix

        @return Returns the prefix string used for the monitor command

        """
        return self.monitorPrefix
 
    def getDbgContextUEFI (self):  
        """!
        This function is used to fetch uefi debug context for sec and dxe 
        binaries

        """      
        self.uefiDbgContext['UEFI_Sec'] = None
        self.uefiDbgContext['UEFI_Dxe'] = None
     
    def setDbgContextUEFI (self, uefiDbgParam, value ):
        """!
        This function is used to set uefi debug context for sec and dxe
        binaries

        """
        self.uefiDbgContext[uefiDbgParam] = value
    
class CustomFormatter(logging.Formatter):
    """
    Defines a custom formatter class: CustomFormatter inherits from 
    logging.Formatter.
    """
    # Sets ANSI escape codes for red color and reset: RED is the escape code 
    # for red text, and RESET is the escape code to reset text formatting.
    RED = "\033[91m"
    RESET = "\033[0m"
    FORMAT = "%(levelname)s: %(message)s"

    # Overrides the format method: This method formats the log record.
    def format(self, record):
        log_fmt = self.FORMAT
        if record.levelno == logging.ERROR:
            log_fmt = self.RED + self.FORMAT + self.RESET
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

# Enabling stdout with logging
logger = logging.getLogger("openocd_pkg")
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(CustomFormatter())
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)    

# Initializes an instance of debugContext Object
g_dbgContext = debugContext()

