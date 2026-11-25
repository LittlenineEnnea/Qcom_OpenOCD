#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file q6.py
#
# @section Description
# This file contains a script for PBL image debugging.

import lldb
from utils.debug_context import logger
from utils.debug_context import g_dbgContext
from utils.source_map import setSourceMap
from lldb_pkg.arch.q6.q6_subsys_context import q6Cfg
from lldb_pkg.target.kaanapali.image.hyp import debugHYP
from lldb_pkg.target.kaanapali.image.debugwithpil import pil_debugger

def q6Entry(debugger, command, exe_ctx, result, subsystem):
    """!   
    This function is used to define a command to configure apps to halt Q6 at SW ENTRY
    by setting debug cookie and also disable pil timeout. 
                                              
    Usage : q6Entry -s <subsystem>

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for 
                                debugging.

    @param subsystem        -   a variable used to denote the dsp subsystem to 
                                be debugged.

    """    

    q6_debug_cookie = 0x0
    disable_pil_timeout_addr = 0x0
    chipset_name = g_dbgContext.chipsetName.lower()

    command_interpreter = debugger.GetCommandInterpreter()
    return_obj = lldb.SBCommandReturnObject()

    wait_pil = debugHYP(debugger, command, exe_ctx, result, subsys=subsystem)

    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname=chip.get("name")
        if (chipname.upper() == chipset_name.upper()):
            wdog_config = chip.find("wdog_config")

    # disabling watchdog timer 1 and 2
    wdog_timer1 = wdog_config.find('APSS_WDT_TMR1_WDOG_CTL').attrib['reg']
    wdog_timer2 = wdog_config.find('APSS_WDT_TMR2_WDOG_CTL').attrib['reg']

    dap_write_substring = "dap_access -w "
    cmd_list = [
        # APSS_WDT_TMR1_WDOG_CTL 
        "%s %s %s" % (dap_write_substring, str(wdog_timer1), "-v 0x0"),
        # APSS_WDT_TMR2_WDOG_CTL 
        "%s %s %s" % (dap_write_substring, str(wdog_timer2), "-v 0x0"),
    ]

    for i in range(len(cmd_list)):
        logger.info(cmd_list[i])
        debugger.HandleCommand(cmd_list[i])
    
    logger.info ("Q6 Debug config from APSS - complete!!")
    logger.info("Continuing target bootup, start subsystem DEBUG - STEP 2")


def mmuSync(debugger, subsystem):
    """!   
    This function is used to configure bitmap based 
    vtlb refresh logic, such that only modified vtlb entries can be 
    updated. The initialization of this logic and clear vtlb entries call
    has been moved to debugger backend code such that as soon as vtlb is 
    found to be enabled, bitmap logic initialization is done from code.
                                              
    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for 
                                debugging.

    @param subsystem        -   a variable used to denote the dsp subsystem to 
                                be debugged.


    """    
    chipset_name = g_dbgContext.chipsetName.upper()
    ss_string = f"{str(chipset_name).upper()}.{subsystem} hexagon "

    # Check if required VTLB attributes are initialized
    if not hasattr(q6Cfg, 'bitmap_addr') or not hasattr(q6Cfg, 'vtlb_entries_addr') or not hasattr(q6Cfg, 'vtlb_revision_addr'):
        logger.warning("VTLB configuration attributes not initialized, skipping mmuSync")
        return
    
    bitmap_init_cmd = [
        f"{ss_string} setVTLBParam {hex(q6Cfg.bitmap_addr)} {hex(q6Cfg.vtlb_entries_addr)}\n",
        f"{ss_string} setRevision {q6Cfg.vtlb_revision_addr}\n",
    ]

    for cmd in bitmap_init_cmd:
        sync_command = f"{q6Cfg.monitor_command} {cmd}"
        logger.info(sync_command)
        debugger.HandleCommand(sync_command)

