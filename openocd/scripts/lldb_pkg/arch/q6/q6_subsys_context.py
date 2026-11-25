#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file q6_subsys_context.py
#
# @section Description
# This file contains script for reading and writing to coprocessor register 
# values.
import telnetlib
from utils.debug_context import g_dbgContext
from utils.debug_context import logger

class Q6Debug:
    """!
    This class provides methods to configure and debug Q6 subsystems.
    """
    def __init__(self):
        """!
        Constructor for the Q6Debug class.
        Initializes the attributes for the Q6Debug object.
        """
        self.chipname = "none"
        self.ss = "none"
        self.tlb_entries = 0
        self.vtlbMainAddress = 0
        self.vtlbDataAddress = 0
        self.hw_threads = 0
        self.wdog_ctl = 0
        self.clk_regs = []
        self.qurtos_init_addr = 0
        self.bitmap_addr = 0
        self.ukernel_file = ""
        self.root_file = ""
        self.vtlb_entries_addr = ""
        self.vtlb_revision_addr = ""
        self.monitor_command = "process plugin packet monitor"
        self.config_complete = False
        self.run_till_main = False
        # Q6 config register variables
        self.hwio_pil =""
        self.cti_reg =""
        self.qdss_apb_pwr_ctl =""
        self.qdss_apb_pwr_up_req =""
        self.tcsr_reg = ""
        self.tcsr_value = ""
        self.nAWMQ6DbgVal = ""
        self.nSWMQ6DbgVal = ""
        self.nLMCUQ6DbgVal = ""
         # User PD files
        self.audio_file = ""
        self.charger_file = ""
        self.sensor_file = ""
        self.bt_file = ""

    def initCfg(self, chipname, ss='Apps', hw_threads=0,  tlb_entries=20, \
        wdog_ctl=hex(0), vtlbMainAddress=0, vtlbDataAddress=0,    \
            core_clk_regs=[],etm_clks=[]):        
        """!
        Initializes the configuration for the Q6Debug object.

        @param chipname         - Name of the chip.
        @param ss               - Subsystem name.
        @param hw_threads       - Number of hardware threads.
        @param tlb_entries      - Number of TLB entries.
        @param wdog_ctl         - Watchdog control value.
        @param vtlbMainAddress  - Main address for VTLB.
        @param vtlbDataAddress  - Data address for VTLB.
        @param core_clk_regs    - List of core clock registers.
        @param etm_clks         - List of ETM clock addresses.
        """
        self.chipname = chipname
        self.ss = ss
        self.tlb_entries = tlb_entries
        self.vtlbMainAddress = vtlbMainAddress
        self.vtlbDataAddress = vtlbDataAddress
        self.hw_threads = hw_threads
        self.wdog_ctl = wdog_ctl
        self.etm_clks = etm_clks
        self.core_clk_regs = core_clk_regs

        self.cdsp_commands = {
            'SetMaxHWThreads': hw_threads,
            'SetNumTLBEntries': tlb_entries,
            'SetWDOGCTL': wdog_ctl,
            'SetVTLBMainAddress': vtlbMainAddress,
            'SetVTLBDataAddress': vtlbDataAddress,
            'SetEtmClkAddress': etm_clks,
        }

        self.adsp_commands = {
            'SetMaxHWThreads': hw_threads,
            'SetNumTLBEntries': tlb_entries,
            'SetWDOGCTL': wdog_ctl,
            'SetVTLBMainAddress': vtlbMainAddress,
            'SetVTLBDataAddress': vtlbDataAddress,
            'SetEtmClkAddress': etm_clks,

        }
        self.mpss_commands = {
            'SetMaxHWThreads': hw_threads,
            'SetNumTLBEntries': tlb_entries,
            'SetWDOGCTL': wdog_ctl,
            'SetVTLBMainAddress': vtlbMainAddress,
            'SetVTLBDataAddress': vtlbDataAddress,
            'SetEtmClkAddress': etm_clks,
        }
        
        self.oempd_commands = {
            'SetWDOGCTL': wdog_ctl,
            'SetEtmClkAddress': etm_clks,
            'EnableClockRegs': core_clk_regs,
        }
        self.initCfg_cmd = 'initConfig'
        self.examineCmd = 'arp_examine'
        self.untrustedCmd = 'untrusted'

    def print(self):
        """!
        Prints the configuration details of the Q6Debug object.
        """
        print("Chip = ", self.chipname)
        print("SS = ", self.ss)
        if (self.ss != 'Apps'):
            print("TLB Entries  = ", self.tlb_entries)
            print("VTLB_MAIN_ADDRESS = ", hex(self.vtlbMainAddress))
            print("VTLB_DATA_ADDRESS = ", hex(self.vtlbDataAddress))

    def send_cdsp_config_commands(self, tln):
        """!
        Sends CDSP configuration commands via Telnet.

        @param tln  - Telnet object for communication.
        """
        etm_clock_regs =' '

        for j in range(len(self.etm_clks)):
            etm_clock_regs = etm_clock_regs + str(self.etm_clks[j]) + ' '        
        
        for key in self.cdsp_commands.keys():
            if (key == 'SetEtmClkAddress'):
                command_formed = str(self.chipname + '.' + self.ss + ' hexagon ' \
                        + key + ' ' + str(etm_clock_regs) + '\n')
            else :        
                command_formed = str(self.chipname + '.' + self.ss + ' hexagon ' \
                + key + ' ' + str(self.cdsp_commands[key]) + '\n')

            print(command_formed)
            tln.write(command_formed.encode('ascii'))
            tln.read_until(b"> ")

        # initialization
        initCmd = str(self.chipname + '.' + self.ss + ' hexagon ' \
             + self.initCfg_cmd + '\n')
        examineCmd = str(self.chipname + '.' + self.ss + ' ' + self.examineCmd + '\n')
 
        self.sendTelnetCmd(tln, initCmd)
        self.sendTelnetCmd(tln, examineCmd)


    def send_adsp_config_commands(self, tln):
        """!
        Sends ADSP configuration commands via Telnet.

        @param tln  - Telnet object for communication.
        """
        clock_regs = ' '
        etm_clock_regs =' '
        
        for i in range(len(self.core_clk_regs)):
                clock_regs = clock_regs + str(self.core_clk_regs[i]) + ' '
                
        for j in range(len(self.etm_clks)):
                etm_clock_regs = etm_clock_regs + str(self.etm_clks[j]) + ' '

        for key in self.adsp_commands.keys():
            # core_clk_regs
            if ((key != 'EnableClockRegs') and (key != 'SetEtmClkAddress')):
                command_formed = str(self.chipname + '.' + self.ss + ' hexagon ' \
                     + key + ' ' + str(self.adsp_commands[key]) + '\n')
            else:
                if (key == 'SetEtmClkAddress'):
                    command_formed = str(self.chipname + '.' + self.ss + ' hexagon ' \
                        + key + ' ' + str(etm_clock_regs) + '\n')


            print(command_formed)

            tln.write(command_formed.encode('ascii'))
            tln.read_until(b"> ")
        # initialization
        initCmd = str(self.chipname + '.' + self.ss + ' hexagon ' \
             + self.initCfg_cmd + '\n')
        examineCmd = str(self.chipname + '.' + self.ss + ' ' + self.examineCmd + '\n')
 
        self.sendTelnetCmd(tln, initCmd)
        self.sendTelnetCmd(tln, examineCmd)

    def send_mpss_config_commands(self, tln):
        """!
        Sends MPSS configuration commands via Telnet.

        @param tln  - Telnet object for communication.
        """
        etm_clock_regs =' '
        

        for key in self.mpss_commands.keys():
            if (key != 'SetEtmClkAddress'):
                command_formed = str(self.chipname + '.' + self.ss + ' hexagon ' + \
                            ' ' + key + ' ' + str(self.mpss_commands[key]) + '\n')
            else:
                for j in range(len(self.etm_clks)):
                    etm_clock_regs = etm_clock_regs + str(self.etm_clks[j]) + ' '
                command_formed = str(self.chipname + '.' + self.ss + ' hexagon ' +
                            ' ' + key + ' ' + str(etm_clock_regs) + '\n')

            print(command_formed)
            tln.write(command_formed.encode('ascii'))
            tln.read_until(b"> ")
        command_formed = str(self.chipname + '.' + self.ss + ' hexagon ' \
                            + self.initCfg_cmd + '\n')
        print(command_formed)
        tln.write(command_formed.encode('ascii'))
        tln.read_until(b"> ")

    def send_mpss_oempd_config_commands(self, tln):
        """!
        Sends MPSS OEMPD configuration commands via Telnet.

        @param tln  - Telnet object for communication.
        """
        command_formed = ''
        for key in self.mpss_commands.keys():
            if (key == 'SetEtmClkAddress'):
                clock_regs = ' '
                for i in range(len(self.clk_regs)):
                    clock_regs = clock_regs + str(self.clk_regs[i]) + ' '
                    
                command_formed = str(self.chipname + '.' + self.ss + ' hexagon ' + \
                                     ' ' + key + ' ' + str(clock_regs) + '\n')
                
                print(command_formed)
                tln.write(command_formed.encode('ascii'))
                tln.read_until(b"> ")
            if (key == 'SetWDOGCTL'):
                command_formed = str(self.chipname + '.' + self.ss + ' hexagon ' + \
                    ' ' + key + ' ' + str(self.mpss_commands[key]) + '\n')
                        
                print(command_formed)
                tln.write(command_formed.encode('ascii'))
                tln.read_until(b"> ")

    def send_oempd_config_commands(self,tln):
        """!
        Sends OEMPD configuration commands via Telnet.

        @param tln  - Telnet object for communication.
        """
        subsystem_str = ' hexagon '
        command_formed = ''
        etm_clock_regs = ' '
        clock_regs = ''
        for i in range(len(self.core_clk_regs)):
            clock_regs = clock_regs + str(self.core_clk_regs[i]) + ' '
        for i in range(len(self.etm_clks)):
            etm_clock_regs = etm_clock_regs + str(self.etm_clks[i]) + ' '

        for key in self.oempd_commands.keys():
            print("key :", key)
            print("ss :", self.ss)
            if (key == 'SetEtmClkAddress'):
                command_formed = str(self.chipname + '.' + self.ss + \
                    str(subsystem_str) +' ' + key + ' ' + str(etm_clock_regs) + '\n')
                self.sendTelnetCmd(tln, command_formed)

            if (key == 'SetWDOGCTL'):
                command_formed = str(self.chipname + '.' + self.ss \
                    + str(subsystem_str) + ' ' + key + ' ' +   \
                        str(self.oempd_commands[key]) + '\n')
                self.sendTelnetCmd(tln, command_formed)

        examineCmd = str(self.chipname +'.' + self.ss +  ' ' +     \
                    str(self.examineCmd) + '\n')
        self.sendTelnetCmd(tln, examineCmd)

        untrustedCmd = str(self.chipname + '.' + self.ss + \
                    str(subsystem_str) + ' ' + str(self.untrustedCmd) + '\n')
        self.sendTelnetCmd(tln, untrustedCmd)


    def sendTelnetCmd(self, tln, cmd):
        """!
        Sends a command via Telnet.

        @param tln  - Telnet object for communication.
        @param cmd  - Command to be sent.
        """
        print(cmd)
        tln.write(cmd.encode('ascii'))
        tln.read_until(b"> ")

    def getAddress(self, symbol_name):
        """
        returns address of a given symbol if present in elf
        @param symbol_name  - Symbol name string input for finding in elf
        @return             - Address of the symbol or None if not found
        """
        try:
            symbol = g_dbgContext.tgt.FindSymbols(symbol_name)
            if not symbol or len(symbol) == 0:
                logger.error(f"Symbol {symbol_name} not found!")
                return None
            address = symbol[0].symbol.GetValue()
            return address
        except Exception as e:
            logger.error(f"Error finding symbol {symbol_name}: {str(e)}")
            return None


# Create object
q6Cfg = Q6Debug()
