#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file debugwithpil.py
#
# @section Description
# This script provides functions for configuring PIL and subsystems.

import lldb
import re
import time
from utils.dap_access import dapAccessGDB
from utils.config_cti import config_cti
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.hwio import g_hwioConfig
from utils.telnet import *
from lldb_pkg.arch.q6.q6_subsys_context import q6Cfg


def pil_debugger(debugger, command, result, subsystem, poll_for_subsys="False"):
    """
    This function configures debug cookies and checks the status of subsystems, 
    including whether they are out of reset or not.

    @param debugger         -   An instance of the SBDebugger Object that
                                provides access to SBTargets used for
                                debugging.
    @param command          -   String containing all the input arguments
                                for the used command.
    @param result           -   A return object which encapsulates
                                success/failure information for the command
                                and output text that needs to be printed
                                as a result of the command.
    @param arg_line         -   The argument line passed to the function.

    @return                 -   None
    """

    NS = ""
    wait_for_pil = False

    command_interpreter = debugger.GetCommandInterpreter()
    return_obj = lldb.SBCommandReturnObject()

    Chipset_name = g_dbgContext.chipsetName.upper()

    logger.info(f"Chipset_name : {Chipset_name}")
    for chip in g_dbgContext.chipInfo.findall('chipset'):
        if chip.get('name') and chip.get('name').upper() == Chipset_name:
            q6_config = chip.find(f'q6_config')

            try:
                if q6_config is not None:
                    hwio = q6_config.find('hwio_pil')
                    if hwio is not None and hwio.get('hwio_pil'):
                        q6Cfg.hwio_pil = hwio.get('hwio_pil')

                    cti = q6_config.find('cti_reg')
                    if cti is not None and cti.get('cti'):
                        q6Cfg.cti_reg = cti.get('cti')
                    
                    qdss = q6_config.find('qdss_apb')
                    if qdss is not None:
                        if qdss.get('pwr_ctl'):
                            q6Cfg.qdss_apb_pwr_ctl = qdss.get('pwr_ctl')
                        if qdss.get('pwr_up_req'):
                            q6Cfg.qdss_apb_pwr_up_req = qdss.get('pwr_up_req')
                    
                    q6DbgVal = q6_config.find('q6DbgVal')
                    if q6DbgVal is not None:
                        if q6DbgVal.get('nAWMQ6DbgVal'):
                            q6Cfg.nAWMQ6DbgVal = q6DbgVal.get('nAWMQ6DbgVal')
                        if q6DbgVal.get('nSWMQ6DbgVal'):
                            q6Cfg.nSWMQ6DbgVal = q6DbgVal.get('nSWMQ6DbgVal')
                        if q6DbgVal.get('nLMCUQ6DbgVal'):
                            q6Cfg.nLMCUQ6DbgVal = q6DbgVal.get('nLMCUQ6DbgVal')
                break  # Exit loop after processing matching chipset
            except Exception as e:
                logger.error(f"Error parsing q6_config XML: {e}")
        else:
            logger.error("chipInfo is not available in debug context")
    # disables PIL timeouts, and it is called only in non-HLOS environments 
    # because the dapaccessGDB command fails when the target is running in HLOS
    if poll_for_subsys == "False":
        perform_war_disablepiltimeouts(debugger, command, result)

    # Check if poll for subsystem is FALSE
    if poll_for_subsys == "False":
        command_interpreter.HandleCommand("register read cpsr", return_obj)

        cpsr_output = return_obj.GetOutput()

        match = re.search(r"cpsr\s*=\s*0x([0-9a-fA-F]+)", cpsr_output)
        if match:
            cpsr_value = int(match.group(1), 16)

            # Mask the lower 5 bits
            mode_bits = cpsr_value & 0x1F

        if mode_bits == 0x05:
            NS = 0
        if mode_bits == 0x09:
            NS = 1

        # Check if subsystem is mpss or all and NS is 0
        if (subsystem == "mpss" or subsystem == "all") and NS == 0:
            logger.info(
                "PIL Setup: Setting TZ/HYP debug cookie nMPSSQ6DbgVal to 0x20000000."
            )
            cmd = "ta v &nMPSSQ6DbgVal"
            command_interpreter.HandleCommand(cmd, return_obj)
            return_obj_str = str(return_obj)
            index = return_obj_str.rindex("=")
            q6_debug_cookie = return_obj_str[index + 1 :].strip()

            cmd = "%s %s %s" % (
                "memory write -s 8 -f x ",
                str(q6_debug_cookie),
                "0x20000000",
            )
            debugger.HandleCommand(cmd)
            wait_for_pil = True

        # Check if subsystem is adsp or all or q6 and NS is 1
        if (subsystem == "adsp" or subsystem == "all" or subsystem == "q6") and NS == 1:
            logger.info(
                "PIL Setup: Setting TZ/HYP debug cookie nLPASSQ6DbgVal to 0x20000000."
            )
            cmd = "ta v &nLPASSQ6DbgVal"
            command_interpreter.HandleCommand(cmd, return_obj)
            return_obj_str = str(return_obj)
            index = return_obj_str.rindex("=")
            q6_debug_cookie = return_obj_str[index + 1 :].strip()
            logger.info(f"adsp debug cookie : {q6_debug_cookie}")

            cmd = "%s %s %s" % (
                "memory write -s 8 -f x ",
                str(q6_debug_cookie),
                "0x20000000",
            )
            debugger.HandleCommand(cmd)
            
            # variable is only present for aspen
            if hasattr(q6Cfg, 'nAWMQ6DbgVal') and q6Cfg.nAWMQ6DbgVal and q6Cfg.nAWMQ6DbgVal.lower() == 'present':
                cmd = "ta v &nAWMQ6DbgVal"
                command_interpreter.HandleCommand(cmd, return_obj)
                return_obj_str = str(return_obj)
                index = return_obj_str.rindex("=")
                nAWMQ6DbgVal = return_obj_str[index + 1 :].strip()
                logger.info(f"nAWMQ6DbgVal : {nAWMQ6DbgVal}")

                cmd = "%s %s %s" % (
                    "memory write -s 8 -f x ",
                    str(nAWMQ6DbgVal),
                    "0x2",
                )
                debugger.HandleCommand(cmd)

            # variable is only present for aspen
            if hasattr(q6Cfg, 'nSWMQ6DbgVal') and q6Cfg.nSWMQ6DbgVal and q6Cfg.nSWMQ6DbgVal.lower() == 'present':
                cmd = "ta v &nSWMQ6DbgVal"
                command_interpreter.HandleCommand(cmd, return_obj)
                return_obj_str = str(return_obj)
                index = return_obj_str.rindex("=")
                nSWMQ6DbgVal = return_obj_str[index + 1 :].strip()
                logger.info(f"nSWMQ6DbgVal : {nSWMQ6DbgVal}")

                cmd = "%s %s %s" % (
                    "memory write -s 8 -f x ",
                    str(nSWMQ6DbgVal),
                    "0x2",
                )
                debugger.HandleCommand(cmd)

            # variable is only present for LMCU
            if hasattr(q6Cfg, 'nLMCUQ6DbgVal') and q6Cfg.nLMCUQ6DbgVal and q6Cfg.nLMCUQ6DbgVal.lower() == 'present':
                cmd = "ta v &nLMCUQ6DbgVal"
                command_interpreter.HandleCommand(cmd, return_obj)
                return_obj_str = str(return_obj)
                index = return_obj_str.rindex("=")
                nLMCUQ6DbgVal = return_obj_str[index + 1 :].strip()
                logger.info(f"nLMCUQ6DbgVal : {nLMCUQ6DbgVal}")
                cmd = "%s %s %s" % (
                    "memory write -s 8 -f x ",
                    str(nLMCUQ6DbgVal),
                    "0x2",
                )
                debugger.HandleCommand(cmd)

            wait_for_pil = True

        # Check if subsystem is cdsp or all or q6 and NS is 1
        if (subsystem == "cdsp" or subsystem == "all" or subsystem == "q6") and NS == 1:
            logger.info(
                "PIL Setup: Setting TZ/HYP debug cookie nTURINGQ6DbgVal to 0x20000000."
            )
            cmd = "ta v &nTURINGQ6DbgVal"
            command_interpreter.HandleCommand(cmd, return_obj)
            return_obj_str = str(return_obj)
            index = return_obj_str.rindex("=")
            q6_debug_cookie = return_obj_str[index + 1 :].strip()
            logger.info(f"cdsp debug cookie : {q6_debug_cookie}")

            cmd = "%s %s %s" % (
                "memory write -s 8 -f x ",
                str(q6_debug_cookie),
                "0x20000000",
            )
            debugger.HandleCommand(cmd)
            wait_for_pil = True
            # Vote for CDSPNOC, this should be removed once UEFI and HLOS PIL do the job.
            dapAccessGDB(
                debugger, command, result, "Yes", None, None, 0xBA7004C, data=0x60004001
            )

        # Check if subsystem is soccp and NS is 1
        if subsystem == "soccp" and NS == 1:
            if lldb.SBTarget().FindSymbols("nSOCCPQ6DbgVal"):
                cmd = "ta v &nSOCCPQ6DbgVal"
                command_interpreter.HandleCommand(cmd, return_obj)
                return_obj_str = str(return_obj)
                index = return_obj_str.rindex("=")
                q6_debug_cookie = return_obj_str[index + 1 :].strip()

                cmd = "%s %s %s" % (
                    "memory write -s 8 -f x ",
                    str(q6_debug_cookie),
                    "0x20000000",
                )
                debugger.HandleCommand(cmd)
                logger.info("nSOCCPQ6DbgVal set as 1 !")
            else:
                logger.info("nSOCCPQ6DbgVal doesn't exist!")
            wait_for_pil = True

    # Check if poll for subsystem is TRUE
    elif poll_for_subsys == "True":

        try:
            tln = getTelnetHandle()
        except:
            logger.error(
                " Unable to establish telnet connection with openocd."
                "OpenOCD server should have been started prior to issuing this command. "
            )
        
        try:
            hwio_pil_path = derive_file_path(q6Cfg.hwio_pil)
        except:
            logger.error("Error trying to fetch hwio_pil path, check chip_config.xml")


        # loading hwio.cmm
        g_hwioConfig.hwio_access(
            debugger, command, result, hwio_pil_path, None, None, None
        )

    
        # Disable the non-secure wdog timer
        dapAccessTelnet(debugger, command, result, tln, True, None, None, 0x17410008, 0)

        # Check if subsystem is all
        if subsystem == "all":
            # Turn on the necessary clocks required for debugger to attach
            reg_addr = g_hwioConfig.hwioRegisterAddr(debugger, command, result, "HWIO_QDSS_AOSS_APB_SWAOCSR_SWAOCSR_SWAOCSR_REG_BASE_START_ADDR")
            dapAccessTelnet(debugger, command, result, tln, True, None, None, int(reg_addr, 16) + 0xFB0, 0xC5ACCE55)

            reg_addr = g_hwioConfig.hwioRegisterAddr(debugger, command, result, "HWIO_QDSS_AOSS_APB_SWAOCSR_CS_SWAOCSR_PDCPWRUPREQ_ADDR")
            memory_read_value = dapAccessTelnet(debugger, command, result, tln, True, int(reg_addr, 16), None, None, None)
            dapAccessTelnet(debugger, command, result, tln, True, None, None, int(reg_addr, 16), int(memory_read_value, 16) | 0x4)

            # Trigger 0 is connected to the Q6 halt
            logger.info("Configuring CTI for Q6 halt...")
            config_cti(debugger, command, result, tln, cti_base_addr="QDSS_MSS_QDSP6_QDSP6SS_QDSP6SS_CSCTI_REG_BASE_START", cti_input_trigger=0, cti_channel_num=0)

            # Trigger 0 is connected to Q6 halt
            logger.info("Configuring CTI for Q6 halt...")
            config_cti(debugger, command, result, tln, cti_base_addr="QDSS_TURING_APB_TURING_QDSP6SS_QDSP6SS_CSCTI_REG_BASE_START", cti_input_trigger=0, cti_channel_num=0)

            # Trigger 0 is connected to Q6 halt
            logger.info("Configuring CTI for Q6 halt...")
            config_cti(debugger, command, result, tln, cti_base_addr="QDSS_LPASS_LPI_QDSP6_QDSP6SS_QDSP6SS_CSCTI_REG_BASE_START", cti_input_trigger=0, cti_channel_num=0)

            # Trigger 1 is a SPARE one on APPS_CTI_0
            logger.info("Configuring CTI for APPS_CTI_0...")
            # only for Aspen we are skipping, so all other chipsets we will perform these writes
            if (q6Cfg.cti_reg == ""):
                config_cti(debugger, command, result, tln, cti_base_addr="QDSS_APSS_CTI_0_CTI_0_CSCTI_REG_BASE_START", cti_output_trigger=0, cti_channel_num=0, wait_for_event="Output")

                config_cti(debugger, command, result, tln, cti_base_addr="QDSS_APSS_CTI_0_CTI_0_CSCTI_REG_BASE_START", cti_output_trigger=0, cti_channel_num=0, wait_for_event="Output")

                config_cti(debugger, command, result, tln, TIBaseAddr="QDSS_APSS_CTI_0_CTI_0_CSCTI_REG_BASE_START", cti_output_trigger=0, cti_channel_num=0, wait_for_event="Output")
  
            logger.info("MPSS/CDSP/ADSP are out of reset")

        # Check if subsystem is mpss
        if subsystem == "mpss":
            # Trigger 0 is connected to the Q6 halt
            logger.info("Configuring CTI for Q6 halt...")
            config_cti( debugger, command, result, tln, cti_base_addr="QDSS_MSS_QDSP6_QDSP6SS_QDSP6SS_CSCTI_REG_BASE_START", cti_input_trigger=0, cti_channel_num=3)

            # Trigger 0 is a SPARE one on APPS_CTI_0
            logger.info("Configuring CTI for APPS_CTI_0...")
            config_cti( debugger, command, result, tln, cti_base_addr="QDSS_APSS_CTI_0_CTI_0_CSCTI_REG_BASE_START", cti_output_trigger=0, cti_channel_num=3, wait_for_event="Output")

            logger.info("MPSS Q6 is out of reset")

        # Check if subsystem is cdsp or q6
        if subsystem == "cdsp" or subsystem == "q6":
            # Trigger 0 is connected to Q6 halt
            logger.info("Configuring CTI for Q6 halt...")
            config_cti(debugger, command, result, tln, cti_base_addr="QDSS_TURING_APB_TURING_QDSP6SS_QDSP6SS_CSCTI_REG_BASE_START", cti_input_trigger=0, cti_channel_num=1)

            # Trigger 1 is a SPARE one on APPS_CTI_0
            logger.info("Configuring CTI for APPS_CTI_0...")
            config_cti( debugger, command, result, tln, cti_base_addr="QDSS_APSS_CTI_0_CTI_0_CSCTI_REG_BASE_START", cti_output_trigger=1, cti_channel_num=1, wait_for_event="Output")

            logger.info("CDSP Q6 is out of reset")

        # Check if subsystem is adsp or q6
        if subsystem == "adsp" or subsystem == "q6":
            # Turn on the necessary clocks required for debugger to attach

            if (q6Cfg.qdss_apb_pwr_ctl != ""):
                reg_addr = q6Cfg.qdss_apb_pwr_ctl
            else:
                reg_addr = g_hwioConfig.hwioRegisterAddr(debugger, command, result, "HWIO_QDSS_AOSS_APB_SWAOCSR_SWAOCSR_SWAOCSR_REG_BASE_START_ADDR")

            logger.info(f"reg_addr : {reg_addr}")
            dapAccessTelnet( debugger, command, result, tln, True, None, None, int(reg_addr, 16) + 0xFB0, 0xC5ACCE55)

            if (q6Cfg.qdss_apb_pwr_up_req != ""):
                reg_addr = q6Cfg.qdss_apb_pwr_up_req
            else:
                reg_addr = g_hwioConfig.hwioRegisterAddr( debugger, command, result, "HWIO_QDSS_AOSS_APB_SWAOCSR_CS_SWAOCSR_PDCPWRUPREQ_ADDR")

            logger.info(f"reg_addr : {reg_addr}")
            memory_read_value = dapAccessTelnet( debugger, command, result, tln, True, int(reg_addr, 16), None, None, None)
            dapAccessTelnet( debugger, command, result, tln, True, None, None, int(reg_addr, 16), int(memory_read_value, 16) | 0x4)

            # only for Aspen we are skipping, so all other chipsets we will perform these writes
            if (q6Cfg.cti_reg == ""):
                # Trigger 0 is connected to Q6 halt
                logger.info("Configuring CTI for Q6 halt...")
                config_cti( debugger, command, result, tln, cti_base_addr="QDSS_LPASS_LPI_QDSP6_QDSP6SS_QDSP6SS_CSCTI_REG_BASE_START", cti_input_trigger=0, cti_channel_num=0)

                # Trigger 0 space is a SPARE one on APPS_CTI_0fv
                logger.info("Configuring CTI for APPS_CTI_0...")
                config_cti( debugger, command, result, tln, cti_base_addr="QDSS_APSS_CTI_0_CTI_0_CSCTI_REG_BASE_START", cti_output_trigger=1, cti_channel_num=0, wait_for_event="Output")

            logger.info("ADSP Q6 is out of reset")

        # Check if subsystem is soccp
        if subsystem == "soccp":
            soccp_cease = 0
            reg_addr = g_hwioConfig.hwioRegisterAddr(debugger, command, result, "HWIO_SOCCP_SOCCP_RVSSCP_STATUS_ADDR")

            memory_read_value = dapAccessTelnet( debugger, command, result, tln, True, int(reg_addr, 16), None, None, None)
            soccp_cease = (int(memory_read_value, 16) & 0x4) >> 0x02
            adapter = g_dbgContext.debug_adapter
          
            logger.info("SOCCP checking STATUS ing...")
            while soccp_cease == 0:
                if adapter.lower() == "eud":
                    time.sleep(0.1)
                memory_read_value = dapAccessTelnet( debugger, command, result, tln, True, int(reg_addr, 16), None, None, None)
                soccp_cease = (int(memory_read_value, 16) & 0x4) >> 0x02
            logger.info("SOCCP is out of reset")

        closeTelnetHandle()

    return wait_for_pil


