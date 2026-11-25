#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file pmic_dump.py
#
# @section Description
# This script contains functions to perform the dumping of PMIC registers.

import os
import sys
import shlex
import importlib
from enum import Enum
from xml.dom import minidom
import xml.etree.ElementTree as ET

from utils.debug_context import logger
from utils.debug_context import g_dbgContext

NO_OF_BUS = 2
NO_OF_PMICS = 14
REV_ID = 261  # 0x105


class PMICS(Enum):
    PMIC_IS_UNKNOWN = 0x0  # UNKNOWN
    PMIC_IS_PM8941 = 0x1  # PM8941
    PMIC_IS_PM8841 = 0x2  # PM8841
    PMIC_IS_PM8019 = 0x3  # PM8019
    PMIC_IS_PM8026 = 0x4  # PM8026
    PMIC_IS_PM8110 = 0x5  # PM8110
    PMIC_IS_PMA8084 = 0x6  # PMA8084
    PMIC_IS_PMI8962 = 0x7  # PMI8962
    PMIC_IS_PMD9635 = 0x8  # PMD9635
    PMIC_IS_PM8994 = 0x9  # PM8994
    PMIC_IS_PMI8994 = 0xA  # PMI8994
    PMIC_IS_PM8916 = 0xB  # PM8916
    PMIC_IS_PM8004 = 0xC  # PM8004
    PMIC_IS_PMF2432 = PMIC_IS_PMA8084  # PMF2432
    PMIC_IS_PM2433 = 0xE  # PM2433
    PMIC_IS_PM8950 = 0x10  # PM8950
    PMIC_IS_PMK8001 = 0x12  # PMK8001
    PMIC_IS_PMI8996 = 0x13  # PMI8996
    PMIC_IS_PM8998 = 0x14  # PM8998
    PMIC_IS_PMI8998 = 0x15  # PMI8998
    PMIC_IS_PM8005 = 0x18  # PM8005
    PMIC_IS_PM660L = 0x1A  # PM660L
    PMIC_IS_PM660 = 0x1B  # PM660
    PMIC_IS_SMB1355 = 0x1C  # SMB1355
    PMIC_IS_PM855 = 0x1E  # PM855
    PMIC_IS_PM8150 = PMIC_IS_PM855  # PM8150
    PMIC_IS_PM8250 = PMIC_IS_PM855  # PM8250
    PMIC_IS_PM855L = 0x1F  # PM855L
    PMIC_IS_PM8150L = PMIC_IS_PM855L  # PM8150L
    PMIC_IS_PM855B = 0x20  # PM855B
    PMIC_IS_PM8150B = PMIC_IS_PM855B  # PM8150B
    PMIC_IS_PMK8002 = 0x21  # PMK8002
    PMIC_IS_PMX24 = 0x22  # PMX24
    PMIC_IS_SMB1390 = 0x23  # SMB1390
    PMIC_IS_PM8009 = 0x24  # PM8009
    PMIC_IS_PMI632 = 0x25  # PMI632
    PMIC_IS_PM855P = 0x26  # PM855P
    PMIC_IS_PM855A = 0x27  # PM855A
    PMIC_IS_PM8150A = PMIC_IS_PM855A  # PM8150A
    PMIC_IS_SMB2351 = 0x29  # SMB2351
    PMIC_IS_PMX55 = 0x2A  # PMX55
    PMIC_IS_PMR8255 = 0x2B  # PMR8255
    PMIC_IS_PM8008 = 0x2C  # PM8008
    PMIC_IS_PM6125 = 0x2D  # PM6125
    PMIC_IS_PM7250B = 0x2E  # PM7250B
    PMIC_IS_PMK8350 = 0x2F  # PMK8350
    PMIC_IS_PM8350 = 0x30  # PM8350
    PMIC_IS_PM8350C = 0x31  # PM8350C
    PMIC_IS_PM8350B = 0x32  # PM8350B
    PMIC_IS_PMR735A = 0x33  # PMR735A
    PMIC_IS_PMR735B = 0x34  # PMR735B
    PMIC_IS_SMB1398 = 0x35  # SMB1398
    PMIC_IS_PM6350 = 0x36  # PM6350
    PMIC_IS_PM2XX = 0x37  # PM2XX
    PMIC_IS_PMX60 = 0x38  # PMX60
    PMIC_IS_SMB1394 = 0x39  # SMB1394
    PMIC_IS_PM8450 = 0x3A  # PM8450
    PMIC_IS_PMX65 = 0x3B  # PMX65
    PMIC_IS_PM7325B = 0x3C  # PM7325B
    PMIC_IS_PM1010H = 0x3E  # PM1010H
    PMIC_IS_PM7325 = 0x3F  # PM7325
    PMIC_IS_PM8010 = 0x41  # PM8010
    PMIC_IS_PMG1110 = 0x43  # PMG1110
    PMIC_IS_VOODOO6 = 0x45  # VOODOO6
    PMIC_IS_VOODOO8 = 0x46  # VOODOO8
    PMIC_IS_KOHALA = 0x47  # KOHALA
    PMIC_IS_NALOJR = 0x48  # NALOJR
    PMIC_IS_HUMU = 0x49  # HUMU
    PMIC_IS_AZURA = 0x4A  # AZURA
    PMIC_IS_SCULPIN = 0x4C  # SCULPIN
    PMIC_IS_BOMPOKA = 0x4D  # BOMPOKA
    PMIC_IS_KOBRA_MM = 0x4E  # KOBRA
    PMIC_IS_KOBRA_PSAIL = 0x4F  # KOBRA PSAIL
    PMIC_IS_BONEFISH = 0x50  # BONEFISH
    PMIC_IS_MORRO = 0x51  # MORRO
    PMIC_IS_COSWORTH = 0x52  # COSWORTHC8
    PMIC_IS_DONINGTON = 0x53  # DONINGTON
    PMIC_IS_ETNA = 0x55  # ETNA
    PMIC_IS_LUMINOUS = 0x57  # LUMINOUS
    PMIC_IS_COTTID_L = 0x59  # COTTID_L
    PMIC_IS_COTTID_ND = 0x5A  # COTTID_ND
    PMIC_IS_COTTID_A = 0x5B  # COTTID_A
    PMIC_IS_INVALID = 0x7FFFFFFF  # INVALID


