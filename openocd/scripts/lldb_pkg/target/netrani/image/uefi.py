#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file uefi.py
#
# @section Description
# This file contains a script for UEFI image debugging.

import os
import sys
import time
import lldb
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from lldb_pkg.target.netrani.image.hyp import debugHYP


def validateFvHeader(debugger, addr):
    """!   
    This function is used to check FV header address and  to verify if the 
    signature proper or not.

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
    @param addr             -   This is the given FV header address which is 
                                used for validating signature.

    """
    returnObj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()    
    baseAddr = addr
    sigAddr = hex(int(baseAddr, 16) + int("0x28", 16))
    cmd = "x/wx {}".format(sigAddr)
    commandInterpreter.HandleCommand( cmd, returnObj )
    res = str(returnObj.GetOutput())
    sig = repr(res[12:22]).replace("'","")
    if (int(sig, 16) == int("0x4856465F", 16)):
        return
    else:
        logger.error(" Invalid FV header, exiting...")
        sys.exit(1)


def verifyInfoBlk(debugger, addr, baseAddr):
    """!   
    This function is used to check the infoBlkAddr and to verify if the 
    signature proper or not.

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
    @param addr             -   This is the given info block pointer offset
                                address which is used for validating signature.
    @param baseAddr         -   This is the given UEFI Base Address.

    """
    returnObj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()
    infoBlkPtrOffsetAddr = str(addr)
    addr = hex(int(baseAddr, 16) + int(infoBlkPtrOffsetAddr, 16))
    cmd = "x/wx {}".format(addr)
    commandInterpreter.HandleCommand( cmd, returnObj )
    res = str(returnObj.GetOutput())
    sig = repr(res[12:22]).replace("'","")
    # checking if signature is correct
    if (int(sig, 16) == int("0x6B6C4249", 16)):
        return
    else:
        logger.error(" UefiInfoBlk not found, exiting...")
        sys.exit(1)


def memoryDump(debugger, infoBlkAddr, iBlkTotalCmdSize, iBlkSymbolCmdString, 
               iBlkCmdStartOffset, debug_dxe_core):
    """!   
    This function dumps memory range where we find relocated address based on elf
    information - gives us list of dxe dlls and corresponding offsets.

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
                               
    """
    
    returnObj = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()
    cmd = "x/wx {}".format( hex((int(infoBlkAddr, 16) + int(iBlkSymbolCmdString, 16))))
    commandInterpreter.HandleCommand( cmd, returnObj )
    res = str(returnObj.GetOutput())
    cmdAddr = repr(res[12:22]).replace("'","")

    cmd = "x/wx {}".format(hex((int(infoBlkAddr, 16) + int(iBlkTotalCmdSize, 16))))
    commandInterpreter.HandleCommand( cmd, returnObj )
    res = str(returnObj.GetOutput())
    cmdSize = repr(res[12:22]).replace("'","")
    CmdEnd = hex(int(cmdAddr, 16) + int(cmdSize, 16) - 
    int("0x1", 16)).replace("L", "")
    
    cmd = "x/wx {}".format(hex((int(infoBlkAddr, 16) + int(iBlkCmdStartOffset, 16))))
    commandInterpreter.HandleCommand( cmd, returnObj )
    res = str(returnObj.GetOutput())
    cmdincroffset = repr(res[12:22]).replace("'","")
    cmdincroffset = hex(int(cmdincroffset, 16) + int(cmdAddr, 16)).replace("L", "")
    
    cmd = "memory read --outfile Uefi_memory_dump.txt --force --binary {} {}".format(
        str(cmdincroffset), str(CmdEnd))

    debugger.HandleCommand(cmd)
    if debug_dxe_core:
        addr = hex((int(infoBlkAddr, 16) + int(iBlkCmdStartOffset, 16)))
        cmd = "memory write -s 4 -f x {} {}".format(addr, cmdSize)
        commandInterpreter.HandleCommand( cmd, returnObj )
        res = str(returnObj.GetOutput())
    return  


