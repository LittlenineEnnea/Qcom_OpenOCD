#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file spmi_cfg.py
#
# @section Description
# This script contains functions to configure SPMI and perform various 
# initialization tasks specific to Bonito target.

from lldb_pkg.target.bonito.pmic.spmi.spmi_core_cfg import *
from utils.debug_context import g_dbgContext
from utils.debug_context import logger
from utils.telnet import *

def spmi_config(debugger, command, exe_ctx, result): 
    """
    This function configures the SPMI core and initializes various components.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context for the command.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """


    spmi_core_cfg(debugger, command, exe_ctx, result)
    init_priorities(debugger, command, exe_ctx, result )
    # init_pvc(debugger, command, exe_ctx, result)
    # init_mgpi(debugger, command, exe_ctx, result)
    # init_vioctl(debugger, command, exe_ctx, result)  Commented out for Bonito
    init_vrm(debugger, command, exe_ctx, result)

    channel_text_file_path = g_spmi_cfg.derive_file_path('channels.txt')

    logger.info("Programming channels...")

    with open(channel_text_file_path, 'r') as file:
        for line in file:
            if not line.strip():  # Check for end of file or empty line
                return
            if not line.startswith('Channel'):
                parts = line.split()
                chan = int(parts[0].rstrip('.'))
                owner = int(parts[1].rstrip('.'), 16)
                sid = int(parts[2], 16)
                periph = int(parts[3], 16)
                irq_own = int(parts[4].rstrip('.'), 16)
                irq_own_mask=0x1000000

                if (irq_own== 0):
                    irq_own_mask=0

                if (chan!=""):
                    tmp=((sid<<16)|(periph<<8)|(irq_own_mask))
                    g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_ARB_REG_ADDR0_ADDR', tmp, 4*chan)

                    tmp = owner
                    g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_SPMI_PERIPH0_2OWNER_TABLE_REG_ADDR', tmp, 4*chan)

    init_channel(debugger, command, exe_ctx, result)
    status_check(debugger, command, exe_ctx, result)

def init_priorities(debugger, command, exe_ctx, result):
    """
    Initializes the priorities for SPMI channels.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context for the command.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """
    for i in range(int(g_spmi_cfg.num_priorities)):
        g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_ARB_PRIORITIES0_ADDR', i, 4*i)
    return

def init_pvc(debugger, command, exe_ctx, result):
    """
    Initializes the PVC ports.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context for the command.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """
    pvc_text_file_path = g_spmi_cfg.derive_file_path('pvc.txt')

    # Enable global PVC ports
    g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_ARB_PVC_INTF_CTL_ADDR', 0x01)

    for i in range(int(g_spmi_cfg.num_pvc_ports)):
        g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_ARB_PVC_PORT0_CTL_ADDR', 0x0, 4*i)

    with open(pvc_text_file_path, 'r') as file:
        for line in file:
            if not line.strip():  # Check for end of file or empty line
                return
            if not line.startswith('Port'):
                set_pvc_table(debugger, command, exe_ctx, result, line, g_spmi_cfg.num_mgpi_ports)

    logger.info("Programming PVC Ports... done")


def init_mgpi(debugger, command, exe_ctx, result):

    """
    Initializes the MGPI ports.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context for the command.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """
    mgpi_text_file_path = g_spmi_cfg.derive_file_path('mgpi.txt')

    for i in range(g_spmi_cfg.num_mgpi_ports):
        if (i==1):
            return
        g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_MGPI_TRIG_CONFIG0_ADDR', 0x0, (0x8*i))

    with open(mgpi_text_file_path, 'r') as file:
        for line in file:
            if not line.strip():  # Check for end of file or empty line
                return
            if not line.startswith('Port'):
                set_mgpi_table(debugger, command, exe_ctx, result, line)

    g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_MGPI_MASTER_CSR_ADDR', 0x1)


    return

