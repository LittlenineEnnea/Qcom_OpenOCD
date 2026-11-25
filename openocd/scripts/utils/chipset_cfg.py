#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file chipset_cfg.py
#
# @section Description
# This file contains class definition of an object that is used to get the 
# chipset information from chip_config.xml file.

from re import I
import xml.etree.ElementTree as ET

class SubsystemConfig:
    """!
    This class is used to get the subsystem information from chip_config.xml      

    """
    def __init__ (self):
        """!

        The SubsystemConfig object base class initializer.
        @return  An instance of the SubsystemConfig class.

        """
        self.config = {}
        self.registers = {}

class ChipsetConfig:
    """!
    This class is used to get the chipset information from chip_config.xml      

    """
    def __init__ (self, chipsetName, configXml = None):

        """!

        The ChipsetConfig object base class initializer.

        @param chipsetName          -   The name of the chipset
        @param configXml            -   The file path where chip_config.xml
                                        and reg_list.xml can be found

        @return  An instance of the ChipsetConfig class.

        """
        self.xmlPath = configXml 

        xmlRoot = ET.parse(configXml, parser = ET.XMLParser(encoding = 'iso-8859-5'))
        xmlTree = xmlRoot.getroot()
        chipsets = xmlTree.findall ('chipset')
        for chipset in chipsets:
            name = chipset.get('name')
            if name != chipsetName:
                continue

    def __configSS (self, xmlNode):
        """!

        The ChipsetConfig object base class initializer.

        @param xmlNode              -   xml tree node is given as input for
                                        chip_config.xml and reg_list.xml 

        @return  An instance of the ssConfig class.

        """
        
        ssConfig = SubsystemConfig()
        for elem in xmlNode:
            if elem.tag == 'config':
                ssConfig.config [elem.get('name')] = elem.get ('value')
            elif elem.tag == 'register':
                ssConfig.registers [elem.get('name')] = elem.get ('address')
        return ssConfig

