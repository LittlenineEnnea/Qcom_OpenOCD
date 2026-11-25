#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file uefi.py
#
# @section Description
# This file contains a script for UEFI image debugging on Hawi chipset.
# Ported from debug_uefi.cmm and dxe_dbg.cmm.

import os
import sys
import time
import lldb
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.dap_access import dapAccessGDB
from lldb_pkg.target.hawi.image.hyp import debugHYP

def update_core_power_state(debugger, command, result):
	"""!
	Workaround: Force higher pState configuration to avoid potential bus errors.

	Reads the target DVFS state from DVFS_CTL registers and writes it to the
	corresponding DVFS_EPP_CTL registers' corPwrDnCfgPst field using secure
	memory access (EZAXI) via DAP.

	@param debugger         -   An instance of the SBDebugger Object
	@param command          -   Command string (unused)
	@param result           -   Command result object
	"""
	BIT_MASK = 0x3f

	# DVFS control register addresses
	DVFS_CTL = [0x198B0000, 0x198B5000, 0x198BA000]

	# DVFS runtime control register addresses (EPP_CTL)
	DVFS_EPP_CTL = [0x198B1B60, 0x198B6B60, 0x198BBB60]

	logger.error("Workaround! Forcing higher pState configuration to avoid potential bus errors.")

	# Process each DVFS controller (0, 1, 2)
	for i in range(3):
		try:
			# Read tgtDvfsSt field from source register (bits [21:16])
			ctl_value = dapAccessGDB(debugger, command, result, "Yes", DVFS_CTL[i], None, None, None)
			if ctl_value is None:
				logger.warning(f"Failed to read DVFS_CTL[{i}] at 0x{DVFS_CTL[i]:X}")
				continue

			ctl_value = int(ctl_value, 16)
			tgt_dvfs_st = (ctl_value >> 0x10) & BIT_MASK

			# Read current corPwrDnCfgPst field from destination register
			epp_ctl_value = dapAccessGDB(debugger, command, result, "Yes", DVFS_EPP_CTL[i], None, None, None)
			if epp_ctl_value is None:
				logger.warning(f"Failed to read DVFS_EPP_CTL[{i}] at 0x{DVFS_EPP_CTL[i]:X}")
				continue

			epp_ctl_value = int(epp_ctl_value, 16)

			# Update corPwrDnCfgPst field (clear lower 6 bits, then set with tgtDvfsSt)
			cor_pwr_dn_cfg_pst = (epp_ctl_value & ~BIT_MASK) | tgt_dvfs_st

			# Write updated value back to EPP_CTL register
			dapAccessGDB(debugger, command, result, "Yes", None, None, DVFS_EPP_CTL[i], cor_pwr_dn_cfg_pst)

			logger.info(f"Updated DVFS_EPP_CTL[{i}]: 0x{cor_pwr_dn_cfg_pst:08X}")

		except Exception as e:
			logger.warning(f"Error updating DVFS_EPP_CTL[{i}]: {e}")
			continue

def printUEFIImageFiles():
    logger.info("custom function called")
    try:
        metaInfo = g_dbgContext.openocdConfig._openocdConfig__metaInfo
        imageInfo = metaInfo._metaInfo__imageInfo['uefi']
        buildPath = imageInfo._ImageInfo__buildPath
        buildFilesInfo = imageInfo._ImageInfo__buildFilesInfo
        logger.info(f"\nTotal UEFI files: {len(buildFilesInfo)}")
        logger.info(f"Build Path: {buildPath}\n")
        for fileName, relativePath in buildFilesInfo.items():
            logger.info(f"{fileName:<40} {relativePath}")
    except Exception as e:
        logger.error(f"Error: {e}")

def validateFvHeader(debugger, addr):
    """!
    Validate FV (Firmware Volume) header signature.

    @param debugger         -   An instance of the SBDebugger Object
    @param addr             -   FV header address to validate
    """
    returnObj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()

    sigAddr = hex(int(addr, 16) + int("0x28", 16))
    cmd = "x/wx {}".format(sigAddr)
    commandInterpreter.HandleCommand(cmd, returnObj)
    res = str(returnObj.GetOutput())
    sig = repr(res[12:22]).replace("'", "")

    if int(sig, 16) == int("0x4856465F", 16):
        return
    else:
        logger.error(" Invalid FV header, exiting...")
        sys.exit(1)


