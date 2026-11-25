#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file collect_tz.py
#
# @section Description
# This file contains a script to collect tz(trustzone) logs. 
# This file is owned by SSG team.

import os
import sys
import lldb 
from pathlib import Path 
from utils.debug_context import logger
from utils.dap_access import dapAccessGDB
import subprocess

WORD_SIZE = 4

def get_chipset_config(target):
    """!
    Used to retrieve the chipset configuration for a given target.

    @param target - The target name for which the chipset configuration is required.

    @returns A dictionary containing the chipset configuration, including simem_offset, log_size, and log_buf_offset.

    @note If the target is not found, an error message is logged and a dictionary with an error message is returned.
    """
    target = target.lower()
    # Chipset config map for different chipset configurations. Add in this format in the form of tuples, (<chipset_name>): (<simem_offset>, <log_size>, <log_buf_offset>)
    config_map = {
        ("hoya", "sdm845", "sdm670", "sdm855", "sm8250", "lahaina", "makena"): (0x146BF720, 0x3000, 0x118C),
        ("kodiak",): (0x146AA720, 0x3000, 0x12A0),
        ("lassen",): (0x14680720, 0x3000, 0x0CD8),
        ("lemans", "monaco"): (0x146D8720, 0x3000, 0x118C),
        ("sdx24", "sdx55"): (0x1468B720, 0x2000, 0x0904),
        ("chitwan", "olympic"): (0x1468F720, 0x2000, 0x0904),
        ("sm6150", "sm7150", "bitra", "cedros", "waipio", "kailua", "fillmore", "aurora", "netrani", "kapiti", "hamoa", "clarence"): (0x146AA720, 0x3000, 0x118C),
        ("halliday",): (0x146AA720, 0x3000, 0x0F50),
        ("lanai", "pakala", "palawan", "milos", "bonito", "eliza", "kaanapali", "molokai"): (0x14680720, 0x3000, 0x118C),
        ("aspen",): (0x14680720, 0x3000, 0x0EC8),
        ("matrix",): (0x14680720, 0x3000, 0x1010),
        ("balsam",): (0x14680720, 0x3000, 0x0EC8),
        ("saipan",): (0x146AB720, 0x3000, 0x118C),
        ("qcs405",): (0x08600720, 0x3000, 0x1188),
        ("mdm9205",): (0x08628720, 0x2000, 0x0904),
        ("nicobar", "kamorta", "agatti", "mannar"): (0x0C125720, 0x3000, 0x118C),
        ("pinnacles",): (0x14680720, 0x2320, 0x0CE0),
        ("kuno",): (0x14693720, 0x16C0, 0x0938),
        ("nord",): (0x14680720, 0x4000, 0x118C),
        ("glymur",): (0x14680720, 0x4000, 0x1F70),
        ("amboseli",): (0x14680720, 0x20A0, 0x0F60),
    }

    # collects right set of configuration based on input <target>
    for targets, (simem_offset, log_size, log_buf_offset) in config_map.items():
        if target in targets:
            return {
                "simem_offset": simem_offset,
                "log_size": log_size,
                "log_buf_offset": log_buf_offset
            }

    logger.error(f"Invalid target: {target}")
    return {
        "errorMessage": "Please pass the correct chipset name or target family name",
        "errorMessageLine1": "Passing -h to script would give details of options."
    }

def little_to_big_endian(address):
    """!
    This function converts a little-endian formatted string to big-endian format.
    @param little_endian_string - The input string in little-endian format, which can be a hexadecimal string with or without the '0x' prefix.
    
    @returns A string representing the input string converted to big-endian format, prefixed with '0x'.
    
    @raises ValueError If the input string is not a valid hexadecimal string.
    
    @note The function takes into account both prefixed and non-prefixed hexadecimal strings as input.
    
    """
    hex_string = address.lower()
    if hex_string.startswith("0x"):
        hex_string = hex_string[2:]
    
    if len(hex_string)%2 != 0:
        hex_string = "0" + hex_string

    try:
        # convert to byte values
        byte_data = bytes.fromhex(hex_string)   
    except ValueError:
        logger.error(f"Invalid hex string: {hex_string}")
        raise ValueError(f"Invalid hex string: {hex_string}")
    
    # reverse the byte values
    reversed_byte_data = byte_data[::-1]   

    big_endian_hex = reversed_byte_data.hex()

    return "0x" + big_endian_hex