def debugRootPd(debugger, dsp, entry = None, tln = None):
    """!   
    This function is used to define a command to perform root pd debug of 
    q6 subsystems

    Usage: root_pd_debug -o <subsystem> -e <entry_name>

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for 
                                debugging.
    @param dsp              -   a variable used to denote the dsp subsystem to 
                                be debugged.

    @param entry             -   a variable used to denote the entry of debug that
                                is being performed, such as SW_ENTRY, QURTOS_Init,
                                QURTOS_root_entry, lib_qurt_init and main.

    """    

    delay = True
    ss_string = " hexagon "
    chipset_name = g_dbgContext.chipsetName.lower()

    q6_config_command = f"{str(chipset_name).upper()}.{dsp.lower()}{ss_string}"
    config_command = f"{q6Cfg.monitor_command} {q6_config_command}"

    debugger.HandleCommand("settings set target.expr-base-addr 0xee000000")
    debugger.HandleCommand("setting set auto-confirm on")

    if dsp.upper() == 'MPSS':
        mpss_elf_path = g_dbgContext.openocdConfig.getBinaryPath("modem", \
                            f'orig_MODEM_PROC_IMG_{chipset_name}.gen.testQ.elf')
        logger.info("mpss_elf_path: %s", mpss_elf_path)
        bin_path = mpss_elf_path[:mpss_elf_path.rindex("ms/") + 3]
        logger.info("bin_path: %s", bin_path)

    elif dsp.upper() in ['ADSP', 'CDSP']:
        root_elf_name = f'ROOT_{chipset_name}.{dsp.lower()}.prodQ.elf'
        logger.info("ROOT FILE NAME: %s", root_elf_name)
        elf_path = g_dbgContext.openocdConfig.getBinaryPath(str(dsp.lower()), root_elf_name)
        bin_path = elf_path[:elf_path.rindex("ms/") + 3]

        #  perform config only at the start

        if (q6Cfg.config_complete == False):
            sendQ6ConfigCommands(debugger, dsp.lower(), tln)
            cmd = f"telnet -c '{str(chipset_name).upper()}.{str(dsp.lower())} hexagon time_delay 10000'"
            debugger.HandleCommand(cmd)
            q6Cfg.config_complete = True

        if entry.upper() == 'MAIN' :
            if q6Cfg.run_till_main == False:
                q6Cfg.run_till_main = True
                # Replaced custom LLDB command with direct function calls (debugRootPd) to fix debug failures in VSCode
                debugRootPd(debugger, dsp.lower(), 'SW_ENTRY', tln)
                debugRootPd(debugger, dsp.lower(), 'QURTOS_init', tln)
                debugRootPd(debugger, dsp.lower(), 'QURTOS_ROOT_ENTRY', tln)
                debugRootPd(debugger, dsp.lower(), 'LIB_QURT_INIT', tln)
                debugRootPd(debugger, dsp.lower(), 'MAIN', tln)
                userPD(debugger)

                commands = [
                    f"telnet -c '{str(chipset_name).upper()}.{str(dsp.lower())} hexagon time_delay 1000'"
                ]
                delay = True
            else :
                lpm_disable_addr = 0
                dog_disable_state_addr = 0
                dbg_cfg_addr = 0

                try:
                    lpm_disable_addr = q6Cfg.getAddress("g_sleepAllowLowPowerModes")
                except Exception as e:
                    logger.error(f"Error getting LPM_disable_addr: {str(e)}")

                try:
                    dog_disable_state_addr = q6Cfg.getAddress("dog_disable_state")
                except Exception as e:
                    logger.error(f"Error getting dog_disable_state: {str(e)}")

                try:
                    dbg_cfg_addr = q6Cfg.getAddress("bIsDbgCfgSet")
                    if dbg_cfg_addr != 0 and isinstance(dbg_cfg_addr, int):
                        debugger.HandleCommand(f"memory write -f x {hex(dbg_cfg_addr)} 0x1")
                except Exception as e:
                    logger.warning(f"bIsDbgCfgSet is not found: {str(e)}")

                commands = ['br del']

                if lpm_disable_addr != 0 and isinstance(lpm_disable_addr, int):
                    commands.append(f'memory write -f x {hex(lpm_disable_addr)} 0x0')
                else:
                    logger.error("Skipping LPM disable - address is invalid")

                if dog_disable_state_addr != 0 and isinstance(dog_disable_state_addr, int):
                    commands.append(f'memory write -f x {hex(dog_disable_state_addr)} 0x3F')
                else:
                    logger.error("Skipping dog disable - address is invalid")

                commands.extend([
                    'br s -n main --hardware',
                    "br s -n QURTK_main_fatal_error",
                    f"{config_command} multi_th_init",
                    'c',
                    'br del'
                ])

        elif entry.upper() == 'SW_ENTRY':
            g_dbgContext.getDbgPortNum(dsp.lower())
        
            commands = [
                f"connect_remote -s {str(dsp.lower())}",
            ]

            if (q6Cfg.tcsr_reg != "" and q6Cfg.tcsr_value != ""):
                logger.info(f"dap_access -w  {q6Cfg.tcsr_reg} -v {q6Cfg.tcsr_value}")
                commands.append(f"dap_access -w  {q6Cfg.tcsr_reg} -v {q6Cfg.tcsr_value}")

        elif entry.upper() == 'QURTOS_INIT':
            if (q6Cfg.run_till_main == False):
                debugRootPd(debugger, dsp.lower(), 'SW_ENTRY', tln)
            commands = [
                f'br s -n QURTOS_init --hardware',
                "c",
                'br del',
            ]
            # we are only setting vtlb revision, bitmap and vtlb entry address
            # this call has been moved earlier as we are enabling bitmap
            # logic as soon as we detect vtlb enabled from code,
            # instead of from script/commands.
            mmuSync(debugger, dsp.lower())

        elif entry.upper() == 'QURTOS_ROOT_ENTRY':
            if (q6Cfg.run_till_main == False):
                debugRootPd(debugger, dsp.lower(), 'QURTOS_init', tln)
            commands = [
                f"br s -n qurtos_root_entry",
                "c",
                'br del',
            ]

        elif entry.upper() == 'LIB_QURT_INIT':
            if (q6Cfg.run_till_main == False):
                debugRootPd(debugger, dsp.lower(), 'QURTOS_ROOT_ENTRY', tln)
            commands = [
                'br s -n lib_qurt_init --hardware',
                'c'
            ]

        for cmd in commands:
            logger.info(cmd)
            debugger.HandleCommand(cmd)