def verifyInfoBlk(debugger, addr, baseAddr):
    """!
    Verify UEFI Info Block signature.

    @param debugger         -   An instance of the SBDebugger Object
    @param addr             -   Info block pointer offset address
    @param baseAddr         -   UEFI base address
    """
    returnObj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()

    infoBlkPtrOffsetAddr = str(addr)
    addr = hex(int(baseAddr, 16) + int(infoBlkPtrOffsetAddr, 16))
    cmd = "x/wx {}".format(addr)
    commandInterpreter.HandleCommand(cmd, returnObj)
    res = str(returnObj.GetOutput())
    sig = repr(res[12:22]).replace("'", "")

    if int(sig, 16) == int("0x6B6C4249", 16):
        return
    else:
        logger.error(" UefiInfoBlk not found, exiting...")
        sys.exit(1)


def memoryDump(debugger, infoBlkAddr, iBlkTotalCmdSize, iBlkSymbolCmdString,
               iBlkCmdStartOffset, debug_dxe_core):
    """!
    Dump memory range containing DXE module list and offsets.

    @param debugger         -   An instance of the SBDebugger Object
    @param infoBlkAddr      -   Info block address
    @param iBlkTotalCmdSize -   Total command size offset
    @param iBlkSymbolCmdString - Symbol command string offset
    @param iBlkCmdStartOffset - Command start offset
    @param debug_dxe_core   -   Flag to debug DXE core
    """
    returnObj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()

    cmd = "x/wx {}".format(hex(int(infoBlkAddr, 16) + int(iBlkSymbolCmdString, 16)))
    commandInterpreter.HandleCommand(cmd, returnObj)
    res = str(returnObj.GetOutput())
    cmdAddr = repr(res[12:22]).replace("'", "")

    cmd = "x/wx {}".format(hex(int(infoBlkAddr, 16) + int(iBlkTotalCmdSize, 16)))
    commandInterpreter.HandleCommand(cmd, returnObj)
    res = str(returnObj.GetOutput())
    cmdSize = repr(res[12:22]).replace("'", "")

    CmdEnd = hex(int(cmdAddr, 16) + int(cmdSize, 16) - int("0x1", 16)).replace("L", "")

    cmd = "x/wx {}".format(hex(int(infoBlkAddr, 16) + int(iBlkCmdStartOffset, 16)))
    commandInterpreter.HandleCommand(cmd, returnObj)
    res = str(returnObj.GetOutput())
    cmdincroffset = repr(res[12:22]).replace("'", "")
    cmdincroffset = hex(int(cmdincroffset, 16) + int(cmdAddr, 16)).replace("L", "")

    cmd = "memory read --outfile Uefi_memory_dump.txt --force --binary {} {}".format(
        str(cmdincroffset), str(CmdEnd))
    logger.error(f"command is {cmd}")
    debugger.HandleCommand(cmd)

    if debug_dxe_core:
        addr = hex(int(infoBlkAddr, 16) + int(iBlkCmdStartOffset, 16))
        cmd = "memory write -s 4 -f x {} {}".format(addr, cmdSize)
        commandInterpreter.HandleCommand(cmd, returnObj)