def import_pmic_config_modules(chip_name):
    """
    This function imports the PMIC configuration modules dynamically
    based on the chip name.

    @param chip_name        -   The name of the chip for which the modules
                                need to be imported.This should be a string
                                representing the chip name
                                (e.g., 'bonito', 'milos').

    @return                 -   Returns the pmic_io_configuration
                                functions from the imported modules.
    """
    # Construct the module names
    pmic_config_module_name = f"lldb_pkg.tech.pmic.{chip_name}.pmic_io_config"

    # Import the modules dynamically
    pmic_config_module = importlib.import_module(pmic_config_module_name)

    # Extract the required functions
    g_pmic_config = getattr(pmic_config_module, "g_pmic_config")

    return g_pmic_config


if g_dbgContext.chipsetName.lower() != None:
    chip_name = g_dbgContext.chipsetName.lower()
    g_pmic_config = import_pmic_config_modules(chip_name)


def pmic_version(
    debugger, command, exe_ctx, result, bus_id=0, slave_id=0, address=0, data=0
):
    """
    This function is used to get pmic version.

    @param debugger         - An instance of the SBDebugger Object that
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command.
    @param exe_ctx          - exe_ctx: The execution context, providing access
                              to the current target, process, thread, and frame.
    @param result           - A return object which encapsulates
                              success/failure information for the command
                              and output text that needs to be printed
                              as a result of the command.
    @bus_id                 - Bus ID
    @slave_id               - Slave ID
    @address                - register address
    @data                   - value to be written
    """
    spmi_owner_channel = g_pmic_config.spmi_owner_channel
    g_pmic_config.pmic_io(debugger, command, exe_ctx, result, "read", "DAP", 
                          spmi_owner_channel, bus_id, slave_id, address, data)


def pmic_dump(debugger, command, exe_ctx, result):
    """
    Performs a PMIC Dump operation.

    @param debugger         - An instance of the SBDebugger Object that
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command.
    @param exe_ctx          - exe_ctx: The execution context, providing access
                              to the current target, process, thread, and frame.
    @param result           - A return object which encapsulates
                              success/failure information for the command
                              and output text that needs to be printed
                              as a result of the command.
    """
    args = shlex.split(command)

    if len(args) != 2:
        logger.error("Expected 2 arguments, but got {}".format(len(args)))
        logger.info("enter \"help pmic_dump\" for command format")
        sys.exit(1)

    file_name = ""
    if args[0] != "all":
        # bus_id parsing
        if args[0].find("0x") != -1:
            bus_id = int(args[0], 16)
        else:
            bus_id = int(args[0])

        # slave_id parsing
        if args[1].find("0x") != -1:
            slave_id = int(args[1], 16)
        else:
            slave_id = int(args[1])

        if bus_id >= NO_OF_BUS:
            logger.info(" Please enter valide bus ID")
            return
        if slave_id >= NO_OF_PMICS:
            logger.info(" Please enter valide Slave ID")
            return

        # readStatus,readData = pmicVersion(debugger, command, exe_ctx, result, bus_id, slave_id,REV_ID,0)
        pmic_version(debugger, command, exe_ctx, result, bus_id, slave_id, REV_ID, 0)
        read_data = g_pmic_config.read_data
        pmic_id = int(read_data, 16)

        if (PMICS(pmic_id).value) != -1:

            file_name = PMICS(pmic_id).name
            store_dump = file_name + ".xml"
            file_name = file_name + ".txt"
            script_directory = os.path.dirname(os.path.abspath(sys.argv[0]))
            file_name = (
                script_directory
                + "\\scripts\\lldb_pkg\\tech\\pmic\\pmic_registers\\"
                + file_name
            )

            # Create the root element
            main_root = ET.Element("pmicDump")
            root = ET.SubElement(main_root, "target")
            sub_root = ET.SubElement(
                root, "pmic", attrib={"Model": g_pmic_config.read_data}
            )
            ET.SubElement(sub_root, "bus", attrib={"Type": "spmi", "slaveid0": "1"})

            file = open(file_name, "r")
            logger.info("Collecting PMIC dump.........")

            for line in file:
                address = int(line, 16)
                spmi_owner_channel = g_pmic_config.spmi_owner_channel
                g_pmic_config.pmic_io(debugger, command, exe_ctx, result, "read", "DAP",
                                      spmi_owner_channel, bus_id, slave_id, address, 0)
                line = line.strip()

                ET.SubElement(
                    sub_root,
                    "register",
                    attrib={"address": line, "value": g_pmic_config.read_data},
                )

            store_data = open(
                f"c:\\temp\\pmic_register_dump_bus_{bus_id}_{store_dump}", "w"
            )
            store_data.write(
                minidom.parseString(ET.tostring(main_root)).toprettyxml(indent="   ")
            )
            store_data.close()
            file.close()
            print("PMIC Dump Location : c:\\temp")