def get_byte(debugger, command, result, address):
    """!
    Reads a single byte from the specified memory address, returns hexdecimal value.

    @param debugger         -   An instance of the SBDebugger Object that
                                provides access to SBTargets used for debugging.
    @param command          -   String containing all the input arguments
                                for the used command.
    @param result           -   A return object which encapsulates
                                success/failure information for the command
                                and output text that needs to be printed
                                as a result of the command.
    @address                -   The memory address from which the byte is to be read.

    """
    
    # 2 didgits in hexadecimal is equivalent to one byte, one digit is equivalent to 4 bits ot half byte
    last_half_byte = hex(address)[-1]
    # extract the last byte value from the address to locate at which index the byte is locate which needs to be read.
    extracted_index = int("0x" + str(last_half_byte), 16)%(WORD_SIZE)

    try:
        raw_hex_chr_32 = dapAccessGDB(debugger, command, result, scriptRun="Yes", regAddress=address)
    except Exception as e:
        logger.error("Error reading the register !!")
        raise ValueError(f"Error reading the register !!")
    
    hex_chr = little_to_big_endian(raw_hex_chr_32)
    hex_chr = int("0x"+ str(hex_chr[2*extracted_index + 2]) + str(hex_chr[2*(extracted_index)+ 1 + 2]), 16)
    
    return hex(hex_chr)

def bytes_to_ascii(byte_stream):
    """! 
    converts byte stream to UTF-8 format.

    @byte stream             -   The byte stream object to be converted to utf-8 format.
    """
    ascii_string = byte_stream.decode("utf-8", errors="ignore")
    return ascii_string

def getHelp():
    """
    Prints the help message for the script.
    """
    logger.info("Help message for the script:")
    logger.info("-------------------------------")
    logger.info("This script provides help for the tz log scripts.")
    logger.info("For more information, please refer to the usage and example sections above.")
    logger.info('''
    Example: python print_tz_log.py \\\\snowcone\\builds692\\INTEGRATION\\TZ.XF.5.0-00048-S845AAAAANAZT-2 hoya
    Also make sure print_tz_log.py is present at same location as print_tz_log.cmm
    ''')
    logger.info("Supported target families:")
    logger.info("-------------------------------")
    logger.info("target family :hoya (for sdm845, sdm670, sdm855)")
    logger.info("               sdm845")
    logger.info("               sdm670")
    logger.info("               sdm855")
    logger.info("               sdx24")

