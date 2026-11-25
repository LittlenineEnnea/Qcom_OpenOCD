#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file hardReset.py
#
# @section Description
# This script contains functions to perform standard pmic hard reset.

import time
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.telnet import *

def pmicHardreset(debugger, command, exe_ctx, result, inputarg = None):       
    try :
        tln = getTelnetHandle()
    except :
        logger.error (" Unable to establish telnet connection with openocd."
        "OpenOCD server should have been started prior to issuing this command. " )
        
    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname = chip.get('name')
        if (chipname.upper() == g_dbgContext.chipsetName.upper()):
            logger.info (" --- SPMI Configuration Initiated ...")
            spmi_config = chip.find('spmi_config')
            for spmi in spmi_config.iter('spmi_write'):
                reg = int(spmi.attrib['reg'],16)
                value = int(spmi.attrib['value'],16)
                try :
                    dapAccessTelnet(debugger,command, result, tln, True, None, None, hex(reg), hex(value))
                except :
                    logger.error (" Lost telnet connection")
                    break

            logger.info (" --- SPMI Configuration Done")
            reset_config = chip.find('reset_config')
            logger.info (" --- PMIC Hard Reset Initiated ...")
            for reset in reset_config.iter('reset_write'):
                attribs = len(reset.attrib)
                reg = int(reset.attrib['reg'],16)
                value = int(reset.attrib['value'],16)
                try:
                    dapAccessTelnet(debugger,command, result, tln, True, None, None, hex(reg), hex(value))
                except :
                    logger.error (" Lost telnet connection")
                    break
                if attribs == 3:
                    delay = float(reset.attrib['delay'])
                    time.sleep(delay)  
    try:
        tln.write(str("targets {}.apss.core0\n ".format( \
            g_dbgContext.chipsetName.upper())). encode('ascii'))
        tln.read_until(b"> ")
        tln.write("resume \n". encode('ascii'))
        tln.read_until(b"> ")
        tln.write("resume \n". encode('ascii'))
        tln.read_until(b"> ")
        tln.close()
    except :
        logger.error ("Lost telnet connection")
    logger.info ("Device has been reset")