def debugUEFI(debugger, command, exe_ctx, result, inputarg = None, ModuleName = None):
    """!   
    This function is used to start the debug by halting at boot_cold_init
    via the same steps as hyp debug. This function helps to debug uefi, 
    dxe dll is then loaded and this helps halt at "ProcessModuleEntryPointList"
    of the dll.
                                              
    Usage : debug -d uefi [-i <module_name>]

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for 
                                debugging.
    @param command          -   String containing all the input arguments
                                for the used command. 
    @param exe_ctx          -   An execution context object carrying 
                                information of the process 
                                context on which the command is used.
    @param result           -   A return object which encapsulates 
                                success/failure information for the command
                                and output text that needs to be printed 
                                as a result of the command.

    @param ModuleName       -   Input module name used for debug after 
                                halting at UEFI entrypoint

    """    

    Dxe_Name = None
    line_num = 0
    Dxe_Name_List = []
    Dxe_dll_Path_List = []
    offset_List = []

    if (inputarg == None and ModuleName == None):
        logger.info (" --- Preparing for Debug of UEFI Image ---")

    debugger.HandleCommand("settings set auto-confirm on")
    Module_Name = ModuleName
    Chipset_name = g_dbgContext.chipsetName.lower()

    # Fetching the InitOffsets
    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname=chip.get('name')
        if (chipname.upper() == Chipset_name.upper()):
            config = chip.find('configure_target')
            offset = chip.find('InitOffsets')
            file_info = chip.find('file_info')
            
    # Fetched all the required InitOffsets
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
    boot_image_path = g_dbgContext.openocdConfig.getImageBasePath ("boot")
    boot_image_path = boot_image_path.replace("\n","")
    chipset = g_dbgContext.chipsetName.upper()
    selTarget = debugger.GetSelectedTarget()
    
    if (file_info):
        file_path = file_info.find('sec_dll_path').attrib['path']
        Sec_elf_path = "{}{}".format(boot_image_path, file_path)
    else:
        Sec_elf_path = g_dbgContext.openocdConfig.getBinaryPath ('boot','Sec.dll')

    CoreObjPath = config.find('CoreObj').attrib['path']
    CoreObj = os.path.join(boot_image_path, CoreObjPath)

    if (Module_Name == None):
        debugHYP(debugger, command, exe_ctx, result, "run")
        debugger.HandleCommand("br del")

        cmd = "br s -a {} --hardware".format(UEFI_baseAddr)
        debugger.HandleCommand(cmd)
        debugger.HandleCommand("c")
        logger.info (" FBC: UEFI entrypoint reached in debug mode.")
        logger.info (" Use debug -d uefi -i <options> for UEFI debugging")
        logger.info (" Example : debug -d uefi -i Sec")
    
    # debug -d uefi -i Sec will run this if condition to reach the entrypoint for Sec Module
    if ((Module_Name != None) and (g_dbgContext.uefiDbgContext['UEFI_Sec'] == None)):
        validateFvHeader(debugger, UEFI_baseAddr)
        if(os.path.exists(Sec_elf_path)):
            Sec_dSYM_cmd = "{}.dSYM".format(Sec_elf_path)
            sec_module = g_dbgContext.tgt.AddModule (Sec_elf_path, None, None, Sec_dSYM_cmd)
        else:
            logger.error (" unable to find sec.dll")
            sys.exit(1)
        
        selTarget = debugger.GetSelectedTarget()
        SecLoadAddr = hex(int(UEFI_baseAddr, 16) + int(SecLoadAddrOffset, 16))
        g_dbgContext.tgt.SetModuleLoadAddress (sec_module, int(SecLoadAddr, 16))
        time.sleep(4)
        res = g_dbgContext.tgt.EvaluateExpression("&LoadDxeCoreFromFfsFile")
        res = str(res)
        r = res.find("=")
        res = res[(r+12):(r+20)]
        LoadDxeCore_addr = "0x{}".format(res)
        LoadDxeCore_addr = int(LoadDxeCore_addr, 16) - int("0x40", 16)
        LoadDxeCore_addr = hex(LoadDxeCore_addr).replace("L","")
        debugger.HandleCommand("br del")
        debugger.HandleCommand("br s -n Main --hardware")
        debugger.HandleCommand("c")
        debugger.HandleCommand("br del")
        g_dbgContext.setDbgContextUEFI('UEFI_Sec', 1)
        
    if ((Module_Name != None) and (Module_Name.upper() != "SEC")):
        if (g_dbgContext.uefiDbgContext['UEFI_Dxe'] == None):
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
        result = lldb.SBCommandReturnObject()
        commandInterpreter = debugger.GetCommandInterpreter()
        commandInterpreter.HandleCommand( cmd, result )
        res = str(result.GetOutput())
        Module_Name_read = res.upper()
        Module_Name_read_start = Module_Name_read.index('"')
        Module_Name_read_end = Module_Name_read.rindex('"')
        Module_Name_read = Module_Name_read[(Module_Name_read_start + 1):
                                            Module_Name_read_end]
        verifyInfoBlk(debugger, InfoBlkPtrOffset, UEFI_baseAddr)
        memoryDump(debugger, infoBlkAddr, iBlkTotalCmdSize, iBlkSymbolCmdString, 
                   iBlkCmdStartOffset, debug_dxe_core)

        file = open("Uefi_memory_dump.txt", "r")
        Source_Path = None
        while file:
            line = file.readline()
            line_num = line_num + 1
            if "translate" in line:
                Path = line
                sub_str = "boot_images"
                Start_index = Path.index("/")
                End_index = Path.index(sub_str)
                Source_Path = Path[Start_index:(End_index + len(sub_str))]
            if '&Sf="' in line: 
                Path = line
                Index = Path.index("/")
                Dxe_dll_Path = Path[Index:]
                Dxe_dll_Path = Dxe_dll_Path.replace("\"","").replace("\n","")
                Dxe_dll_Path_List.append(Dxe_dll_Path)
                Index2 = Path.rindex("/")
                Dxe_Name = Path[Index2:]
                Dxe_Name = Dxe_Name.replace("/","").replace('.dll"', '').\
                    replace("\n","").upper()
                Dxe_Name_List.append(Dxe_Name) 
            if 'data.load.elf "&Sf"' in line:
                slash_index = line.find('/')
                hex_index = line.find('0x')
                offset = line[hex_index:(slash_index - 1)]
                offset_List.append(offset)
                if debug_dxe_core:
                    break
                if (Dxe_Name == Module_Name_read):
                    break
            else:
                continue
        file.close()

        i = 0
        if debug_dxe_core:
            pass
        else:
            j = Dxe_Name_List.index(Module_Name_read)

        Dxe_dll_Path = "".join ([ CoreObj , Dxe_dll_Path_List[i]])
        if (os.path.exists(Dxe_dll_Path)):
            Dxe_dSYM_cmd = "{}.dSYM".format(Dxe_dll_Path)
            g_dbgContext.tgt.AddModule (Dxe_dll_Path, None, None, Dxe_dSYM_cmd)
        else:
            logger.error (" Unable to find DxeCore.dll")
            sys.exit(1)

        module5 = int(selTarget.GetNumModules()) - 1
        module5 = selTarget.modules[module5]
        g_dbgContext.tgt.SetModuleLoadAddress (module5, int(offset_List[i], 16))
        time.sleep(4)
        if debug_dxe_core != True:
            Dxe_dll_Path = "".join ([ CoreObj , Dxe_dll_Path_List[j]])
            
            if (os.path.exists(Dxe_dll_Path)):
                Dxe_dSYM_cmd = "{}.dSYM".format(Dxe_dll_Path)
                g_dbgContext.tgt.AddModule (Dxe_dll_Path, None, None, Dxe_dSYM_cmd)
            else:
                logger.error ("Unable to find %s", Module_Name_read)
                sys.exit(1)

            module6 = int(selTarget.GetNumModules()) - 1
            module6 = selTarget.modules[module6]
            g_dbgContext.tgt.SetModuleLoadAddress (module6, int(offset_List[j], 16))
            time.sleep(4)
        if Source_Path:
            boot_image_path = os.path.join(boot_image_path, f"boot_images")            
            cmd = "settings append target.source-map {} {}".format(Source_Path,\
                                                                       boot_image_path)
            debugger.HandleCommand(cmd)
        debugger.HandleCommand(cmd)
        if debug_dxe_core:
            debugger.HandleCommand("br s -n DxeMain -H")
            debugger.HandleCommand("c")
            logger.info (" --- FBC: Dxe entrypoint reached in debug mode")
            return

        module6 = str(module6)

        module6_name = os.path.basename(module6)

        bkpt = selTarget.BreakpointCreateByName('ProcessModuleEntryPointList', \
                                                module6_name)
        debugger.HandleCommand("c")
        sec_module = str(sec_module)
        
        sec_module_name = os.path.basename(sec_module)
        
        bkpt = selTarget.BreakpointCreateByName('BreakPointFunction', sec_module_name)
        os.remove("Uefi_memory_dump.txt")

        cmd_list = lldb.SBStringList()
        cmd_list.AppendString("runtimedxe")
        bkpt.SetCommandLineCommands(cmd_list)

    if (inputarg):
        # if called from inside a script/another function return
        return 
    else:
        logger.info (" --- FBC: Dxe entrypoint reached in debug mode")