def debugUEFI(debugger, command, exe_ctx, result, inputarg=None, ModuleName=None):
    """!
    Debug UEFI firmware on Hawi chipset.

    This function orchestrates UEFI debugging by:
    1. Booting through HYP to UEFI entrypoint
    2. Loading SEC module symbols
    3. Loading and debugging DXE modules

    Usage: debug -d uefi [-i <module_name>]

    @param debugger         -   An instance of the SBDebugger Object
    @param command          -   String containing all input arguments
    @param exe_ctx          -   An execution context object
    @param result           -   A return object for command output
    @param inputarg         -   Flag indicating if called from another script
    @param ModuleName       -   Input module name for debugging
    """

    Dxe_Name = None
    line_num = 0
    Dxe_Name_List = []
    Dxe_dll_Path_List = []
    offset_List = []

    if inputarg is None and ModuleName is None:
        logger.info(" --- Preparing for Debug of UEFI Image ---")

    debugger.HandleCommand("settings set auto-confirm on")
    Module_Name = ModuleName
    Chipset_name = g_dbgContext.chipsetName.lower()

    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname = chip.get('name')
        if chipname.upper() == Chipset_name.upper():
            config = chip.find('configure_target')
            offset = chip.find('InitOffsets')
            file_info = chip.find('file_info')

    UEFI_baseAddr = config.find('UefiRamEntryAddr').attrib['Addr']
    InfoBlkPtrOffset = config.find('InfoBlkPtrOffset').attrib['Addr']
    SecLoadAddrOffset = offset.find('SecLoadAddrOffset').attrib['Offset']
    IBlkDbgStopDrvrStr = offset.find('IBlkDbgStopDrvrStr').attrib['Offset']
    IBlkBdsHotkey = offset.find('IBlkBdsHotkey').attrib['Offset']
    IBlkForceDisableHWWdog = offset.find('IBlkForceDisableHWWdog').attrib['Offset']
    IBlkModuleName = offset.find('IBlkModuleName').attrib['Offset']
    iBlkTotalCmdSize = offset.find('IBlkTotalCmdSize').attrib['Offset']
    iBlkSymbolCmdString = offset.find('IBlkSymbolCmdString').attrib['Offset']
    iBlkCmdStartOffset = offset.find('IBlkCmdStartOffset').attrib['Offset']

    LoadDxeCore_addr = None
    uefi_image_path = g_dbgContext.openocdConfig.getImageBasePath("uefi")
    uefi_image_path = uefi_image_path.replace("\n", "")
    selTarget = debugger.GetSelectedTarget()

    args = command.split()
    if len(args) >= 5:
        logger.info(f"arguments {args[4]}")
        Sec_debug_path = args[4]
    elif len(args) == 2:
         pass
    else:
        logger.error("Provide valid sec.debug path in UEFI_BIN_PATH in settings.json")
        sys.exit(1)

    # if Sec_debug_path:
    #     pass
    # else:
    #     Sec_debug_path = g_dbgContext.openocdConfig.getBinaryPath('uefi', 'Sec.debug')

    CoreObjPath = config.find('CoreObj').attrib['path']
    CoreObj = os.path.join(uefi_image_path, CoreObjPath)
    # logger.error(f"core obj path {CoreObj}")
    if Module_Name is None:
        # printUEFIImageFiles()
        debugHYP(debugger, command, exe_ctx, result, "run")
        debugger.HandleCommand("br del")

        cmd = "br s -a {} --hardware".format(UEFI_baseAddr)
        debugger.HandleCommand(cmd)
        debugger.HandleCommand("c")
        logger.info(" FBC: UEFI entrypoint reached in debug mode.")
        logger.info(" Use debug -d uefi -i <options> for UEFI debugging")
        logger.info(" Example : debug -d uefi -i Sec")

    if (Module_Name is not None) and (g_dbgContext.uefiDbgContext['UEFI_Sec'] is None):
        update_core_power_state(debugger, command, result)
        validateFvHeader(debugger, UEFI_baseAddr)
        if os.path.exists(Sec_debug_path):
            Sec_dSYM_cmd = "{}.dSYM".format(Sec_debug_path)
            sec_module = g_dbgContext.tgt.AddModule(Sec_debug_path, None, None, Sec_dSYM_cmd)
        else:
            logger.error(" unable to find sec.debug")
            sys.exit(1)

        selTarget = debugger.GetSelectedTarget()
        SecLoadAddr = hex(int(UEFI_baseAddr, 16) + int(SecLoadAddrOffset, 16))
        g_dbgContext.tgt.SetModuleLoadAddress(sec_module, int(SecLoadAddr, 16))
        time.sleep(4)

        res = g_dbgContext.tgt.EvaluateExpression("&LoadDxeCoreFromFfsFile")
        res_str = str(res)

        try:
            r = res_str.find("=")
            if r != -1:
                hex_str = res_str[(r + 12):(r + 20)].strip()
                LoadDxeCore_addr = int(hex_str, 16)
            else:
                LoadDxeCore_addr = res.GetValueAsUnsigned()
        except (ValueError, IndexError):
            LoadDxeCore_addr = res.GetValueAsUnsigned()

        LoadDxeCore_addr = LoadDxeCore_addr - int("0x40", 16)
        LoadDxeCore_addr = hex(LoadDxeCore_addr).replace("L", "")

        debugger.HandleCommand("br del")
        debugger.HandleCommand("br s -n SecMain --hardware")
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")
        g_dbgContext.setDbgContextUEFI('UEFI_Sec', 1)

    if (Module_Name is not None) and (Module_Name.upper() != "SEC"):
        if g_dbgContext.uefiDbgContext['UEFI_Dxe'] is None:
            cmd = "br s -a {} --hardware".format(str(LoadDxeCore_addr))
            debugger.HandleCommand(cmd)
            debugger.HandleCommand("c")
            debugger.HandleCommand("br del")

        RamEntryAddr = UEFI_baseAddr
        infoBlkAddr = hex(int(RamEntryAddr, 16) + int(InfoBlkPtrOffset, 16))
        DrvrAddr = hex(int(infoBlkAddr, 16) + int(IBlkDbgStopDrvrStr, 16))
        BdsHotkeyAddr = hex(int(infoBlkAddr, 16) + int(IBlkBdsHotkey, 16))
        ForceDisableHWWdogAddr = hex(int(infoBlkAddr, 16) + int(IBlkForceDisableHWWdog, 16))

        Module_Name = Module_Name.upper()
        if Module_Name != "DXE":
            debug_dxe_core = False
        else:
            Module_Name = ""
            debug_dxe_core = True
        Module_len = len(Module_Name)

        cmd = "memory write -s {} -f s {} {}".format(Module_len, DrvrAddr, Module_Name)
        debugger.HandleCommand(cmd)

        cmd = "memory write -s 4 -f x {} 0x0".format(BdsHotkeyAddr)
        debugger.HandleCommand(cmd)

        cmd = "memory write -s 4 -f x {} 0x1".format(ForceDisableHWWdogAddr)
        debugger.HandleCommand(cmd)

        if debug_dxe_core:
            pass
        else:
            debugger.HandleCommand("br s -n BreakPointFunction -H")
            debugger.HandleCommand("c")

        ModName = hex(int(infoBlkAddr, 16) + int(IBlkModuleName, 16))
        cmd = "x/s {}".format(ModName)

        debugger.HandleCommand("br del")
        result_obj = lldb.SBCommandReturnObject()
        commandInterpreter = debugger.GetCommandInterpreter()
        commandInterpreter.HandleCommand(cmd, result_obj)
        res = str(result_obj.GetOutput())
        Module_Name_read = res.upper()
        Module_Name_read_start = Module_Name_read.index('"')
        Module_Name_read_end = Module_Name_read.rindex('"')
        Module_Name_read = Module_Name_read[(Module_Name_read_start + 1):
                                            Module_Name_read_end]

        # verifyInfoBlk(debugger, InfoBlkPtrOffset, UEFI_baseAddr)
        memoryDump(debugger, infoBlkAddr, iBlkTotalCmdSize, iBlkSymbolCmdString,
                   iBlkCmdStartOffset, debug_dxe_core)
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        file_path = os.path.join(script_dir, "Uefi_memory_dump.txt")

        file = open(file_path, "r")
        Source_Path = None
        try:
            while True:
                line = file.readline()
                if not line:
                    break
                line_num += 1

                if "translate" in line:
                    Path = line
                    sub_str = "uefi_images"
                    Start_index = Path.index("/")
                    End_index = Path.index(sub_str)
                    Source_Path = Path[Start_index:(End_index + len(sub_str))]

                if '&Sf="' in line:
                    Path = line
                    Index = Path.index("/")
                    Dxe_dll_Path = Path[Index:]
                    Dxe_dll_Path = Dxe_dll_Path.replace("\"", "").replace("\n", "")
                    Dxe_dll_Path_List.append(Dxe_dll_Path)
                    Index2 = Path.rindex("/")
                    Dxe_Name = Path[Index2:]
                    Dxe_Name = Dxe_Name.replace("/", "").replace('.debug"', '').replace("\n", "").upper()
                    Dxe_Name_List.append(Dxe_Name)

                if 'data.load.elf "&Sf"' in line:
                    slash_index = line.find('/')
                    hex_index = line.find('0x')
                    offset = line[hex_index:(slash_index - 1)]
                    offset_List.append(offset)
                    if Dxe_Name == Module_Name_read:
                        break
                if debug_dxe_core:
                    break
        except:
            logger.error("parsing Uefi_memory_dump.txt file failed")
        file.close()
        i = 0
        if debug_dxe_core:
            pass
        else:
            j = Dxe_Name_List.index(Module_Name_read)

        Dxe_dll_Path = "".join([CoreObj, Dxe_dll_Path_List[i]])
        if os.path.exists(Dxe_dll_Path):
            Dxe_dSYM_cmd = "{}.dSYM".format(Dxe_dll_Path)
            g_dbgContext.tgt.AddModule(Dxe_dll_Path, None, None, Dxe_dSYM_cmd)
        else:
            logger.error(" Unable to find DxeCore.debug file")
            sys.exit(1)

        module5 = int(selTarget.GetNumModules()) - 1
        module5 = selTarget.modules[module5]
        g_dbgContext.tgt.SetModuleLoadAddress(module5, int(offset_List[i], 16))
        time.sleep(4)

        if debug_dxe_core != True:
            Dxe_dll_Path = "".join([CoreObj, Dxe_dll_Path_List[j]])

            if os.path.exists(Dxe_dll_Path):
                Dxe_dSYM_cmd = "{}.dSYM".format(Dxe_dll_Path)
                g_dbgContext.tgt.AddModule(Dxe_dll_Path, None, None, Dxe_dSYM_cmd)
            else:
                logger.error("Unable to find %s", Module_Name_read)
                sys.exit(1)

            module6 = int(selTarget.GetNumModules()) - 1
            module6 = selTarget.modules[module6]
            g_dbgContext.tgt.SetModuleLoadAddress(module6, int(offset_List[j], 16))
            time.sleep(4)

        if Source_Path:
            uefi_image_path = os.path.join(uefi_image_path, "uefi_images")
            cmd = "settings append target.source-map {} {}".format(Source_Path, uefi_image_path)
            debugger.HandleCommand(cmd)

        if debug_dxe_core:
            debugger.HandleCommand("br s -n DxeMain -H")
            debugger.HandleCommand("c")
            logger.info(" --- FBC: Dxe entrypoint reached in debug mode")
            return

        module6 = str(module6)
        module6_name = os.path.basename(module6)

        bkpt = selTarget.BreakpointCreateByName('ProcessModuleEntryPointList', module6_name)
        debugger.HandleCommand("c")
        sec_module = str(sec_module)

        sec_module_name = os.path.basename(sec_module)

        bkpt = selTarget.BreakpointCreateByName('BreakPointFunction', sec_module_name)
        # os.remove("Uefi_memory_dump.txt")
        os.remove(file_path)

        cmd_list = lldb.SBStringList()
        cmd_list.AppendString("runtimedxe")
        bkpt.SetCommandLineCommands(cmd_list)

    if inputarg:
        return
    else:
        logger.info(" --- FBC: Dxe entrypoint reached in debug mode")


