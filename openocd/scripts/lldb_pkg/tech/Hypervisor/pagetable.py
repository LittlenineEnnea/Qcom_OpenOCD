#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file pagetable.py
#
# @section Description
# This file contains script to dump the pagetable into a file.

import lldb

flag = {
    "perm":"",
    "gbl":"",
    "share":"",
    "cache":""
}
       
def memory_read(debugger,addr):
    """!
    This function is used to read value from address

     @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for 
                                debugging.
     @param addr             -   This is the given address which is 
                                used for Read.
     
    """
    process = debugger.GetSelectedTarget().GetProcess()
    error_ref = lldb.SBError()
    val = process.ReadMemory(addr, 8, error_ref)   
    val = hex(int.from_bytes(val, byteorder ='little'))
    return val


def dump_pagetable(debugger, command, exe_ctx, result, pt_base, file,stage):
    """!   
    This function is used to define a command to dump pagetable.                                                

    Usage : mmu_dump -d EL1 -p <dump_file_path>  

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
    @param pt_base          -   The base address of the pagetable.
    @param file             -   The file path where the output is stored.
    @param stage            -   The stage is represent the VA to IPA(stage 1) or 
                                IPA to PA(stage 2)


    """
    dump = open(file,'w')
    end_addr = pt_base + 0x1000
    descriptor_mask = 0x0FF8
    L1_descriptor_addr = pt_base
    upper_attr_mask = 0x70000000000000
    lower_attr_mask = 0xFFF 
    dump.write("|             Input Address            |            Output Address            |      Size     | Perm |  Global |  Shareability  | Cacheability |\n")
    dump.write("|--------------------------------------|--------------------------------------|---------------|------|---------|----------------|--------------|\n")
    while (pt_base != end_addr):
        L1_descriptor = memory_read( debugger , pt_base)
        L1_descriptor = int(L1_descriptor,16)
        save_va_s1 = pt_base  &  descriptor_mask
        save_va_s1 = save_va_s1 >> 3
        save_va_s1 = save_va_s1 << 30
        if (L1_descriptor !=  0x0):
        # valid entry, check if block or descriptor
            if ((L1_descriptor  &  0x2) != 0x2):
                    #its a 1GB block
                    s1_block = L1_descriptor  &  0xFFFFFFFFF000
                    upper_attr = L1_descriptor  &  upper_attr_mask
                    lower_attr = L1_descriptor & lower_attr_mask
                    if (stage  ==  0x0):
                        flag = calc_attrs_el2_stage1(upper_attr,lower_attr)
                    else:
                        flag = calc_attrs_stage2(upper_attr,lower_attr)
                    temp4 = save_va_s1
                    dump.write(f"| IPA:    0x{temp4:09X} -    0x{temp4 + (0x40000000-1):09X} | PA:     0x{s1_block:09X} -    0x{s1_block + (0x40000000-1):09X} |    0x40000000     |  {flag['perm']}  |  { flag['gbl']} |    { flag['share']}    | {flag['cache']} |\n")
            else: #its an L2 table descriptor
                L2_descriptor_addr = L1_descriptor & 0xFFFFFFFFF000
                L2_end_addr = L2_descriptor_addr + 0x1000
                while (L2_descriptor_addr != L2_end_addr):
                    L2_descriptor = memory_read( debugger ,L2_descriptor_addr)
                    L2_descriptor = int(L2_descriptor,16)
                    save_va_s2 = L2_descriptor_addr & descriptor_mask
                    save_va_s2 = save_va_s2  >>  3
                    save_va_s2 = save_va_s2 << 21
                    if (L2_descriptor != 0x0):
                        if ((L2_descriptor & 0x2) != 0x2):
                            #its a 2MB block
                            s2_block = L2_descriptor & 0xFFFFFFFFF000
                            upper_attr = L2_descriptor & upper_attr_mask
                            lower_attr = L2_descriptor & lower_attr_mask
                            if (stage == 0x0):
                                flag = calc_attrs_el2_stage1(upper_attr,lower_attr)
                            else:
                                flag = calc_attrs_stage2(upper_attr,lower_attr)
                            temp4 = (save_va_s1|save_va_s2)                         
                            dump.write(f"| IPA:    0x{temp4:09X} -    0x{temp4 + (0x200000-1):09X} | PA:     0x{s2_block:09X} -    0x{s2_block + (0x200000-1):09X} |    0x200000     |  {flag['perm']}  |  { flag['gbl']} |    { flag['share']}    | {flag['cache']} |\n")
                        else: #its a L3 descriptor
                            L3_descriptor_addr = L2_descriptor & 0xFFFFFFFFF000
                            L3_end_addr = L3_descriptor_addr + 0x1000
                            while (L3_descriptor_addr != L3_end_addr):
                                L3_descriptor = memory_read(  debugger ,L3_descriptor_addr)
                                L3_descriptor = int(L3_descriptor,16)
                                save_va_s3 = L3_descriptor_addr & descriptor_mask
                                if (L3_descriptor != 0x0):
                                    #4kb block
                                    s3_block = L3_descriptor & 0xFFFFFFFFF000
                                    upper_attr = L3_descriptor & upper_attr_mask
                                    lower_attr = L3_descriptor & lower_attr_mask
                                    if (stage == 0x0):
                                        flag = calc_attrs_el2_stage1(upper_attr, lower_attr)
                                    else:
                                        flag = calc_attrs_stage2(upper_attr, lower_attr)
                                    save_va_s3 = save_va_s3 >> 3
                                    save_va_s3 = save_va_s3 << 12
                                    temp4 = ((save_va_s1|save_va_s2)|save_va_s3)
                                    dump.write(f"| IPA:    0x{temp4:09X} -    0x{temp4 + (0x1000-1):09X} | PA:     0x{s3_block:09X} -    0x{s3_block + (0x1000-1):09X} |    0x1000     |  {flag['perm']}  |  { flag['gbl']} |    { flag['share']}    | {flag['cache']} |\n")
                                L3_descriptor_addr = L3_descriptor_addr + 0x8
                    L2_descriptor_addr = L2_descriptor_addr + 0x8
        pt_base = pt_base + 0x8
    dump.close()