def runtimedxeUEFI(debugger, command, exe_ctx, result):
    """!   
    This function helps to debug runtimedxe  dll. In this function,
    runtimedxe dll is loaded and this helps halt at 
    "ProcessModuleEntryPointList"    
                                              
    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for 
                                debugging.
    @param command          -   String containing all the input arguments
                                for the used command. 
    @param exe_ctx          -   An execution context object carrying 
                                information of the process 
                                context on which the command is used.
    @param result           -   A return object which encapsulates 
                                success/failure information for the command
                                and output text that needs to be printed 
                                as a result of the command.

    """   
    Chipset_name = g_dbgContext.chipsetName.lower()
    boot_image_path = g_dbgContext.openocdConfig.getImageBasePath ("boot")
    
    for chip in g_dbgContext.chipInfo.findall('chipset'):
        chipname=chip.get('name')
        if (chipname.upper() == Chipset_name.upper()):
            config = chip.find('configure_target')
            offset = chip.find('InitOffsets')
            file_info = chip.find('file_info')
    
    if (file_info):
        file_path = file_info.find('sec_dll_path').attrib['path']
        Sec_elf_path = "{}{}".format(boot_image_path, file_path)

    else:
        Sec_elf_path = g_dbgContext.openocdConfig.getBinaryPath ('boot','Sec.dll')

    CoreObjPath = config.find('CoreObj').attrib['path']
    CoreObj = "{}{}".format(boot_image_path, CoreObjPath)

    # Fetching the InitOffsets  
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

    result = lldb.SBCommandReturnObject()
    commandInterpreter = debugger.GetCommandInterpreter()
    commandInterpreter.HandleCommand( cmd, result )
    res = str(result.GetOutput())

    Module_Name_read = res.upper()
    Module_Name_read_start = Module_Name_read.index('"')
    Module_Name_read_end = Module_Name_read.rindex('"')
    Module_Name_read = Module_Name_read[(Module_Name_read_start + 1) : \
                                        Module_Name_read_end]
    
    verifyInfoBlk(debugger, InfoBlkPtrOffset, UEFI_baseAddr)
    memoryDump(debugger, infoBlkAddr, iBlkTotalCmdSize, iBlkSymbolCmdString, \
               iBlkCmdStartOffset)
    Dxe_Name = None
    line_num = 0
    Dxe_Name_List = []
    Dxe_dll_Path_List = []
    offset_List = []
    file = open("Uefi_memory_dump.txt", "r")
    while file:
        line = file.readline()
        line_num = line_num + 1
        if "translate" in line:
            Path = line
            sub_str = "boot_images"
            Start_index = Path.index("/")
            End_index = Path.index(sub_str)
            Source_Path = Path[Start_index:(End_index + len(sub_str))]
        if '&Sf="' in line:   
            Path = line
            Index = Path.index("/")
            Dxe_dll_Path = Path[Index:]
            Dxe_dll_Path = Dxe_dll_Path.replace("\"","").replace("\n","")
            Dxe_dll_Path_List.append(Dxe_dll_Path)
            Index2 = Path.rindex("/")
            Dxe_Name = Path[Index2:]
            Dxe_Name = Dxe_Name.replace("/","").replace('.dll"', '').replace\
                ("\n","").upper()
            Dxe_Name_List.append(Dxe_Name) 
        if 'data.load.elf "&Sf"' in line:
            slash_index = line.find('/')
            hex_index = line.find('0x')
            offset = line[hex_index:(slash_index - 1)]
            offset_List.append(offset)
            if (Dxe_Name == Module_Name_read):
                break
        else:
            continue
    file.close()
    j = Dxe_Name_List.index(Module_Name_read)
    selTarget = debugger.GetSelectedTarget()
    Dxe_dll_Path = "". join ([CoreObj, Dxe_dll_Path_List[j]])

    if (os.path.exists(Dxe_dll_Path)):
        Dxe_dSYM_cmd = "{}.dSYM".format(Dxe_dll_Path)
        g_dbgContext.tgt.AddModule (Dxe_dll_Path, None, None, Dxe_dSYM_cmd)
    else:
        logger.error ("Unable to find %s", Module_Name_read)
        sys.exit(1)
    module8 = int(selTarget.GetNumModules()) - 1
    module8 = selTarget.modules[module8]
    g_dbgContext.tgt.SetModuleLoadAddress (module8, int(offset_List[j], 16))
    time.sleep(4)
    module8 = str(module8)
    module8_name = module8[module8.rindex("\\")+1:]
    bkpt = selTarget.BreakpointCreateByName('ProcessModuleEntryPointList', \
                                            module8_name)
    debugger.HandleCommand("c")
    os.remove("Uefi_memory_dump.txt")