def init_vioctl(debugger, command, exe_ctx, result):
    """
    Initializes the VIOCTL ports.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context for the command.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """
    vioctl_text_file_path = g_spmi_cfg.derive_file_path('vioctl.txt')
    logger.info("Programming VIOCTL Ports. . .")

    with open(vioctl_text_file_path, 'r') as file:
        for line in file:
            if not line.strip():  # Check for end of file or empty line
                return
            if not line.startswith('vioctl'):
                set_vioctl_table(debugger, command, exe_ctx, result, line)
    
    logger.info("Done")

    return

def init_vrm(debugger, command, exe_ctx, result):
    """
    Initializes the VRM ports.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context for the command.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """
    vrm_text_file_path = g_spmi_cfg.derive_file_path('vrm.txt')
    logger.info("Programming VRM Ports")

    g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_ARB_VRM_S_PORT_CTL_ADDR', 0x03)
    g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_ARB_VRM_S_INTF_CTL_ADDR', 0x01)

    with open(vrm_text_file_path, 'r') as file:
        for line in file:
            if not line.strip():  # Check for end of file or empty line
                return
            if not line.startswith('Channel'):
                parts = line.split()
                '''
                    Channel  SlaveId  Periph
                    0.       0x0      0x50
                    1.       0x0      0x51
                    2.       0x0      0x54

                '''
                vrm_id = int(parts[0].rstrip('.'))
                vrm_sid = int(parts[1], 16)
                vrm_pid = int(parts[2], 16)

                tmp=(vrm_sid<<16)|(vrm_pid<<8)
                g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_ARB_VRM_S_ADDR0_ADDR', tmp, 4*vrm_id )

    logger.info("Done")

    return

def init_channel(debugger, command, exe_ctx, result):
    """
    Initializes the SPMI channels.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context for the command.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """
    logger.info ("Channel | Owner | SID | Periph          Channel | Owner | SID | Periph")
    logger.info("------------------------------          ------------------------------")
    for i in range(int(g_spmi_cfg.channel_count/2)):
        channel = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_PMIC_ARB_REG_ADDR0_ADDR', (4*i) )
        owner = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_SPMI_PERIPH0_2OWNER_TABLE_REG_ADDR', (4*i) )
        owner  = owner & 0x7

        line1 =f"{i} | {owner&0x7} | {(channel>>16)&0xF} | {hex((channel>>8)&0xFF)}"

        channel = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_PMIC_ARB_REG_ADDR0_ADDR', int((4*i)+(int(g_spmi_cfg.channel_count/2)*4)) )
        owner = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_SPMI_PERIPH0_2OWNER_TABLE_REG_ADDR', int((4*i)+(int(g_spmi_cfg.channel_count/2)*4)) )
        owner  = owner & 0x7

        logger.info(f"{line1} \t\t {int(i + g_spmi_cfg.channel_count/2)} | {owner&0x7} | {(channel>>16)&0xF} | {hex((channel>>8)&0xFF)}")

    return


def set_vioctl_table(debugger, command, exe_ctx, result, vioctl_text_input):
    """
    Configures the VIOCTL table based on the provided input.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context for the command.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    @param vioctl_text_input - String containing the VIOCTL configuration data.
    """
    parts = vioctl_text_input.split()

    temp = parts[0]
    sid = int(parts[1],16)
    pid = int(parts[2], 16)
    mask = int(parts[3], 16)

    # enable VIOCTL
    cfg_data=((mask<<24)|(0x1<<23)|(sid<<16)|(pid<<8)|(mask))

    if (temp=="N") :
        g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_SPMI_PIC_RCS_VIOCTL_N_0_ADDR', cfg_data)
        g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_SPMI_PIC_RCS_VIOCTL_N_1_ADDR', cfg_data)
    else :
        g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_SPMI_PIC_RCS_VIOCTL_0_ADDR', cfg_data)
        g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_SPMI_PIC_RCS_VIOCTL_1_ADDR', cfg_data)
    
    return
    
