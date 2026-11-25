#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file connect.py
#
# @section Description
# This script is used for performing connect operation with the target
import os
import shlex
import time
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from version import ver_info
from utils.telnet_client import TelnetClient

def get_chip_config_path():
    """
    Locate chip_config.xml based on chipset name from g_dbgContext
    Path structure: scripts/lldb_pkg/target/<chipset>/chip_config.xml
    """
    chipset_name = g_dbgContext.chipsetName
    if not chipset_name:
        logger.error("Chipset name not set in g_dbgContext")
        return None
    g_dbgContext.openocdDir = os.environ['OPENOCD_DIR']
    if "\\bin" in g_dbgContext.openocdDir:
        config_path = g_dbgContext.openocdDir.replace("\\bin","\\scripts\\lldb_pkg\\target")
    else:
        # POSIX hosts (Linux/macOS): <package>/bin -> <package>/scripts/lldb_pkg/target
        config_path = os.path.join(os.path.dirname(g_dbgContext.openocdDir.rstrip('/')),
                                   "scripts", "lldb_pkg", "target")
    chip_config_path = os.path.join(config_path, chipset_name.lower(), 'chip_config.xml')

    if not os.path.exists(chip_config_path):
        logger.error(f"chip_config.xml not found at {chip_config_path}")
        return None

    return chip_config_path


def connect(debugger, command, exe_ctx, result):
    
    from utils.telematics import send_telematics_data
    import xml.etree.ElementTree as ET
    
    SUBSYSTEM_TARGET_NAME = {
        "apss": "apss.core0",
        "apss0": "apss.core0",
        "apss3": "apss.core3",
        "cpucp": "cpucp",
        "soccp": "soccp",
        "aoss": "aoss",
        "tme": "tme",
        "adsp": "adsp",
        "cdsp": "cdsp",
        "mpss": "mpss",
        "lmcu": "lmcu",
        "swm": "swm",
        "awm": "awm"
    }
    
    command_args = shlex.split(command)
    
    if len(command_args) < 2:
        logger.error("Invalid command format. Expected: connect_remote -s <subsystem>[.<core>]")
        return
    
    subsystem = command_args[1]
    subsystem, dot, core_no = subsystem.partition('.')
    telematics_data = []
    
    # logger.error(f"user meta var {g_dbgContext.userMeta}")
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
    else:
        chip_info = g_dbgContext.chipInfo
    # Fetch adapter & protocol
    adapter = g_dbgContext.debug_adapter.upper()
    telematics_data.append({"adapter": adapter})
    protocol = g_dbgContext.debug_protocol.upper()
    telematics_data.append({"protocol": protocol})

    # Fetch chipset info
    chipset = g_dbgContext.chipsetName.upper()
    telematics_data.append({"chipset": chipset})
    
    g_dbgContext.getDbgPortNum(subsystem)
    gdb_port = g_dbgContext.port
    
    # Fetch architecture from chip_config.xml
    arch = None
    try:
        for chip in chip_info.findall('chipset'):
            chipname = chip.get('name')
            if chipname.upper() == chipset:
                arch_config = chip.find('arch')
                if arch_config is not None:
                    arch_element = arch_config.find(str(subsystem).lower())
                    if arch_element is not None:
                        arch = str(arch_element.attrib.get('arch'))
                        break
    except (AttributeError, TypeError) as e:
        logger.error(f"Unable to fetch architecture: {e}")
        return
    
    if arch is None:
        logger.error(f"Architecture not found for chipset {chipset}, subsystem {subsystem}")
        return
    
    target_name = SUBSYSTEM_TARGET_NAME.get(subsystem.lower())
    if target_name is None:
        logger.error(f"Unknown subsystem '{subsystem}' for {g_dbgContext.chipsetName.upper()}.")
        logger.error(f"Expected one of: {', '.join(SUBSYSTEM_TARGET_NAME)}")
        return

    cmd = f"{chipset}.{target_name} arp_examine"
    try:
        with TelnetClient() as telnet:
            telnet.send_command(cmd)
        time.sleep(0.5)
    except Exception as e:
        logger.error(f"Failed to arp_examine '{chipset}.{target_name}': {e}")
        logger.error("Please ensure OpenOCD is running and reachable over telnet.")
        return
    
    # Build telematics data
    telematics_data.append({"arch": arch})
    version = f"{ver_info.major_rev_id}.{ver_info.minor_rev_id}.{ver_info.spin_rev_id}"
    telematics_data.append({"version": version})
    
    # Frame the connect command
    cmd = "gdb-remote {}:{}".format(g_dbgContext.getGdbRemoteHost(), gdb_port)
    tmp_dict = {}
    for item in telematics_data:
        tmp_dict.update(item)
    tmp_dict = {f'{key}': f'{value}'.replace("'", "") for key, value in tmp_dict.items()}
    
    debugger.HandleCommand(cmd)
    send_telematics_data(tmp_dict)
