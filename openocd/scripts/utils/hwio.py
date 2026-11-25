#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file hwio.py
#
# @section Description
# This file contains script for parsing and performing hwio operations.

import os
import sys
import lldb
import platform
from os.path import abspath
from inspect import getsourcefile
from pathlib import Path
from utils.debug_context import logger
from utils.debug_context import g_dbgContext
from utils.dap_access import dapAccessGDB

class hwioloader:
    """!
    
    This class parse hwio.cmm and store variables and values

    """
    def __init__(self):
        """!

        The hwioloader object base class initializer.

        @return  An instance of the hwioloader class.

        """
        ## Store Key Value pairs read from hwio.cmm
        self.variables = {}

    def getAddress(self, key):
        """!

        This function  is used to get address from the register name.

        @param key  -   Register name
        @return address of register

        """
        return self.variables.get(key)

    def getRegisterName(self, value):
        """!

        This function  is used to get address from the register name.

        @param key  -   Register name
        @return address of register

        """

        for key,dValue in self.variables.items():
            if (int(dValue,16) == int(value,16)):
                return key

        return ValueError(f"Value provided is incorrect: {value}")


    def set_variable(self, name, value):
        """!

        This function create a database of register name and value 

        @param name - Register name
        @param value - value associated with register name

        """
        self.variables[name] = value

    def parse_line(self,line):
        """!

        This function pase lines from cmm script  

        @param line - Line from input script

        """
        parts = line.split()  # Split the line by whitespace
        if len(parts) < 3:
            raise ValueError(f"Line format is incorrect: {line}")
        name_value = parts[2].split(':') if ':' in parts[2] else [None, parts[2]]  # Handle both formats
        if len(name_value) < 2:
            raise ValueError(f"Name-value format is incorrect: {parts[2]}")
        name = parts[1]  # Extract the variable name
        value = name_value[1]  # Extract the variable value
        return name, value

    def hwio_file_parse(self, debugger, input_file):
        """!

        This function pase hwio.cmm file and creates a database of
        register and values along with a python file containing the same
        in C:/temp

        @param input_file - cmm input file

        """
        try:
            with open(input_file, 'r') as f:
                lines = f.readlines()
        except FileNotFoundError:
            logger.info(f"Input file '{input_file}' not found")
            sys.exit(1)
        osName = platform.system()
        # Ensure the temp directory exists
        scriptFile = abspath (getsourcefile(lambda:0))
        scriptsRootDir = os.path.dirname(os.path.dirname(scriptFile))
        # Ensure the temp directory exists else create new
        if (osName == 'Windows'):
            temp_dir = Path(r"C:\Temp")
        elif (osName in ('Linux', 'Darwin')):
            temp_dir = Path(os.path.join(scriptsRootDir, "temp"))
        
        temp_dir.mkdir(parents=True, exist_ok=True)
        # Extract the base name of the input file and change the extension to .py
        base_name = Path(input_file)
        output_file = temp_dir / (base_name.stem + ".py")
        logger.info("Output file path : {}".format(output_file))

        variables = []
        for line in lines:
            if line.startswith('y.create.l'):
                try:
                    name, value = self.parse_line(line)
                    self.set_variable(name,value)
                    variables.append(f'{name} = {value}\n')
                except ValueError as e:
                    logger.error(f"Skipping line due to error: {e} - Line: {line.strip()}")

        with open(output_file, 'w') as f:
            f.writelines(variables)

        # Add command to source the output file in LLDB
        with open(output_file, 'a') as f:
            f.write(f'\n# Source this file in LLDB\ncommand source -s 0 {output_file}\n')

    def hwio_read(self, debugger, command, result, read_reg, offset = 0):
        if(len(self.variables) != 0):
            addr = self.getAddress(read_reg)
            addr = int(addr, 16)
            value = dapAccessGDB(debugger,command, result, "Yes", addr + offset , None, None, None)
            return int(value, 16)
        else:
            logger.info("Run hwio -f <hwio.cmm file path> first")

    def hwio_write(self, debugger, command, result, write_reg, value, offset = 0):
        if(len(self.variables) != 0):
            addr = self.getAddress(write_reg)
            addr = int(addr, 16)
            dapAccessGDB(debugger,command, result, "Yes" , None, None, addr + offset, value)
        else:
            logger.info("Run hwio -f <hwio.cmm file path> first")
    
    def hwio_retrieve(self, debugger, command, result, value):
        if(len(self.variables) != 0):
            regName = self.getRegisterName(value)
            if (regName != ValueError):
                logger.info("REGISTER NAME : {}".format(regName ))
        else:
            logger.info("Run hwio -f <hwio.cmm file path> first")

    def hwioRegisterAddr(self, debugger, command, result, read_reg):
        if(len(self.variables) != 0):
            addr = self.getAddress(read_reg)
            return addr
        else:
            logger.info("Unable to locate the hwio register.\nRun hwio -f <hwio.cmm file path> first")

    def hwio_access(self, debugger, command, result, inputfile, read_reg=None, write_reg=None, value=None):
        """!

        This function perform hwio access functionality i.e. parsing hwio.cmm file i.e.
        1. Creates a database of register and values along with a python file containing the same
        in C:/temp
        2. With -r option, read hwio register
        3. With -w option, write hwio register

        @param input_file - cmm input file
        @param read_reg - Register name to perform read operation
        @param write_reg - Register name to perform write operation
        @param value - Value to write

        """
        if(inputfile != None):
            self.hwio_file_parse(debugger, inputfile)
        elif(read_reg != None):
            self.hwio_read(debugger, command, result, read_reg)
        elif(write_reg != None):
            self.hwio_write(debugger, command, result, write_reg, value)
        elif (value!= None):
            self.hwio_retrieve(debugger, command, result,value)
        else:
            logger.error("Wrong command. Please use help to get details on commands supported")

##
#   Initializes an instance of hwioloader Object
g_hwioConfig = hwioloader()