def set_mgpi_table (debugger, command, exe_ctx, result, mgpi_text_input):
    """
    Configures the MGPI table based on the provided input.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context for the command.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    @param mgpi_text_input     - String containing the MGPI configuration data.
    """
    parts = mgpi_text_input.split()
    port = int(parts[0].rstrip('.'))
    pvc_port = int(parts[1].rstrip('.'), 16)
    edge = parts[2]
    pos_addr_idx = int(parts[3].rstrip('.'), 16)
    pos_data = int(parts[4].rstrip('.'), 16)
    neg_addr_idx = int(parts[5].rstrip('.'), 16)
    neg_data = int(parts[6].rstrip('.'), 16)
    mux_sel = int(parts[7].rstrip('.'), 16)

    
    # enable MGPI and data
    cfg_data = ((neg_addr_idx<<24)|(neg_data<<16)|(pos_addr_idx<<8)|(pos_data))
    g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_MGPI_TRIG_CONFIG0_ADDR', cfg_data, (0x8*port))
    # data.set &ACCESS_CLASS:(HWIO_PMIC_MGPI_TRIG_CONFIG0_ADDR+(0x8*(&port))) %LONG &cfg_data

    # enable MGPI positve or negative or dual edge
    cfg_data = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_PMIC_MGPI_TRIG_CONFIG_EN0_ADDR',(0x8*port) )

    if (edge == 'pos'):
        cfg_data=((cfg_data & 0x1F00)|0x1)
    if (edge == 'neg'):
        cfg_data=((cfg_data & 0x1F00)|0x2)
    if (edge == 'dual'):
        cfg_data=((cfg_data & 0x1F00)|0x3)

    if ((mux_sel != "") and (mux_sel !="NA")):
        g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_MGPI_MUX_SEL0_ADDR', mux_sel, (0x10000*port))

    g_hwioConfig.hwio_write( debugger, command, result, 'HWIO_PMIC_MGPI_TRIG_CONFIG_EN0_ADDR', cfg_data, (0x8*port))

def set_pvc_table(debugger, command, exe_ctx, result, pvc_text_input, num_mgpi_ports):
    """
    Configures the PVC table based on the provided input.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context for the command.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    @param pvc_text_input   - String containing the PVC configuration data.
    @param num_mgpi_ports   - Number of MGPI ports to be configured.
    """
    parts = pvc_text_input.split()

    port = int(parts[0].rstrip('.'))
    t_values = [hex(int(t, 16)) for t in parts[1:]]

    g_hwioConfig.hwio_write(debugger, command, result, 'HWIO_PMIC_ARB_PVC_PORT0_CTL_ADDR', 0x01, (0x4*port))
    g_hwioConfig.hwio_write(debugger, command, result, 'HWIO_PMIC_ARB_PVC0_ADDR0_ADDR', int(t_values[0],16), (0x20*port))
    g_hwioConfig.hwio_write(debugger, command, result, 'HWIO_PMIC_ARB_PVC0_ADDR1_ADDR', int(t_values[1],16), (0x20*port))
    g_hwioConfig.hwio_write(debugger, command, result, 'HWIO_PMIC_ARB_PVC0_ADDR2_ADDR', int(t_values[2],16), (0x20*port))
    g_hwioConfig.hwio_write(debugger, command, result, 'HWIO_PMIC_ARB_PVC0_ADDR3_ADDR', int(t_values[3],16), (0x20*port))

    if num_mgpi_ports > 4:
        g_hwioConfig.hwio_write(debugger, command, result, 'HWIO_PMIC_ARB_PVC0_ADDR4_ADDR', int(t_values[4],16), (0x20*port))
        g_hwioConfig.hwio_write(debugger, command, result, 'HWIO_PMIC_ARB_PVC0_ADDR5_ADDR', int(t_values[5],16), (0x20*port))
        g_hwioConfig.hwio_write(debugger, command, result, 'HWIO_PMIC_ARB_PVC0_ADDR6_ADDR', int(t_values[6],16), (0x20*port))
        g_hwioConfig.hwio_write(debugger, command, result, 'HWIO_PMIC_ARB_PVC0_ADDR7_ADDR', int(t_values[7],16), (0x20*port))

