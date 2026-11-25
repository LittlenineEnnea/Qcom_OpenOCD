#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file spmi_core_cfg.py
#
# @section Description
# This script contains functions to configure and initialize SPMI
# (System Power Management Interface) settings specific to Bonito target.

import re
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.hwio import g_hwioConfig
from utils.telnet import *

class spmi_cfg_obj :
    """!
    This class handles the configuration for SPMI (System Power Management 
    Interface).

    @param file_path - Path to the configuration file.
    """
    def __init__(self, file_path):
        """!
        Constructor for spmi_cfg_obj class.

        Initializes the object with the provided file path and sets default
        values for various attributes.

        @param file_path - Path to the configuration file.
        """
        self.file_path = file_path
        self.tmp = ""
        self.channel_count = ""
        self.num_pvc_ports = ""
        self.num_priorities = ""
        self.num_mgpi_ports = ""
        self.master_id = ""

        self.file_path = ""
        self.text_file_path_root = ""
        self.derive_file_path("")

    def extract_value(self, line):
        """!
        Extracts numerical value from a given line of text.

        This function removes all alphabetic characters from the line, strips 
        whitespace, and converts the remaining string to an integer.

        @param line - A string from which the numerical value is to be extracted.
        @return Integer value extracted from the line.
        """
        return int(re.sub(r'[A-Za-z]', '', line).strip().rstrip('.')) if line else None

    def read_meta_text_file(self, file_path=None):
        """!

        Reads and extracts values from a meta text file.

        This function reads the meta text file specified by the file path and extracts 
        values for channel count, number of PVC ports, number of priorities, number 
        of MGPI ports, and master ID.

        @param file_path - Optional path to the meta text file. 
        If not provided, the existing file path is used.

        """
        if file_path is not None:
            self.file_path = file_path

        with open(self.file_path, 'r') as file:


            # Reading values from meta.txt file :
            self.channel_count = self.extract_value(file.readline().strip())
            self.num_pvc_ports = self.extract_value(file.readline().strip())
            self.num_priorities = self.extract_value(file.readline().strip())
            self.num_mgpi_ports = self.extract_value(file.readline().strip())
            self.master_id = self.extract_value(file.readline().strip())

    def derive_file_path(self, file_name = ""):
        """!
        Derives the file path for a given file name.

        This function constructs the file path based on the meta path and chipset 
        name from the debug context configuration.

        @param file_name - Name of the file for which the path is to be derived.
        @return The derived file path as a string.
        """
        # this object is initialized when the script is imported as part of init 
        # adding this check since openocdConfig object is not initialized till 
        # meta_path is set correctly
        if (g_dbgContext.openocdConfig != None):
            meta_path = g_dbgContext.openocdConfig.getMetaPath()
            chipset_name = g_dbgContext.chipsetName.lower()

            self.text_file_path_root =  "{}/common/Core/tools/{}/tools/pmic/spmi/{}".\
                format(meta_path,chipset_name,chipset_name)
            self.file_path = "{}/{}".format(self.text_file_path_root,file_name)

            return self.file_path    

def spmi_core_cfg(debugger, command, exe_ctx, result): 
    """!
    This function configures the SPMI core settings by loading necessary 
    configuration files.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """
    # derive txtfile_paths from meta
    hwio_path = g_spmi_cfg.derive_file_path('hwio.cmm')
    aoss_path = g_spmi_cfg.derive_file_path('aoss_cc.cmm')

    # loading hwio.cmm
    g_hwioConfig.hwio_access(debugger, command, result, hwio_path, \
        None, None, None)
    # loading aoss_cc.cmm
    g_hwioConfig.hwio_access(debugger, command, result, aoss_path, \
        None, None, None)

    #Initial configuration of SPMI clocks
    spmi_clock_enable(debugger, command, exe_ctx, result)
    spmi_master_init(debugger, command, exe_ctx, result)
    
    return


#Initial configuration of SPMI clocks- in a seperate file
def spmi_clock_enable (debugger, command, exe_ctx, result):
    """!
    This function enables the SPMI clocks.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """
    tln = g_dbgContext.telnetHandle
    logger.info (" --- SPMI CLOCK ENABLEMENT ...")
    HWIO_AOSS_TEST_BUS_CTRL_ADDR = 0x0C260208
    
    dapAccessTelnet(debugger,command, result, tln, None, None, None, \
        HWIO_AOSS_TEST_BUS_CTRL_ADDR, 0x80000000)

    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_AOSS_CC_SPMI_BCR_ADDR', 0x0)
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_AOSS_CC_SPMI2_BCR_ADDR', 0x0) 
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_AOSS_CC_SPMI_CFG_AHB_CBCR_ADDR', 0x1)
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_AOSS_CC_SPMI_SER_CBCR_ADDR', 0x1)    
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_AOSS_CC_SPMI_AOD_SER_CBCR_ADDR', 0x1)   
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_AOSS_CC_SPMI2_SER_CBCR_ADDR', 0x1)  
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_AOSS_CC_SPMI2_AOD_SER_CBCR_ADDR', 0x1)  

