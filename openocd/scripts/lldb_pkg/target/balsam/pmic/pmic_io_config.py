#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file pmic_io_config.py
#
# @section Description
# This script contains the PMIC IO configuration class and its initialization
# specific to Balsam target.

import time
import os

from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.dap_access import dapAccessGDB
from lldb_pkg.target.balsam.pmic.spmi.spmi_cfg import spmi_config
from utils.telnet import *


class pmic_io_config:
    """
    Class for PMIC IO configuration.

    Attributes:
        io_debug_flag (bool): Flag to enable or disable IO debugging.
    """

    def __init__(self):
        """
        Initializes the PMIC IO configuration with default settings.
        """
        self.read_status = 0
        self.read_data = 0
        self.io_debug_flag = False

        self.aoss_base_addr = 0xB000000
        self.pmic_arb_base_addr = self.aoss_base_addr + 0x1400000
        self.max_spmi_channels = 896
        self.n_channel_offset = 0x1000
        self.n_obsq_channel_offset = 0x20
        self.obsq_channel_offset = 0x8000
        self.max_register_addr = 0xFFFF

        self.aop_view_base_addr = 0x60000000
        self.qdss_soc_debug_base_addr = 0x6000000
        self.qdss_pmic_arb_base_addr = self.qdss_soc_debug_base_addr + 0xb22000

        self.g_break_flag = 0
        self.protocol_irq_status = 0
        self.spmi_not_initialized = 0xFF
        self.pm_vrm_hwio_ver_addr = 0x0B9F5000
        self.pm_vrm_hwio_ver = 0
        self.pmic_arb_features_addr = 0xC400004
        self.spmi_owner_channel = 3

        self.pmic_arb_features = 0
        self.pmic_arb_ver_addr = 0xC400000
        self.pmic_arb_ver = 0
        self.main_status = True

        self.pmic_arb_debug_spmi_channel_cmd_0 = self.qdss_pmic_arb_base_addr+0x0
        self.pmic_arb_debug_spmi_channel_cmd_1 = self.qdss_pmic_arb_base_addr+0x4
        self.pmic_arb_debug_spmi_channel_cmd_2 = self.qdss_pmic_arb_base_addr+0x8
        self.pmic_arb_debug_spmi_channel_cmd_3 = self.qdss_pmic_arb_base_addr+0xc
        self.pmic_arb_debug_spmi_channel_config = self.qdss_pmic_arb_base_addr+0x10
        self.pmic_arb_debug_spmi_channel_status = self.qdss_pmic_arb_base_addr+0x14
        self.pmic_arb_debug_spmi_channel_wdata_0 = self.qdss_pmic_arb_base_addr+0x18
        self.pmic_arb_debug_spmi_channel_rdata_0 = self.qdss_pmic_arb_base_addr+0x38

        self.pmic_arb_channel_n_cmd = self.pmic_arb_base_addr+0x100000
        self.pmic_arb_channel_n_status = self.pmic_arb_base_addr+0x100008
        self.pmic_arb_channel_n_wdata_0 = self.pmic_arb_base_addr+0x100010
        self.pmic_arb_channel_n_rdata_0 = self.pmic_arb_base_addr+0x100018
        self.pmic_arb_obsq_channel_cmd = self.pmic_arb_base_addr+0x40000
        self.pmic_arb_obsq_channel_n_status = self.pmic_arb_base_addr+0x40008
        self.pmic_arb_obsq_channel_n_rdata = self.pmic_arb_base_addr+0x40018
        self.pmic_arb_reg_addr_n = self.pmic_arb_base_addr+0x2000
        self.pmic_arb_periph_n_owner_addr = self.pmic_arb_base_addr+0x2D000



    def pmic_io(self, debugger, command, exe_ctx, result, rw ="", access ="", owner = 0 ,\
         busId = 0, slave_id = 0, reg = 0 , data = 0, xfer_size = "" , dbg = ""):  
        """
        Performs PMIC IO operations based on the provided parameters.

        @param self             - Reference to the current instance of the class.
        @param debugger         - An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
        @param command          - String containing all the input arguments
                                for the used command. 
        @param result           - A return object which encapsulates 
                                success/failure information for the command
                                and output text that needs to be printed 
                                as a result of the command.
        @param rw               - Read/Write operation indicator.
        @param access           - Access mode (e.g., DAP).
        @param owner            - Owner channel.
        @param busId            - Bus ID.
        @param slave_id         - Slave ID.
        @param reg              - Register address.
        @param data             - Data to be written/read.
        @param xfer_size        - Transfer size.
        @param dbg              - Debug flag.
        """
        g_break_flag = 0

        if (dbg !=""):
            self.io_debug_flag = dbg
        if (xfer_size== ""):
            xfer_size=1
        if (busId == ""):
            busId = 0

        if (busId == 1):
            self.pmic_arb_features_addr=0xC400008

        if (rw!=""):
            rw = rw.lower()
        if ((owner!="")and(rw!="config")):
            self.spmi_owner_channel=owner

        if (access!=""):
            bus_access_mode="access"


        if (reg!=""):
            if (reg > self.max_register_addr) or (reg<0):
                logger.error("Invalid register address")
                return

        # Get PMIC Arbiter base address and last spmi channel
        # ; Get PMIC Arbiter base address and last spmi channel
        if (bus_access_mode=="A"):
            g_break_flag=1

        if (dbg==""):
            self.pmic_arb_ver = dapAccessGDB(debugger,command, result, "Yes", \
                self.pmic_arb_ver_addr, None, None, None)

            if (self.pmic_arb_ver == 0x50000000):
                self.pm_vrm_hwio_ver = dapAccessGDB(debugger,command, result, "Yes", \
                    self.pm_vrm_hwio_ver_addr, None, None, None)
                #  this check is needed only for napali versions v1.0 to default to debug channel
                if (self.pm_vrm_hwio_ver == 0x10000):
                    self.io_debug_flag = True

        if not self.io_debug_flag:
            self.pmic_arb_features = dapAccessGDB(debugger,command, result, "Yes", \
                self.pmic_arb_features_addr, None, None, None)

            self.pmic_arb_features = int (self.pmic_arb_features,16)
            self.max_spmi_channels = self.pmic_arb_features&0x7FF

            if (busId == 1):
                # last channel for second spmi bus
                self.max_spmi_channels = self.max_spmi_channels+0x380
            
            spmi_protocol_irq_status = self.pmic_arb_base_addr+0x2F000

            self.protocol_irq_status = dapAccessGDB(debugger,command, result, "Yes", \
                spmi_protocol_irq_status, None, None, None)

            if (int(self.protocol_irq_status, 16) & 0x400) != 0x400:
                if (rw=="read"):
                    if (g_break_flag==1):
                        debugger.HandleCommand("c")
                        spmi_not_initialized = 0
                elif (rw=="write"):
                    if (g_break_flag==1):
                        debugger.HandleCommand("c")

        if (g_break_flag ==1):
            debugger.HandleCommand("c")

        if (rw=="read"):
            if not self.io_debug_flag:
                read_status,read_data = self.spmi_read(debugger, command, result, slave_id, reg , xfer_size)
                self.read_status = read_status
                self.read_data = read_data
                return self.read_status, self.read_data
        elif (rw=="write"):
            if self.io_debug_flag:
                self. spmi_debug_write(debugger, command, result, slave_id, reg, data)
            else :
                try :
                    write_status = self.spmiWrite(debugger, command, result, slave_id,\
                         reg, data, xfer_size)
                    if (int(write_status,16) == 1):
                        logger.info("SPMI rw: Slave=0x{} Address=0x{} Value=0x {}".\
                            format(slave_id, hex(reg) , hex(data)))
                    else:
                        logger.info("SPMI error: Slave=0x{} Address=0x{} Value=0x {}".\
                            format(slave_id, hex(reg), hex(data)))
                    return
                except:
                    # ignoring exception due to disconnection occured during pmic hard reset
                    pass
                logger.info("HARD RESET COMPLETE")

        elif (rw=="config"):
            current_dir = os.getcwd()
            logger.info("PATH {current_dir}")
            spmi_config_script = str(current_dir).replace("\\","/")+"/spmi/spmiCfg.cmm"
            logger.info("UPDATED PATH {spmi_config_script}")
            spmi_config_target = owner

            if os.path.isfile(spmi_config_script):
                if (spmi_config_target!=""):
                    spmi_config_status = spmi_config(debugger, command, exe_ctx, result)
                    if (spmi_config_status!="SUCCESS"):
                        logger.error("HARD RESET FAILED")
                        pass
                    else :
                        spmi_config_status=1
            
                else:
                    logger.info ("Enter valid target (e.g 845 and so on ) for SPMI initialization!")
                    pass
            else:
                logger.info ("spmi_config_script not found!")
                pass
        else:
            main_status=False
            return

        
        return

    def spmi_read(self, debugger, command, result, slave_id, register_address, xfer_size):
        """
        Reads data from a specified SPMI register.

        @param self             - Reference to the current instance of the class.
        @param debugger         - An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
        @param command          - String containing all the input arguments
                                for the used command. 
        @param result           - A return object which encapsulates 
                                success/failure information for the command
                                and output text that needs to be printed 
                                as a result of the command.
        @param slave_id         - Slave ID from which data is to be read.
        @param register_address - Address of the register to read from.
        @param xfer_size        - Size of the data to be read.
        """
        register_base=(int(register_address)&0xFF00)|int((slave_id)<<0x10)
        register_offset = int (register_address)&0xFF
        register_owner = self.spmi_owner_channel
        spmi_channel = self.max_spmi_channels - 1
        max_count = 10
        count_index = 0
        break_flag = 0

        dapAccessGDB(debugger,command, result, "Yes", None, None, (self.pmic_arb_reg_addr_n)+\
            (4*(spmi_channel)), register_base)
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_periph_n_owner_addr)+(4*(spmi_channel)), register_owner)
        if (xfer_size== 0x4):
            xfer_len = 3
        else :
            xfer_len=0

        spmi_cmd = (0x1<<0x1B)|(register_offset<<0x4)|(xfer_len)

        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_obsq_channel_cmd+(self.n_obsq_channel_offset*spmi_channel)\
                +(self.obsq_channel_offset*register_owner)), spmi_cmd)

        read_status = 0
        read_data=0

        while ((read_status==0) and (count_index<max_count)):
            read_status = dapAccessGDB(debugger,command, result, "Yes", \
                (self.pmic_arb_obsq_channel_n_status+self.n_obsq_channel_offset*\
                spmi_channel+self.obsq_channel_offset*register_owner), None, None, None)
            count_index = count_index+1
        read_data = dapAccessGDB(debugger,command, result, "Yes", \
            (self.pmic_arb_obsq_channel_n_rdata+self.n_obsq_channel_offset*\
                spmi_channel+self.obsq_channel_offset*register_owner), None, None, None)

        return read_status, read_data


    def spmiWrite(self, debugger, command, result, slave_id, register_address,\
         write_data, xfer_size):
        """
        Writes data to a specified SPMI register.

        @param self             - Reference to the current instance of the class.
        @param debugger         - An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
        @param command          - String containing all the input arguments
                                for the used command. 
        @param result           - A return object which encapsulates 
                                success/failure information for the command
                                and output text that needs to be printed 
                                as a result of the command.
        @param slave_id         - Slave ID to which data is to be written.
        @param register_address - Address of the register to write to.
        @param write_data       - Data to be written to the register.
        @param xfer_size        - Size of the data to be written.
        """
        tln = g_dbgContext.telnetHandle
        register_offset = (register_address) & 0xFF
        register_base = ((register_address) & 0xFF00) | (slave_id << 0x10)
        register_owner = self.spmi_owner_channel
        spmi_channel = self.max_spmi_channels - 1
        spmi_cmd = 0
        break_flag = 0
        count_index = 0
        max_count = 10
        xfer_len = 0

        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_reg_addr_n+(4*spmi_channel)), register_base)
        time.sleep(0.1)    

        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_periph_n_owner_addr)+(4*(spmi_channel)), register_owner)
        time.sleep(0.1)        

        if (xfer_size== 0x4):
            xfer_len = 3
        else :
            write_data=write_data & 0xFF
            xfer_len=0
     
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_channel_n_wdata_0+self.n_channel_offset * spmi_channel),\
                 write_data)
        time.sleep(0.1)

        spmi_cmd = (0x0<<0x1B)|(register_offset<<0x4)|(xfer_len)

        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_channel_n_cmd+self.n_channel_offset*spmi_channel),\
                 spmi_cmd)
        time.sleep(0.1)  

        if (register_address==0x864) :
            return

        write_status = '0x0'
        while (int(write_status,16)!=1) and (count_index<max_count):

            time.sleep(0.1)
            write_status = dapAccessGDB(debugger,command, result, "Yes", \
                (int(self.pmic_arb_channel_n_status+self.n_channel_offset*spmi_channel)),\
                     None, None, None)

            count_index = count_index+1

        if (break_flag == 1):
            debugger.HandleCommand("c")
        return write_status

    def spmiDebugRead(self, debugger, command, result, slave_id, register_address):
        """
        Reads data from a specified SPMI register for debugging purposes.

        @param self             - Reference to the current instance of the class.
        @param debugger         - An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
        @param command          - String containing all the input arguments
                                for the used command. 
        @param result           - A return object which encapsulates 
                                success/failure information for the command
                                and output text that needs to be printed 
                                as a result of the command.
        @param slave_id         - Slave ID from which data is to be read.
        @param register_address - Address of the register to read from.
        """
        register_offset = (register_address) & 0xFF
        register_ppid = (register_address >> 0x8) & 0xFF
        spmi_cmd = 0
        break_flag = 0
        count_index = 0
        max_count = 10


        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_debug_spmi_channel_cmd_1), slave_id)
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_debug_spmi_channel_cmd_2), register_ppid)
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_debug_spmi_channel_cmd_3), register_offset)
        spmi_cmd=0x2
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_debug_spmi_channel_cmd_0), spmi_cmd)

        while ((read_status == 0 ) and (count_index < max_count)):
            read_status = dapAccessGDB(debugger,command, result, "Yes", \
                (self.pmic_arb_debug_spmi_channel_status), None, None, None)
            count_index=count_index+1

        read_data = dapAccessGDB(debugger,command, result, "Yes", \
            (self.pmic_arb_debug_spmi_channel_rdata_0), None, None, None)

        if (break_flag==1):
            debugger.HandleCommand("c")

        return read_status, read_data

    def spmi_debug_write(self, debugger, command, result, slave_id, \
        register_address, write_data):
        """
        Writes data to a specified SPMI register for debugging purposes.

        @param self             - Reference to the current instance of the class.
        @param debugger         - An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
        @param command          - String containing all the input arguments
                                for the used command. 
        @param result           - A return object which encapsulates 
                                success/failure information for the command
                                and output text that needs to be printed 
                                as a result of the command.
        @param slave_id         - Slave ID to which data is to be written.
        @param register_address - Address of the register to write to.
        @param write_data       - Data to be written to the register.
        """
        register_offset = (register_address) & 0xFF
        register_ppid = (register_address >> 0x8) & 0xFF
        spmi_cmd = 0
        break_flag = 0
        count_index = 0
        max_count = 10

        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_debug_spmi_channel_cmd_1), slave_id)
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_debug_spmi_channel_cmd_2), register_ppid)
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_debug_spmi_channel_cmd_3), register_offset)
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_debug_spmi_channel_wdata_0), write_data&0xFF)

        spmi_cmd=0x0
        dapAccessGDB(debugger,command, result, "Yes", None, None, \
            (self.pmic_arb_debug_spmi_channel_cmd_0), spmi_cmd)

        if (register_address==0x864):
            return

        write_status=0
        while ((write_status==0) and (count_index<max_count)):
            write_status = dapAccessGDB(debugger,command, result, "Yes", \
                (self.pmic_arb_debug_spmi_channel_status), None, None, None)
            count_index=count_index+1

        read_data = dapAccessGDB(debugger,command, result, "Yes", \
            (self.pmic_arb_debug_spmi_channel_rdata_0), None, None, None)

        if (break_flag==1):
            debugger.HandleCommand("c")
        
        return write_status

g_pmic_config = pmic_io_config()