def calc_attrs_el2_stage1(upper,lower):
    """!
    This function used to how memory attributes are specified in a stage 1 block entry
     @param  upper      -   This contain the Upper attributes
     @param  lower      -   This contain the lower attributes
    """
    upper = upper >> 52

     # Access permissions control whether a region is readable or writeable, or both
    if (((lower & 0xC0) == 0x80) and ((upper & 0x4) == 0x4)): 
         flag['perm'] = "RO"
    elif (((lower & 0xC0) == 0x80) and ((upper & 0x4) != 0x4)): 
         flag['perm'] = "RX"
    elif ((lower & 0xC0) == 0x00):
         flag['perm'] = "RW"
    else:
         flag['perm'] = "none"

    # This section used to find shareability memory
    if ((lower & 0x300) == 0x300):
         flag['share'] = "inner share"
    elif ((lower & 0x300) == 0x200):
         flag['share'] = "outer share"
    elif ((lower & 0x300) == 0x0):
         flag['share'] = "no share"
    else:
         flag['share'] = "N/A"

    # This section used to find global or non global ,means the region is available for all processes or process-specific
    if ((lower & 0x800) == 0x800):
         flag['gbl'] = "not glb"
    else :
         flag['gbl'] = "global"

    # Cacheability attributes for memory
    lower_temp2 = lower >> 2
    if ((lower_temp2 & 0xF) == 0x6):
         flag['cache'] = "Device nGnRE"           # Device non-Gathering, non-Reordering, Early Write Acknowledgement.
    elif ((lower_temp2 & 0xF) == 0x1):
         flag['cache'] = "Outer WB Inner WB"      # WB - Write-Back Cacheable
    elif ((lower_temp2 & 0xF) == 0x3):
         flag['cache'] = "Outer WT Inner WT"      # WT - Write-Through Cacheable
    elif ((lower_temp2 & 0xF) == 0x0):
         flag['cache'] = "Outer NC Inner NC"      # NC - Non-cacheable
    else:
         flag['cache'] = "N/A"
    return flag