def status_check(debugger, command, exe_ctx, result):
    """
    Checks and logs the status of various SPMI configurations.

    @param debugger         - An instance of the SBDebugger Object that 
                              provides access to SBTargets used for debugging.
    @param command          - String containing all the input arguments
                              for the used command. 
    @param exe_ctx          - Execution context for the command.
    @param result           - A return object which encapsulates 
                              success/failure information for the command
                              and output text that needs to be printed 
                              as a result of the command.
    """
    logger.info("*** SPMI CONFIG STATUS ***")

    tmp = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_SPMI_GENI_CTRL_ADDR')
    if (tmp == 0):
        res = "Enabled"
    else:
        res = "Disabled"

    logger.info(f"SPMI Master: = {res} \t")

    tmp = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_PMIC_ARB_PVC_INTF_CTL_ADDR')
    if (tmp == 1):
        res = "Enabled"
    else:
        res = "Disabled"

    logger.info(f"PVC ports: = {res} \t")

    tmp = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_PMIC_ARB_VRM_S_INTF_CTL_ADDR')
    if (tmp == 1):
        res = "Enabled"
    else:
        res = "Disabled"
    logger.info(f"VRM ports: = {res} \t")


    tmp = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_PMIC_MGPI_MASTER_CSR_ADDR')
    if ((tmp & 0x1) == 1):
        res = "Enabled"
    else:
        res = "Disabled"
    logger.info(f"MGPI ports: = {res} \t")

    tmp = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_SPMI_GENI_STATUS_ADDR')
    if (tmp == 0):
        res = "Idle"
    else:
        res = "Active"
    logger.info(f"Geni Core: = {res} \t")

    tmp = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_SPMI_MWB_ENABLE_REG_ADDR')
    if (tmp == 0):
        res = "Disabled"
    else:
        res = "Enabled"
    logger.info(f"Global Int: = {res} \t")

    tmp = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_SPMI_SEC_DISABLE_REG_ADDR')
    if (tmp == 0):
        res = "Security on"
    else:
        res = "Security off"
    logger.info(f"SEC Disabl: = {res} \t")


    tmp = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_SPMI_CMPR_EN_REG_ADDR')
    if (tmp == 0):
        res = "R/W security off"
    else:
       res = "R/W security on"
    logger.info(f"CMPR: = {res}\t")

    irq = g_hwioConfig.hwio_read(debugger, command, result, 'HWIO_SPMI_PROTOCOL_IRQ_STATUS_ADDR')
    if (irq == 0):
        res = "ERROR"
        return
    else :
        res = "<--- Should not be 0"
    logger.info(f"IRQ Status:{irq} {res}")

    print_if_set(11, irq, "ARBITER_DISCONNECTED")
    print_if_set(10, irq, "ARBITER_CONNECTED")
    print_if_set(9, irq, "PERIH_IRQ_LOST")
    print_if_set(8, irq, "UNEXPECTED_SSC")
    print_if_set(7, irq, "NO_RESPONSE_DATA_FRAME_DETECTED")
    print_if_set(6, irq, "NO_RESPONSE_CMD_FRAME_DETECTED")
    print_if_set(5, irq, "FALSE_MASTER_ARBITRATION_WIN")
    print_if_set(4, irq, "FALSE_BUS_REQUEST")
    print_if_set(3, irq, "UNSUPPORTED_COMMAND")
    print_if_set(2, irq, "DATA_ADDR_FRAME_PARITY_ERROR")
    print_if_set(1, irq, "SLAVE_CMD_FRAME_PARITY_ERROR")
    print_if_set(0, irq, "MASTER_CMD_FRAME_PARITY_ERROR")

    print("SUCCESS")

def print_if_set(bit, val, message):
    """
    Logs a message if the specified bit in the value is set.

    @param bit              - The bit position to check.
    @param val              - The value to check the bit in.
    @param message          - The message to log if the bit is set.
    """
    val=(val>>bit)&0x1
    
    if (val==1):
        logger.info(message)