def spmi_master_init(debugger, command, exe_ctx, result):
    """!
    This function initializes the SPMI master.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """
    logger.info (" --- SPMI MASTER INIT ...")
    # // 1. Write SPMI GENI FW image
    # ;GOSUB CHECK_GENI_IMG true

    # 2. Initiate FORCE-DEFAULT command
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_GENI_FORCE_DEFAULT_REG_ADDR', 0x1)  

    # 3. Switch PROGRAM-RAM clock muxing to serial clock
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_GENI_CLK_CTRL_ADDR', 0x1)  

    #  4. Enable all security features
    read_value = g_hwioConfig.hwio_read(debugger, command, result,\
         'HWIO_SPMI_SEC_DISABLE_REG_ADDR')
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_SEC_DISABLE_REG_ADDR', 0x0)  

    # // 5. Disable forced High-Z driven to GENI outputs
    # // SOE0_EN
    read_value = g_hwioConfig.hwio_read(debugger, command, result, \
        'HWIO_SPMI_GENI_OUTPUT_CTRL_ADDR')
    read_value = read_value | 0x1
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_GENI_OUTPUT_CTRL_ADDR', read_value)  

    read_value = g_hwioConfig.hwio_read(debugger, command, result, \
        'HWIO_SPMI_GENI_OUTPUT_CTRL_ADDR')
    read_value = read_value | 0x2
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_GENI_OUTPUT_CTRL_ADDR', read_value)  

    # Configure SPMI_MID_REG register

    meta_file_path = g_spmi_cfg.derive_file_path('meta.txt')
    g_spmi_cfg.read_meta_text_file(meta_file_path)


    if (g_spmi_cfg.master_id == 1):
        g_hwioConfig.hwio_write( debugger, command, result, \
            'HWIO_SPMI_MID_REG_ADDR', g_spmi_cfg.master_id)  
    else:
        g_hwioConfig.hwio_write( debugger, command, result, \
            'HWIO_SPMI_MID_REG_ADDR', 0x0)  

    #  7. Multi-master settings - set ASYNC_SSC_DET_INT_DIS_BMSK, ARB_UNKNOWN_EN_BMSK, 
    read_value = g_hwioConfig.hwio_read(debugger, command, result, \
        'HWIO_SPMI_CFG_REG_ADDR')
    read_value = read_value | 0x600BA8
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_CFG_REG_ADDR', read_value)  
    
    # subroutine to check GENI IMG
    check_img_geni(debugger,command, result)

    #  13. Enable interrupts globally
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_MWB_ENABLE_REG_ADDR', 0x1)  

    #  14. Change ROM and RAM mux to output RAM Data.
    read_value = g_hwioConfig.hwio_read(debugger, command, result, \
        'HWIO_SPMI_GENI_PROG_ROM_CTRL_REG_ADDR')
    read_value = 0
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_GENI_PROG_ROM_CTRL_REG_ADDR', read_value)  
        
    # 15. Enabling HW control CGC
    read_value = g_hwioConfig.hwio_read(debugger, command, result, \
        'HWIO_SPMI_GENI_CGC_CTRL_ADDR')
    read_value = read_value | 0xFF
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_GENI_CGC_CTRL_ADDR', read_value)  

    read_value = g_hwioConfig.hwio_read(debugger, command, result, \
        'HWIO_SPMI_CGC_CTRL_ADDR')
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_CGC_CTRL_ADDR', 0x7F)  

    # ARBITER_ENABLE
    read_value = g_hwioConfig.hwio_read(debugger, command, result, \
        'HWIO_SPMI_CFG_REG_ADDR')
    read_value = read_value | 0x1
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_CFG_REG_ADDR', read_value)

def check_img_geni(debugger,command, result):
    """!
    This function checks and updates the GENI (Generic Interface)
     image by reading and writing register values.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """
    # reading register addresses and values 

    clock_value = g_hwioConfig.hwio_read(debugger, command, result, \
        'HWIO_SPMI_GENI_CLK_CTRL_ADDR')
    geni_cgc_val = g_hwioConfig.hwio_read(debugger, command, result, \
        'HWIO_SPMI_GENI_CGC_CTRL_ADDR')
    spmi_cgc_val = g_hwioConfig.hwio_read(debugger, command, result, \
        'HWIO_SPMI_CGC_CTRL_ADDR')

    # writing initial values for these registers
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_GENI_CLK_CTRL_ADDR', 0x0)
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_GENI_CGC_CTRL_ADDR', 0x78)
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_CGC_CTRL_ADDR', 0x0)

    geni_file_path = g_spmi_cfg.derive_file_path('geni.txt')

    with open(geni_file_path, 'r') as file:
        for line in file:
            file_value = line.strip()

            if file_value == "":
                continue
            elif file_value == "REG":
                i = 0
                base = "HWIO_SPMI_GENI_CFG_REG"
                logger.info("REGISTER                        |   CURRENT VALUE   ")
                logger.info("----------------------------------------------------")
            elif file_value == "RAM":
                i = 0
                base = "HWIO_SPMI_GENI_CFG_RAM"
                logger.info("")
                logger.info("REGISTER                        |   CURRENT VALUE   ")
                logger.info("----------------------------------------------------")
            else:
                register_name = f"{base}{i}_ADDR"
                offset = (4 *i)
                read_value = g_hwioConfig.hwio_read(debugger, command, result, \
                    register_name, offset)
                hwio_reg_str = f"{base}{i}_ADDR\t"

                if file_value != read_value:
                    logger.info(f"{hwio_reg_str} Updated value: {file_value}")
                    file_value = int(file_value, 16)
                    g_hwioConfig.hwio_write( debugger, command, result, register_name ,\
                         file_value)

                else :
                    logger.info(f"{hwio_reg_str} Correct value: {file_value}")

                    pass
                i += 1
                
    # writing the previously read values
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_GENI_CLK_CTRL_ADDR',clock_value)
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_GENI_CGC_CTRL_ADDR', geni_cgc_val)
    g_hwioConfig.hwio_write( debugger, command, result, \
        'HWIO_SPMI_CGC_CTRL_ADDR', spmi_cgc_val)


g_spmi_cfg = spmi_cfg_obj("")