def calc_attrs_stage2(upper,lower):
    """!
    This function used to how memory attributes are specified in a stage 2 block entry
     @param  upper      -   This contain the Upper attributes
     @param  lower      -   This contain the lower attributes
    """
    upper = upper >> 52

    # Access permissions control whether a region is readable or writeable, or both
    if (((lower & 0xC0) == 0x40) and ((upper & 0x4) == 0x4)): 
         flag['perm'] = "RO"
    elif (((lower & 0xC0) == 0x40) and ((upper & 0x4) != 0x4)): 
         flag['perm'] = "RX"
    elif ((lower & 0xC0) == 0x80):
         flag['perm'] = "WO"
    elif (((lower & 0xC0) == 0xC0) and ((upper & 0x4) == 0x4)):
         flag['perm'] = "RW"
    elif (((lower & 0xC0) == 0xC0) and ((upper & 0x4) != 0x4)): 
         flag['perm'] = "RWX"
    else:
         flag['perm'] = "N/A"

    # This section used to find shareability memory
    if ((lower & 0x300) == 0x300):
         flag['share'] = "inner share"
    elif ((lower & 0x300) == 0x200):
         flag['share'] = "outer share"
    elif ((lower & 0x300) == 0x000):
         flag['share'] = "no share"
    else:
         flag['share'] = "N/A"

    # This section used to find global or non global ,meaning the region is available for all processes or process-specific
    if ((lower & 0x800) == 0x800):
         flag['gbl'] = "not glb"
    else :
         flag['gbl'] = "global"

    # Cacheability attributes for memory
    lower_temp = lower >> 2
    if ((lower_temp & 0xF) == 0x0):
         flag['cache'] = "Device nGnRnE"               # Device non-Gathering, non-Reordering, No Early Write Acknowledgement
    elif ((lower_temp & 0xF) == 0x1):
         flag['cache'] = "Device nGnRE"                # Device non-Gathering, non-Reordering, Early Write Acknowledgement.
    elif ((lower_temp & 0xF) == 0x2):
         flag['cache'] = "Device nGRE"                 # Device non-Gathering, Reordering, Early Write Acknowledgement.
    elif ((lower_temp & 0xF) == 0x3):
         flag['cache'] = "Device GRE"                  # Device Gathering, Reordering, Early Write Acknowledgement.
    elif ((lower_temp & 0xF) == 0x5):
         flag['cache'] = "uncached"
    elif ((lower_temp & 0xF) == 0x6):
         flag['cache'] = "Outer uncached Inner WT"     # WT - Write-Through Cacheable
    elif ((lower_temp & 0xF) == 0x7):
         flag['cache'] = "Outer uncached Inner WB"     # WB - Write-Back Cacheable
    elif ((lower_temp & 0xF) == 0x9):
         flag['cache'] = "Outer WT Inner uncached"
    elif ((lower_temp & 0xF) == 0xA):
         flag['cache'] = "Outer WT Inner WT"
    elif ((lower_temp & 0xF) == 0xB):
         flag['cache'] = "Outer WT Inner WB"
    elif ((lower_temp & 0xF) == 0xD):
         flag['cache'] = "Outer WB Inner uncached"
    elif ((lower_temp & 0xF) == 0xE):
         flag['cache'] = "Outer WB Inner WT"
    elif ((lower_temp & 0xF) == 0xF):
         flag['cache'] = "Outer WB Inner WB"
    else:
         flag['cache'] = "N/A"
    return flag
