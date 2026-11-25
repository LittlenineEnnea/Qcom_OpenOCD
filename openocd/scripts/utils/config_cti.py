#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file config_cti.py
#
# @section Description
# This script configures Cross Trigger Interface (CTI) registers for the target 
# device

from utils.telnet import *
from utils.hwio import g_hwioConfig
from utils.debug_context import logger


def config_cti( debugger, command, result, tln, cti_base_addr="", cti_input_trigger="", cti_output_trigger="", cti_channel_num="", wait_for_event=""):
    """
    This function is used to configure the CTI registers for the target device
    and also configure channel num to trigger for given input or output.

    @param debugger         -   An instance of the SBDebugger Object that
                                provides access to SBTargets used for
                                debugging.
    @param command          -   String containing all the input arguments
                                for the used command.
    @param result           -   A return object which encapsulates
                                success/failure information for the command
                                and output text that needs to be printed
                                as a result of the command.
    @param tln              -   A telnet handle used for communication with
                                the target device.
    @param cti_base_addr    -   The base address of the CTI block in hex format

    @param cti_input_trigger-   The input trigger number to configure

    @param cti_output_trigger-  The output trigger number to configure

    @param cti_channel_num  -   The channel number to configure for the trigger

    @param wait_for_event   -   A flag to wait for an output event

    @return                 -   None

    """

    # Check for valid CTI block and Channel number
    if (cti_base_addr == "") or (cti_channel_num == ""):
        logger.error("Provide a valid CTI base address and channel number.")
        return

    # Check for valid Input Trigger or Output trigger
    if (cti_input_trigger == "") and (cti_output_trigger == ""):
        logger.error("Provide a valid CTI Trigger Input or Output")
        return

    # Prefix CTI Base address with HWIO format
    cti_base_addr = "HWIO_" + cti_base_addr + "_ADDR"

    reg_addr = g_hwioConfig.hwioRegisterAddr(debugger, command, result, cti_base_addr)
    dapAccessTelnet( debugger, command, result, tln, True, None, None, hex(int(reg_addr, 16) + 0xFB0), 0xC5ACCE55)

    # Global enable
    dapAccessTelnet(debugger, command, result, tln, True, None, None, hex(int(reg_addr, 16)), 0x1)

    # Configure Channel num to trigger for given input or output
    if cti_input_trigger != "":
        dapAccessTelnet( debugger, command, result, tln, True, None, None, int(reg_addr, 16) + 0x20 + (int(cti_input_trigger) * 4), 1 << int(cti_channel_num))

    if cti_output_trigger != "":
        dapAccessTelnet(debugger,command,result,tln,True,None,None,int(reg_addr, 16) + 0xA0 + (int(cti_output_trigger) * 4),1 << int(cti_channel_num))

    if wait_for_event == "Output":
        output = 0
        mask = 1 << int(cti_output_trigger)

        while (output & mask) == 0:
            output = dapAccessTelnet( debugger, command, result, tln, True, int(reg_addr, 16) + 0x134, None, None, None)
            output = int(output, 16)

        dapAccessTelnet( debugger, command, result, tln, True, None, None, int(reg_addr, 16) + 0x10, output)