def runtimedxeUEFI(debugger, command, exe_ctx, result):
    """!
    Debug runtime DXE modules.

    This function continues UEFI debugging for runtime DXE modules.

    @param debugger         -   An instance of the SBDebugger Object
    @param command          -   String containing all input arguments
    @param exe_ctx          -   An execution context object
    @param result           -   A return object for command output
    """
    Chipset_name = g_dbgContext.chipsetName.lower()
    uefi_image_path = g_dbgContext.openocdConfig.getImageBasePath("uefi")

    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname = chip.get('name')
        if chipname.upper() == Chipset_name.upper():
            config = chip.find('configure_target')
            offset = chip.find('InitOffsets')
            file_info = chip.find('file_info')

    # if file_info:
    #     file_path = file_info.find('sec_dll_path').attrib['path']
    #     Sec_debug_path = "{}{}".format(uefi_image_path, file_path)
    # else:
    #     Sec_debug_path = g_dbgContext.openocdConfig.getBinaryPath('uefi', 'Sec.debug')

    CoreObjPath = config.find('CoreObj').attrib['path']
    CoreObj = "{}{}".format(uefi_image_path, CoreObjPath)

    UEFI_baseAddr = config.find('UefiRamEntryAddr').attrib['Addr']
    InfoBlkPtrOffset = config.find('InfoBlkPtrOffset').attrib['Addr']
    iBlkTotalCmdSize = offset.find('IBlkTotalCmdSize').attrib['Offset']
    iBlkSymbolCmdString = offset.find('IBlkSymbolCmdString').attrib['Offset']
    iBlkCmdStartOffset = offset.find('IBlkCmdStartOffset').attrib['Offset']
    IBlkModuleName = offset.find('IBlkModuleName').attrib['Offset']

    RamEntryAddr = UEFI_baseAddr
    infoBlkAddr = hex(int(RamEntryAddr, 16) + int(InfoBlkPtrOffset, 16))
    ModName = hex(int(infoBlkAddr, 16) + int(IBlkModuleName, 16))
    cmd = "x/s {}".format(ModName)

    result_obj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()
    commandInterpreter.HandleCommand(cmd, result_obj)
    res = str(result_obj.GetOutput())

    Module_Name_read = res.upper()
    Module_Name_read_start = Module_Name_read.index('"')
    Module_Name_read_end = Module_Name_read.rindex('"')
    Module_Name_read = Module_Name_read[(Module_Name_read_start + 1):
                                        Module_Name_read_end]

    # verifyInfoBlk(debugger, InfoBlkPtrOffset, UEFI_baseAddr)
    memoryDump(debugger, infoBlkAddr, iBlkTotalCmdSize, iBlkSymbolCmdString,
               iBlkCmdStartOffset, False)

    Dxe_Name = None
    line_num = 0
    Dxe_Name_List = []
    Dxe_dll_Path_List = []
    offset_List = []

    script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    file_path = os.path.join(script_dir, "Uefi_memory_dump.txt")

    file = open(file_path, "r")
    Source_Path = None
    try:
        while True:
            line = file.readline()
            if not line:
                break
            line_num += 1

            if "translate" in line:
                Path = line
                sub_str = "uefi_images"
                Start_index = Path.index("/")
                End_index = Path.index(sub_str)
                Source_Path = Path[Start_index:(End_index + len(sub_str))]

            if '&Sf="' in line:
                Path = line
                Index = Path.index("/")
                Dxe_dll_Path = Path[Index:]
                Dxe_dll_Path = Dxe_dll_Path.replace("\"", "").replace("\n", "")
                Dxe_dll_Path_List.append(Dxe_dll_Path)
                Index2 = Path.rindex("/")
                Dxe_Name = Path[Index2:]
                Dxe_Name = Dxe_Name.replace("/", "").replace('.debug"', '').replace("\n", "").upper()
                Dxe_Name_List.append(Dxe_Name)

            if 'data.load.elf "&Sf"' in line:
                slash_index = line.find('/')
                hex_index = line.find('0x')
                offset = line[hex_index:(slash_index - 1)]
                offset_List.append(offset)
                if Dxe_Name == Module_Name_read:
                    break
    except:
        logger.error("parsing Uefi_memory_dump.txt file failed")
    file.close()
    j = Dxe_Name_List.index(Module_Name_read)
    selTarget = debugger.GetSelectedTarget()
    Dxe_dll_Path = "".join([CoreObj, Dxe_dll_Path_List[j]])

    if os.path.exists(Dxe_dll_Path):
        Dxe_dSYM_cmd = "{}.dSYM".format(Dxe_dll_Path)
        g_dbgContext.tgt.AddModule(Dxe_dll_Path, None, None, Dxe_dSYM_cmd)
    else:
        logger.error("Unable to find %s", Module_Name_read)
        sys.exit(1)

    module8 = int(selTarget.GetNumModules()) - 1
    module8 = selTarget.modules[module8]
    g_dbgContext.tgt.SetModuleLoadAddress(module8, int(offset_List[j], 16))
    time.sleep(4)
    module8 = str(module8)
    module8_name = module8[module8.rindex("\\") + 1:]
    bkpt = selTarget.BreakpointCreateByName('ProcessModuleEntryPointList', module8_name)
    debugger.HandleCommand("c")
    os.remove("Uefi_memory_dump.txt")