def userPD(debugger):
    """!   
    This function is used to fetch user PD elf filenames for targets that have it 
    specified in config. For others, it will be skipped with no warnings/error logs as 
    it is not applicable for most targets.                                             

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for 
                                debugging.
    """    

    if (q6Cfg.audio_file != ""):
        loadPD(debugger, q6Cfg.audio_file)
    if (q6Cfg.charger_file != ""):
        loadPD(debugger, q6Cfg.charger_file)
    if (q6Cfg.sensor_file != ""):
        loadPD(debugger, q6Cfg.sensor_file)
    if (q6Cfg.bt_file != ""):
        loadPD(debugger, q6Cfg.bt_file)
        
def loadPD(debugger, module_file):
    """!   
    This function is used to load elfs and is used in userPD(). 

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for 
                                debugging.
    @param module_file      -   variable to denote filename of the elf
    
    """    

    module = g_dbgContext.tgt.AddModule(module_file, None, None, None)
    load_module = f"target modules load --file {module_file} --slide 0x0"
    logger.info(f"{load_module}")
    debugger.HandleCommand(f"{load_module}")

def sendQ6ConfigCommands(debugger, subsystem, tln, mode = None,path = None):
    """!   
    This function is used to define a command to configure apps to halt Q6 at SW ENTRY
    by setting debug cookie and also disable pil timeout. 
                                              
    Usage : q6Entry -s <subsystem>

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for 
                                debugging.

    @param subsystem        -   a variable used to denote the dsp subsystem to 
                                be debugged.
    @param tln              -   The telnet handle.

    @param mode             -   a variable used to denotes the mode of debug,
                                such as untrusted (OEMPD) or trusted (root pd).
    @param path             -   a variable used to set path for Q6 image and begin 
                                debug.

    """    
    core_clock_list = []
    etm_clock_list = []
    chipset_name = g_dbgContext.chipsetName.upper()

    if path == None and subsystem.lower() != 'mpss':
        path = g_dbgContext.openocdConfig.getImageBasePath(subsystem.lower())
        path = f"{path}/{subsystem}_proc/build/ms"
    elif subsystem.lower() == 'mpss' and mode.lower() != 'oempd':
        path = g_dbgContext.openocdConfig.getImageBasePath('modem')
        path = f"{path}/modem_proc/build/ms"
    

    logger.info(f"subsystem is set to : {subsystem}")
    logger.info(f"path is set to : {path}")
    logger.info(f"Mode is set to : {mode}")

    for chip in g_dbgContext.chipInfo.findall('chipset'):
        if chip.get('name').upper() ==  chipset_name:

            ss = chip.find(f'{subsystem}_config')
            q6Cfg.ukernel_file = \
                f"{path}/{ss.find('ukernel_path').get('ukernel_file')}"
            q6Cfg.root_file = \
                f"{path}/{ss.find('root_path').get('root_path')}"
            user_pd = ss.find('user_pd')
            if user_pd is not None:
                q6Cfg.audio_file = user_pd.get('audio_path')
                q6Cfg.charger_file = user_pd.get('charger_path')
                q6Cfg.sensor_file = user_pd.get('sensor_path')
                q6Cfg.bt_file = user_pd.get('bt_path')
                
            tcsr = ss.find('tcsr_reg')
            if tcsr is not None:
                q6Cfg.tcsr_reg = tcsr.get('tcsr')
                q6Cfg.tcsr_value = tcsr.get('value')
            
            tlb_num = ss.find('tlb_entries').get('tlb_num')
            wdog_ctl = ss.find('wdog_ctl').get('wdog')
            hw_thrds = ss.find('hw_threads').get('thrd_num')

            core_clock_list = [clk_reg.attrib['core_clk'] for clk_reg in \
                               ss.iter('core_clk_reg')]
            etm_clock_list = [clk_reg.attrib['etm_clk'] for clk_reg in    \
                              ss.iter('etm_clk_reg')]

            if mode != 'OEMPD':
                g_dbgContext.tgt = debugger.CreateTarget(q6Cfg.ukernel_file)
                root_module = g_dbgContext.tgt.AddModule(q6Cfg.root_file, None, None, None)
                load_module = f"target modules load --file {q6Cfg.root_file} --slide 0x0"

                logger.info(f"{load_module}")
                debugger.HandleCommand(f"{load_module}")

                vtlb_main_address = q6Cfg.getAddress("QURTK_vtlb_main")
                vtlb_data_address = q6Cfg.getAddress ("QURTK_vtlb_data")
                q6Cfg.qurtos_init_addr = q6Cfg.getAddress("QURTOS_init")

                try:
                    q6Cfg.bitmap_addr = q6Cfg.getAddress("QURTK_vtlb_bitmap")
                    q6Cfg.vtlb_entries_addr = q6Cfg.getAddress("QURTK_vtlb_entries")
                    q6Cfg.vtlb_revision_addr = q6Cfg.getAddress("QURTK_vtlb_revision")

                    logger.info(f"bitmap addr : {q6Cfg.bitmap_addr}")
                    logger.info(f"q6Cfg.vtlb_entries_addr : "
                                f"{q6Cfg.vtlb_entries_addr}")
                    logger.info(f"QURTK_vtlb_revision : "
                                f"{q6Cfg.vtlb_revision_addr}")
                except (IndexError, AttributeError, KeyError) as e:
                    logger.warning(f"vtlb variables not found in ELF: {type(e).__name__}: {str(e)}")
                except Exception as e:
                    logger.error(f"Unexpected error retrieving vtlb addresses: {type(e).__name__}: {str(e)}")

                logger.info(f"wdog address fetched is {wdog_ctl}")
                debugger.HandleCommand(f'dap_access -w {wdog_ctl} -v 0x0')
            if mode == 'OEMPD' :
                root_module = debugger.CreateTarget(q6Cfg.root_file)
                
            # Packing a tuple init_args of initialization arguments for the initCfg function.
            init_args = (chipset_name, subsystem, hw_thrds, tlb_num, wdog_ctl)

            if mode == 'OEMPD':
                q6Cfg.initCfg(*init_args, hex(0), hex(0), core_clock_list, etm_clock_list)
                q6Cfg.send_oempd_config_commands(tln)
            elif subsystem == 'cdsp':
                q6Cfg.initCfg(*init_args, hex(vtlb_main_address),\
                            hex(vtlb_data_address), None, etm_clock_list )
                q6Cfg.send_cdsp_config_commands(tln)
            elif subsystem == 'adsp':
                q6Cfg.initCfg(*init_args, hex(vtlb_main_address), \
                            hex(vtlb_data_address), core_clock_list, etm_clock_list)

                q6Cfg.send_adsp_config_commands(tln)


            cmd = f"{str(chipset_name).upper()}.{subsystem} arp_examine\n"
            logger.info(cmd)
            tln.write(cmd.encode('ascii'))
            tln.read_until(b"> ")

            if mode == 'OEMPD':
                cmd = f"{str(chipset_name).upper()}.{subsystem} hexagon untrusted\n"
                logger.info(cmd)
                tln.write(cmd.encode('ascii'))
                tln.read_until(b"> ")

            logger.info(q6Cfg.ukernel_file)
            logger.info(q6Cfg.root_file)

        try:
            source_path_set = g_dbgContext.sourcePath[subsystem]
            logger.info("previously set source path is %s", source_path_set)
            setSourceMap(debugger, subsystem, source_path_set)
            logger.info("setting source map")
        except KeyError:
            logger.warning("source path not set, please set source path to view code")
