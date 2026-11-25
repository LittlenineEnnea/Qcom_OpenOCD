#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file reset.py
#
# @section Description
# This file contains script to serve basic utility fucntions.


from utils.debug_context import logger
from utils.debug_context import g_dbgContext
from utils.connect import get_chip_config_path
import xml.etree.ElementTree as ET

def get_adi_version():
    """!
    @brief Look up the ADI version configured for the active chipset in
    chip_config.xml.

    @return The <adi><version adi_version="..."> attribute for the active
            chipset, or "5" if it can't be resolved.
    """

    chip_name = g_dbgContext.chipsetName.lower()
    if g_dbgContext.userMeta == 0:
        # Get chipset from g_dbgContext and load chip_config.xml
        chip_config_path = get_chip_config_path()
        if not chip_config_path:
            logger.error("Cannot proceed without valid chip_config.xml")
            return
        try:
            tree = ET.parse(chip_config_path)
            chip_info = tree.getroot()
            g_dbgContext.chipInfo = chip_info
        except Exception as e:
            logger.error(f"Failed to parse chip_config.xml: {e}")
            return
    try:
        for chip in g_dbgContext.chipInfo.findall("chipset"):
            if chip.get("name", "").lower() == chip_name:
                adi = chip.find("adi")
                return adi.find("version").attrib["adi_version"]
    except Exception as e:
        logger.warning(f"get_adi_version: defaulting to ADI v5 for {chip_name}: {e}")
    
    return "5"

