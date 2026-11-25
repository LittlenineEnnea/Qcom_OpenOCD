#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file register_dump.py
#
# @section Description
# This file contains script for generating a register dump output from passed 
# input argument file.

import os
import re
import time
import lldb
import platform
from os.path import abspath
from inspect import getsourcefile
from pathlib import Path
from datetime import datetime
from utils.debug_context import logger
from utils.debug_context import g_dbgContext
from utils.dap_access import dapAccessGDB

def register_dump(debugger, command, result, inputFile):
    """!

    This function pase register dump file, reads the register values and copy
    the same in an output file. 

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for debugging.
    @param command          -   String containing all the input arguments
                                for the used command. 
    @param result           -   A return object which encapsulates 
                                success/failure information for the command
                                and output text that needs to be printed 
                                as a result of the command.
    @param inputFile        -   Path to register dump cmm 

    """

    commandInterpreter = debugger.GetCommandInterpreter()
    try:
        # Output File path. Script will generate output file on fixed path
        # outputPath = Path(r"C:\Temp")
        osName = platform.system()
        # Ensure the temp directory exists
        scriptFile = abspath (getsourcefile(lambda:0))
        scriptsRootDir = os.path.dirname(os.path.dirname (scriptFile))
        # Ensure the temp directory exists else create new
        if (osName == 'Windows'):
            outputPath = Path(r"C:\Temp")
        elif (osName in ('Linux', 'Darwin')):
            outputPath = Path(os.path.join(scriptsRootDir, "temp"))
        
        # Ensure the temp directory exists
        outputPath.mkdir(parents=True, exist_ok=True)
        
        # Extract the file name and extension from the input file
        fileName = Path(inputFile)
        outputFile = outputPath / (fileName.stem + ".xml")
        #logger.info("Output File path : {}".format(outputFile))
        
        with open(inputFile, 'r') as infile, open(outputFile, 'w') as outfile:
            logger.info("Reading register values from : {}".format(inputFile))

            writeDone = False
            # Copy content from input file to output file, excluding "WRITE #1"
            for line in infile:
                if "WRITE #1" in line:
                    line = line.replace('WRITE #1 "', "")
                    if "<timestamp>" in line:
                        # Replace the timestamp with the current system time
                        timestamp = datetime.now().strftime("%d. %b %Y %H:%M:%S")
                        line = re.sub(r"<timestamp>.*?</timestamp>", "<timestamp>{}</timestamp>".format(timestamp), line)
                    if "<generator>" in line:
                        # Add generator as Openocd
                        line = re.sub(r"<generator>.*?</generator>", "<generator>{}</generator>".format("Openocd"), line)
                    if "<register" in line:
                        # Regular expression to extract the name attribute
                        name_pattern = re.compile(r"name='([^']*)'")
                        name_match = name_pattern.search(line)
                        name = name_match.group(1) if name_match else "Unknown"

                        # Extract address using regex
                        match = re.search(r"address='(0x[0-9A-Fa-f]+)'", line)
                        if match:
                            address = match.group(1)
                            # Call to dapAccessGDB function to perform EZAXI Read operation
                            addr_value = dapAccessGDB(debugger,command, result, "Yes", address, None, None, None)
                            
                            # Populate the data in a output file. Spaces are kept intentionally to keep the indentation intact
                            outfile.write("                <register address='{}' value='{}' name='{}'/>\n".format(address, addr_value, name))
                        else:
                            logger.info("No match found in line: {}".format(line.strip()))
                        writeDone = True

                    if writeDone is False:
                        outfile.write(line)

        logger.info("Register values have been written to : {}".format(outputFile))
    
    except IOError:
        logger.info("File not found: {}".format(inputFile))
    except Exception as e:
        logger.info("An error occurred: {}".format(e))