def perform_war_disablepiltimeouts(debugger, command, result):
    """
    This function disables PIL timeouts.

    @param debugger         -   An instance of the SBDebugger Object that
                                provides access to SBTargets used for
                                debugging.
    @param command          -   String containing all the input arguments
                                for the used command.
    @param result           -   A return object which encapsulates
                                success/failure information for the command
                                and output text that needs to be printed
                                as a result of the command.
    """
    logger.info("PIL Setup: Workaround! Disabling PIL timeouts...")

    chipset_name = g_dbgContext.chipsetName
    if (chipset_name.upper() == "LANAI"):
        disable_pil_timeout_addr = 0x146AA6DC
    else:
        disable_pil_timeout_addr = 0x146806DC

    dapAccessGDB(debugger, command, result, "yes", None, None, disable_pil_timeout_addr, 0x53444247)


def derive_file_path(cmm_file_name):
    """
    This function derives the file path for the given CMM file name based on the
    chip set name and the common Core tools directory structure. It returns
    the full path to the CMM file required for the current chipset. The
    function assumes that the meta path and chipset name are already known
    and available in the global debug context object `g_dbgContext`.
    @param cmm_file_name    -   Name of the CMM file to be located.

    @return                 -   full path to the CMM file. 

    """
    if g_dbgContext.openocdConfig != None:
        meta_path = g_dbgContext.openocdConfig.getMetaPath()
        chipset_name = g_dbgContext.chipsetName.lower()
        file_path_root = "{}/common/Core/tools/silicon/{}/fbc/images/pil/cate".format(meta_path, chipset_name)
        
        if (chipset_name.lower() == 'aspen'):
            file_path_root = "{}/common/Core/tools/{}/silicon/fbc/images/pil/cate".format(meta_path, chipset_name)
            
        file_path = "{}/{}".format(file_path_root, cmm_file_name)
        logger.info(f"file_path : {file_path}/{cmm_file_name}")
        return file_path
