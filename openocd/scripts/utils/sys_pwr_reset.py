#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file sys_pwr_reset.py
#
# @section Description
# This file contains a script for performing target power off/on.

import time
import sys
import TACDev

from utils.debug_context import logger
 
def SysPowerReset():
    """!   
    This function is used to define a command to perform target power off/on.
    Also enables the EUD, if EUD state is False before doing system power reset.
    """

    alpacaVersion = TACDev.AlpacaVersion()
    logger.info("Alpaca Version: {}".format(alpacaVersion))

    tacVersion = TACDev.TACVersion()
    logger.info("TAC Version: {}".format(tacVersion))

    deviceCount = TACDev.GetDeviceCount()
    if (deviceCount > 0):
        tacDevice = TACDev.GetDevice(0)
        
        if tacDevice is not None:
            try :
                if tacDevice.Open():
                    logger.info("Power cycling the TAC device with port name: {} and serial number: {}".format(tacDevice.PortName(), tacDevice.SerialNumber()))
            except :
                logger.info("Close alpaca or axiom and then try")
                sys.exit(1)

            EUDState = tacDevice.GetEUDState()
            # logger.info("EUD state of device : {}", EUDState)
            if EUDState is False:
                try :
                    tacDevice.Eud(1)
                except :
                    logger.info("Updating the EUD pin state failed")
                    
            # logger.info("EUD state of device : {}", tacDevice.GetEUDState()

            logger.info("Power Off Button Triggered: {}".format(tacDevice.PowerOffButton()))
            time.sleep(2)
            logger.info("Power On Button Triggered: {}".format(tacDevice.PowerOnButton()))
            time.sleep(10)
            tacDevice.Close()

        else:
            logger.info("Device {} not found.".format(tacDevice.PortName()))
    else:
        logger.info("TAC device not connected")