def get_tz_logs(debugger, command, result, tz_path, target):
    """!
    This function helps in collects trustzone logs, stores the logs in a file and prints the logs in the dbeug console.

    @param debugger         -   An instance of the SBDebugger Object that
                                provides access to SBTargets used for debugging.
    @param command          -   String containing all the input arguments
                                for the used command.
    @param result           -   A return object which encapsulates
                                success/failure information for the command
                                and output text that needs to be printed
                                as a result of the command.
    @tz_path                -   Path to the tz image
    @target                 -   Target family of the chipset
    """

    config = get_chipset_config(target)

    if "errorMessage" in config:
        logger.error(config["errorMessage"])
        return

    if tz_path == "--help" or tz_path == "-h" or target == "" or tz_path == "":
        getHelp()
        return
    
    simem_offset = config["simem_offset"]
    log_size = config["log_size"]
    log_buf_offset = config["log_buf_offset"]

    pwd = os.getcwd()

    # Change the path where you want to save the logs
    log_dir = r"C:\Users"
    user = os.getlogin()
    log_dir = os.path.join(log_dir, user, "Temp")
    os.makedirs(log_dir, exist_ok=True)

    str_diag = os.path.join(log_dir, "TZ_LOG_STR.txt")
    txt_diag = os.path.join(log_dir, "TZ_LOG.txt")

    with open(txt_diag, "w") as fp:
        pass
    with open(str_diag, "w") as fp:
        pass

    dict_path = os.path.join(tz_path, "ssg", "bsp", "qsee", "errorCodesDict.txt")
    if not Path(dict_path).is_file():
        logger.error(f"Not Found: {dict_path}")
        logger.error(f"Please Please Point to TZ Build which has Dictionalry File (Check NSID Location)")
        raise FileNotFoundError("Not Found: errorCodesDict.txt")

    try:
        tzbsp_diag = int(dapAccessGDB(debugger, command, result, scriptRun="Yes", regAddress=simem_offset), 16)
    except Exception as e:
        logger.error(f"Failed to read register: {e}")
    try:
        magic_number= int(dapAccessGDB(debugger, command, result, scriptRun="Yes", regAddress=tzbsp_diag), 16)
    except Exception as e:
        logger.error(f"Failed to read register: {e}")
    
    if magic_number != int("0x747A6461", 16):
        logger.error("Invalid magic number (s/b 0x747A6461):")
        raise ValueError("Invalid magic number (s/b 0x747A6461):")


    logger.info("Please make sure python 3 is installed")
    logger.info(f"RAW log file {txt_diag}")
    logger.info(f"String log file {str_diag}")

    tz_log_buf_start = tzbsp_diag + log_buf_offset
    tz_log_buf_end = tzbsp_diag + log_size

    prev_hex_chr = ""
    debug_str = ""

    try:
        # post-process the read 32 bit address to 16 bit using bitwise 'AND' operation with 0xFFFF, equvivalent to short.
        tz_log_buf_off = int(dapAccessGDB(debugger, command, result, scriptRun="Yes", regAddress=tz_log_buf_start - 2), 16)&(0xFFFF)
    except Exception as e:
        logger.error(f"Failed to read register: {e}")

    tz_log_buf_walk = tz_log_buf_start + tz_log_buf_off
    tz_log_buf_walk_prev = tz_log_buf_walk

    logger.info("print starting at current offset if it has wrapped")
    cntr = 0
    byte_chunks = 0
    debug_log = ""
    byte_stream = []
    while tz_log_buf_walk != tz_log_buf_end:

        # Read the byte from the pointed address, get a hexadecimal value
        hex_chr = get_byte(debugger, command, result, tz_log_buf_walk)

        # collect the byte object
        if int(str(hex_chr), 16) != int("0x00", 16):
            byte_stream.append(int(str(hex_chr), 16))

        if int(str(hex_chr), 16) == int("a", 16) and int(str(prev_hex_chr),16) == int("d", 16):
            # first convert the list to byte object before conversion
            debug_str = bytes_to_ascii(byte_stream = bytes(byte_stream))
            logger.info(debug_str)

            debug_log += debug_str

            tz_log_buf_walk_prev = tz_log_buf_walk + 1
            byte_chunks = 0
            byte_stream = []

        tz_log_buf_walk += 1
        cntr += 1
        byte_chunks += 1
        prev_hex_chr = hex_chr

    with open(txt_diag, "w") as f:
        f.write(debug_log)

    # byte_stream = method1(debugger, command, result, scriptRun="Yes", start_address=tz_log_buf_walk_prev, num_byte_chunks=byte_chunks)
    # debug_str = bytes_to_ascii(byte_stream = bytes(byte_stream))

    tz_log_buf_walk = tz_log_buf_start
    tz_log_buf_walk_prev = tz_log_buf_walk
    
    logger.info("print from start of buffer to current offset")
    cntr = 0
    byte_chunks = 0
    byte_stream = []
    while tz_log_buf_walk != tz_log_buf_start + tz_log_buf_off:
        # Read the byte from the pointed address
        hex_chr = get_byte(debugger, command, result, tz_log_buf_walk)
        byte_stream.append(int(str(hex_chr), 16))

        if int(str(hex_chr), 16) == int("a", 16) and int(str(prev_hex_chr),16) == int("d", 16):
            # first convert the list to byte object before conversion
            debug_str = bytes_to_ascii(byte_stream = bytes(byte_stream))
            logger.info(debug_str)

            debug_log += debug_str

            tz_log_buf_walk_prev = tz_log_buf_walk + 1
            byte_chunks = 0
            byte_stream = []

        tz_log_buf_walk += 1
        cntr += 1
        byte_chunks += 1
        prev_hex_chr = hex_chr

    with open(txt_diag, "a") as f:
        f.write(debug_log)

    logger.info(f"Current directory : {pwd}")
    script_file_loc = os.path.join(pwd, "utils", "print_tz_log.py")

    if not os.path.exists(script_file_loc):
        logger.error("Python script print_tz_log.py file does not exist")
        raise FileNotFoundError(f"Python script print_tz_log.py file does not exist, please add the file at {script_file_loc}")
    
    logger.info(f"Runnning python file {script_file_loc}")
    tz_path = os.path.dirname(tz_path)
    tz_path = tz_path.replace('/', '\\')

    try:
        subprocess.run(["python", script_file_loc, "-t", tz_path, "--l", txt_diag, "--o", str_diag])
        logger.info(f"Log file saved to : {txt_diag}")
    except Exception as e:
        logger.error(f"Error running print_tz_log.py file: {e}")
