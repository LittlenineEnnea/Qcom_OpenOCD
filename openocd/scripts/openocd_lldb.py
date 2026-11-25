#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file openocd_lldb.py
#
# @section Description
# This file is a unified script for debugging FBC subsystems using the LLDB 
# debugger. It defines custom LLDB commands for debugging.

# Imports
import os
import sys
import time
import inspect
import optparse
import argparse
import shlex
import importlib
import lldb
import xml.etree.ElementTree as ET


from utils.debug_context import g_dbgContext
from utils.telnet import *
from utils.debug_context import logger
from utils.dap_access import dapAccessGDB
from utils.openocd import runOpenocd
from lldb_pkg.tech.Hypervisor.pagetable import dump_pagetable
from lldb_pkg.arch.arm.coprocessor_reg import *
from utils.binary_path import SetBinaryPath
from utils.image_path import GetImagePath
from utils.image_path import SetImagePath
from utils.initDebug import initDebug
from utils.reset import ResetCore
from utils.source_map import setSourceMap
from utils.register_dump import register_dump
from utils.hwio import g_hwioConfig
# from utils.sys_pwr_reset import SysPowerReset
from utils.stub import stub_func
from utils.enable_all_clocks import enable_all_clocks
from utils.collect_tz import get_tz_logs
from lldb_pkg.tech.pmic.hardReset import pmicHardreset
from utils.debug_adapter import dbg_adapter
from lldb_pkg.arch.q6.q6_subsys_context import q6Cfg
from utils.connect import connect
from lldb_pkg.tech.QuRT.OSAM import debug_buffer  
from lldb_pkg.tech.QuRT.OSAM import task_list
from lldb_pkg.tech.QuRT.OSAM import interrupt_list
from lldb_pkg.tech.QuRT.OSAM import mergebuffer 
from lldb_pkg.tech.QuRT.OSAM import processlist
from lldb_pkg.tech.QuRT.OSAM import futexlist
from lldb_pkg.tech.QuRT.OSAM import hvx_logs
from lldb_pkg.tech.QuRT.OSAM import hmx_logs
from lldb_pkg.tech.QuRT.OSAM.command_registry import command_registry
from lldb_pkg.tech.Zephyr import zephyr
from lldb_pkg.tech.QuRT.OSAM import qurt_thread_label
from lldb_pkg.tech.QuRT.OSAM import push_qurt_offsets
from lldb_pkg.arch.q6 import hexagon_utils_loader
from utils.tme_logs import tme_logs_command
from lldb_pkg.tech.Hypervisor.hyp_extension import *

## Function Definitions

class openocd:
    """!
    This class is used define a command to start the openocd window, 
    for running this you need to create a environment variable with  
    OPENOCD_DIR name and pass the openocd.exe path to it and the 
    openocd_halt.cfg path aslo should be changed accordingly. 
    Usage : openocd 
    """
    program = 'openocd'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  

        usage = "usage: %prog -c <chipset_ID> -s [subsystem] -d [dabug adapter, protocol]"
        description = ('This command is meant to start openocd in a new python prompt. ')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        parser.add_option("-s", "--subsystem",  action="store",
                        type="string", dest="subsystem")
        parser.add_option("-c", "--chipname",   action="store",
                          type="string", dest='chipName')
        parser.add_option("-d", "--debug_adapter",  action="store",
                        type="string", dest="adapter_and_protocol")
        return parser


    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "command for starting openocd in a new python prompt"

    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()



    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the openocd command that uses meta information and 
        recognises chipset name. This command starts openocd in a new 
        python window, with the identified chipset configuration.

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
        args = shlex.split(command)
        
        (options, args) = self.parser.parse_args(args)

        if (options == '{}'):
            logger.warning (" Please provide valid input arguments.")
            logger.warning (" Usage : openocd -c <chipset_ID> -s [subsystem] -d [dabug adapter and protocol]")

        runOpenocd(debugger, command, exe_ctx, result, options.chipName, options.subsystem)

class dapAccess:
    """!
    This class is used define a command to facilitate access to secure memory regions
    for reads and writes via GDB. \n
    Usage : \n
    dump    :  dap_access -r <address> -n <units> 
    read    :  dap_access -r <address>
    write   :  dap_access -w <address> -v <value>
    write mask:  dap_access -w <address> -v <value> -m <mask>
    """
    program = 'dap_access'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)


    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog [-r <address>] for reads\n" \
                "       %prog [-r <address> -n <units>] for reading n no. of words\n" \
                "       %prog [-w <address> -v <value>] for writes"
        description = ('Secure DAP Access via AXI ')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description = description, prog =  cls.program, usage = usage,
            add_help_option = False
        )
        
        parser.add_option("-r", "--read_register_name",  action="store",
                        type="string", dest="read_reg")
        parser.add_option("-w", "--write_register_name",  action="store",
                        type="string", dest="write_reg")
        parser.add_option("-v", "--value", action="store",
                        type="int", dest="data")
        parser.add_option("-d", "--startaddr", action="store",
                        type="int", dest="start")
        parser.add_option("-n", "--words", action="store",
                        type="int", dest="word_length")
        parser.add_option("-s", "--server", action="store",
                        type="string", dest="server")
        parser.add_option("-m", "--mask", action="store",
                        type="int", dest="mask")        

        return parser


    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "access to secure memory regions for reads and writes via AXI"


    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that facilitate access to secure memory regions
        for reads and writes via GDB. \n

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
        global g_dbgContext
        args = shlex.split(command)
        (options, args) = self.parser.parse_args(args)
        invalidInputError = " Please provide valid input arguments.\n" \
                "Usage : \n"  \
                "dump       :  dap_access -r <address> -n <units> [-s <server>] \n" \
                "read       :  dap_access -r <address> [-s <server>] \n" \
                "write      :  dap_access -w <address> -v <value> [-s <server>]\n" \
                "write mask :  dap_access -w <address> -v <value> [-m <mask>]\n" \

        if (options.read_reg == None and options.write_reg == None):
            logger.error (invalidInputError)

        regAddress = options.read_reg
        num_of_words = options.word_length
        write_reg = options.write_reg
        data = options.data
        mask = options.mask

        if (regAddress != None):
            regAddress = int(regAddress,16)
        elif (write_reg != None):
            write_reg = int(write_reg,16)

        try :
            if (options.server != None ):
                try :
                    if (g_dbgContext.telnetHandle == None):
                        tln = getTelnetHandle()
                    else :
                        closeTelnetHandle()
                        tln = getTelnetHandle()

                    dapAccessTelnet(debugger,command, result, tln, None, regAddress, num_of_words, write_reg, data)
                except :

                    logger.error(" Unable to establish telnet connection with openocd."
                    "OpenOCD server should have been started prior to issuing this command. " )

            else:
                dapAccessGDB(debugger,command, result, None, regAddress, num_of_words, write_reg, data, mask)
        except :
            logger.error("Exception occured")
            return

class connectRemote:
    """! 
    This class is used define a command that helps establish a gdb connection with openocd.\n
    """
    program = 'connect_remote'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -s <subsusyem>"
        description = ("This command is used for establishing gdb connection to "
                    " GDB server for debugging the subsytem on device ")
        
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
        
        parser.add_option('-s', 
                          '--subsystem',
                          action='store',
                          type='string', 
                          dest='subsystem_core')
        return parser

    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "command to connect to openocd's GDB server for debugging the subsytem on device"

    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that helps establish a gdb connection 
        with openocd.

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
        command_args = shlex.split(command)
        try :
            (options, args) = self.parser.parse_args(command_args)
            if (options == None or options.subsystem_core == None):
                logger.error("Please provide subsystem")
                return
        except :
            logger.warning ("Please provide valid input arguments.\n"
            "Usage : connect_remote -s <subsusyem>")
            result.SetError("option parsing failed")
            return
        connect(debugger, command, exe_ctx, result)

class debug:
    """! 
    This command is meant to initiate debug of images such as XBL, TZ, HYP, UEFI.
    OpenOCD server should have been started prior to issuing this command.
    Usage : \n
    XBL DEBUG   :   debug -d xbl 
    TZ DEBUG    :   debug -d tz 
    HYP DEBUG   :   debug -d hyp
    UEFI        :   debug -d uefi [-i <module_name>]
    """
    program = 'debug'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -d <subsystem> [-i <module> -o <binary-file-path>]"
        description = ('This command is meant to initiate debug of images such as XBL, TZ, HYP, UEFI. '
                       'OpenOCD server should have been started prior to issuing this command. ')
        
        # Pass add_help_option = False, since this keeps the command in line
        #  with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        parser.add_option('-d', 
                          '--debugscript',
                          action='store',
                          type='string', 
                          dest='debugscript')
        parser.add_option('-o', 
                          '--runmode',
                          action='store',
                          type='string', 
                          dest='run')
        parser.add_option('-i', 
                          '--module',
                          action='store',
                          type='string', 
                          dest='module')
        parser.add_option('-s',
                          '--debugstep',
                          action='store',
                          type='string',
                          dest='debugstep')
        parser.add_option('-p',
                          '--symbol_file',
                          action='store',
                          type='string',
                          dest='path',
                          default=None)

        return parser


    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return " command for debugging FBC images such as XBL, TZ, HYP, UEFI"


    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that is meant to initiate debug of images
        such as XBL, TZ, HYP, UEFI. OpenOCD server should have been started 
        prior to issuing this command.

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
        command_args = ""
        Module = None
        validDbgCmds = ["PBL", "XBL", "TFA", "TZ", "HYP", "UEFI", "APSS", "ABL", "AOSS", "SYSTEMD", "SOCCP", "CPUCP", "LMCU", "TME", "AWM", "SWM"]

        # Clean up command: remove flags with empty values (e.g., "-p " at end)
        import re
        command = re.sub(r'\s+-p\s*$', '', command)

        args = shlex.split(command)

        (options, args) = self.parser.parse_args(args)

        if (options.debugscript == None):
            logger.error (" Please provide valid input arguments.\n"
                          "Usage : debug -d <subsystem> [-i <module> -o <binary-file-path> -s <debugstep>/<source_path>]")
            return
        
        if (str(options.debugscript.upper()) not in validDbgCmds):
            logger.error("Please check if {} is a valid subsystem name".format(options.debugscript.upper()))
            return
            
        if ((options.module != None) or (options.run != None) or ( options.debugstep != None)):
            if (options.module != None):
                command_args = "\'{}\'".format(options.module)
                Module = command_args
            elif (options.run != None):
                command_args = str(options.run)
            elif (options.debugstep != None):
                command_args = str(options.debugstep)

        if (g_dbgContext.chipsetName != None):
            chip_name = g_dbgContext.chipsetName.lower()
        else:
            logger.error("Chipset name is not provided. Please set chipset name in settings.json")
            return

        # Map image names to module names (aoss -> aop)
        image_module_name = options.debugscript.lower()
        if image_module_name == "aoss":
            image_module_name = "aop"

        import_str = f"lldb_pkg.target.{chip_name}.image.{image_module_name}"
        image_script_module = importlib.import_module(import_str)
        if options.debugscript.upper() == "XBL":
            debugXBL = getattr(image_script_module, 'debugXBL')
        elif options.debugscript.upper() == "TFA":
            debugTFA = getattr(image_script_module, 'debugTFA')
        elif options.debugscript.upper() == "TZ":
            debugTZ = getattr(image_script_module, 'debugTZ')
        elif options.debugscript.upper() == "HYP":
            debugHYP = getattr(image_script_module, 'debugHYP')
        elif options.debugscript.upper() == "UEFI":
            debugUEFI = getattr(image_script_module, 'debugUEFI')
        elif options.debugscript.upper() == "PBL":
            debugPBL = getattr(image_script_module, 'debugPBL')
        elif options.debugscript.upper() == "SOCCP":
            debugSOCCP = getattr(image_script_module, 'debugSOCCP')
        elif options.debugscript.upper() == "TME":
            debugTME = getattr(image_script_module, 'debugTME')
        elif options.debugscript.upper() == "CPUCP":
            debugCPUCP = getattr(image_script_module, 'debugCPUCP')
        elif options.debugscript.upper() == "LMCU":
            debugLMCU = getattr(image_script_module, 'debugLMCU')
        elif options.debugscript.upper() == "SWM":
            debugSWM = getattr(image_script_module, 'debugSWM')
        elif options.debugscript.upper() == "AWM":
            debugAWM = getattr(image_script_module, 'debugAWM')
        elif options.debugscript.upper() == "AOSS":
            debugAOSS = getattr(image_script_module, 'debugAOSS')
        if (options.path != None and options.path != ''):
            command_args = "\'{}\'".format(options.path)
            functionName = "debug{}(debugger, command, exe_ctx, result, None, {}, {})".format(str( \
                options.debugscript.upper()), Module , str(command_args))
        elif (command_args != '' and (options.path == None or options.path == '')):
            functionName = "debug{}(debugger, command, exe_ctx, result, None, {})".format(str( \
                options.debugscript.upper()), str(command_args))
        else :
            functionName = "debug{}(debugger, command, exe_ctx, result)".format(str( \
                options.debugscript.upper()))

        eval(functionName)
        
class hardReset:
    """! 
    This command is meant to perform hard reset of the device, by writing to PMIC registers.
    This helps clear pending SPMI transactions.\n
    Usage : pmic_hardreset
    
    """
    program = 'pmic_hardreset'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog"
        description = ('This command is used to perform hard reset by writing to PMIC registers ' 
                       'This helps clear pending SPMI transactions.')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
        return parser

    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "command to perform hard reset by writing to PMIC registers"

    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that is meant to perform hard reset of 
        the device, by writing to PMIC registers.This helps clear pending 
        SPMI transactions.

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

        if (g_dbgContext.chipsetName != None):
            chip_name = g_dbgContext.chipsetName.lower() 
        else:
            logger.error("Please set metapath or chipname before running the hardreset command")
            return


        # This check is added to execute PMIC scripts.
        if chip_name == "bonito" or chip_name == "hamoa" or chip_name == "milos" or chip_name == "eliza" or chip_name == "netrani" or chip_name == "kaanapali" or chip_name == "balsam" or chip_name == "molokai" or chip_name == "alana" or chip_name == "clarence" or chip_name == "poros" or chip_name == "mavros": 
            # std_reset.py script imports spmi and pmic scripts relevant to the chipset hence we 
            # require chipsetname to be set for this import
            from utils.std_reset import std_reset
            std_reset(debugger, command, exe_ctx, result)
        else:
            pmicHardreset(debugger, command, exe_ctx, result, None)

class PeekPoke:
    """! 
    This command is meant to perform read and write operation on the device, by using write flag we are writing into PMIC registers.
    and same for read, by using read flag we are reading the registers value from the PMIC registers.
    This helps to read and write on PMIC registers.\n
    Usage : pmic_peek_poke <flag> <busID> <slaveId> <address> <data>
    
    """
    program = 'pmic_peek_poke'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog <flag> <busID> <slaveId> <address> <data>\n\nFlags:\n  -r  Read from PMIC register\n  -w  Write to PMIC register"
        description = ('This command is used to perform read and write operation on PMIC registers.')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
        return parser

    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "command to perform read and write operation on PMIC registers"

    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        This command is meant to perform read and write operation on the device, by using write flag we are writing into PMIC registers.
        and same for read, by using read flag we are reading the registers value from the PMIC registers.

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
        if (g_dbgContext.chipsetName != None):
            chip_name = g_dbgContext.chipsetName.lower() 
        else:
            logger.error("Please set metapath or chipname before running the PMIC PEEK POKE command")
            return
	    # pmic_peek_poke.py script imports pmic scripts relevant to the chipset hence we 
	    # require chipsetname to be set for this import
        from lldb_pkg.tech.pmic.pmic_peek_poke import pmic_peek_poke

        pmic_peek_poke(debugger, command, exe_ctx, result)

class PmicDump:
    """! 
    This command is meant to collect the pmic dump of pmics. 
    Usage : pmic_dump <bus_id> <slave_id>
    
    """
    program = 'pmic_dump'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog <bus_id> <slave_id>"
        description = ('This command is meant to collect the pmic dump of all pmics register')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
        return parser

    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "This command is meant to collect the pmic dump of all pmics register"

    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        This command is meant to collect the pmic dump of all pmics register

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
        if (g_dbgContext.chipsetName != None):
            chip_name = g_dbgContext.chipsetName.lower() 
        else:
            logger.error("Please set metapath or chipname before running the PMIC DUMP command")
            return
	    # pmic_dump.py script imports pmic scripts relevant to the chipset hence we 
	    # require chipsetname to be set for this import
        from lldb_pkg.tech.pmic.pmic_dump import pmic_dump

        pmic_dump(debugger, command, exe_ctx, result)

class metaDirectory:
    """! 
    This command is used for getting or setting meta related information to be \
    used for debug such as image paths and symbol information.\n
    Usage :
    meta_dir -s  <metabuild_path>
    """
    program = 'meta_dir'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -s <metapath> for setting meta path\n" \
                "       %prog for getting meta path"
        description = ('For getting or setting meta related information to be used \
                       for debug such as image paths and symbol information')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        parser.add_option("-s", "--metapath",  action="store",
                                type="string", dest="metadir")

        return parser


    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "command to set or get meta related information to be used for debug"


    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command is used for getting or setting meta 
        related information to be used for debug such as image paths and
        symbol information.

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

        args = shlex.split(command)
        (options, args) = self.parser.parse_args(args)

        if (options =='{}'):
            logger.warning ("Please provide valid input arguments.\n Usage : \
                             meta_dir -s <metapath>")

        if (options.metadir == None):
            logger.info (" meta_path is set to  : %s" , \
                          g_dbgContext.openocdConfig.getMetaPath())
            return
        metaDir = options.metadir

        g_dbgContext.userMeta = 1
        initDebug(metaDir, g_dbgContext.userMeta)
        
class telnetCommand:
    """! 
    This command is intended for using commands via  telnet connection.\n
    Usage :
    telnet -c <openocd_command>
    """
    program = 'telnet'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -c <openocd_command>"
        description = ('This command is intended for using openocd telnet commands via \
                        telnet connection using lldb')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage, 
            add_help_option=False)
        

        parser.add_option("-c", "--command",  action="store",
                        type="string", dest="cmd")

        return parser


    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "command for sending telnet commands"


    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that is intended for using commands via
        telnet connection.

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
        args = shlex.split(command)
        (options, args) = self.parser.parse_args(args)

        if (options =='{}'):
            logger.warning (" Please provide valid input arguments.\n Usage \
                             : telnet -c <openocd_command>")

        try :
            sendTelnetCommand(str(options.cmd))
        except :
            logger.error ("Unable to establish telnet connection with openocd. \
                            OpenOCD server should have been started prior to issuing \
                            this command. " )


class runtimedxe:
    """! 
    This command is used to halt at the entry of runtimedxe. This has to be run \
    after debug -d uefi.\n
    Usage :
    runtimedxe
    """
    program = 'runtimedxe'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog"
        description = ('command is used for halting at the entry of runtimedxe.\
                        This has to be run after debug -d uefi')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.

        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        return parser


    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "command for halting at the entry of runtimedxe. Run this command after debug -d uefi"


    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that is used to halt at the entry of 
        runtimedxe. This has to be run after debug -d uefi.

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
        runtimedxeUEFI(debugger, command, exe_ctx, result)

class binaryPath:
    """! 
    This command is used to set or get path for a specific binary/symbol file.\n
    Usage :
    binary_path -i <imagename> [-s <binary-file-path> -f <filename>]
    """
    program = 'binary_path'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)


    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -i <imagename> -s <binary-file-path> for setting the binary path\n" \
                "       %prog -i <imagename> -f <filename> for getting the binary path"
        description = ('This command is used to set or get path for a specific \
                       binary/symbol file')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
        
        parser.add_option("-i", "--image",  action="store",
                                type="string", dest="image")
        parser.add_option("-s", "--binarypath",  action="store",
                                type="string", dest="binarypath")
        parser.add_option("-f", "--filename",  action="store",
                                type="string", dest="filename")

        return parser


    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "command to set or get engineering Binary Path "


    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that is used to set or get path for a 
        specific binary/symbol file.

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

        args = shlex.split(command)

        (options, args) = self.parser.parse_args(args)

        if (options.filename == None and options.binarypath == None):
            logger.error (" Please provide valid input arguments.\n" \
            "Usage: binary_path -i <imagename> [-s <binary-path -f <binary-filename>]")
            return

        if (options.filename != None):
            readBinaryPath = g_dbgContext.openocdConfig.\
                getBinaryPath(options.image, options.filename)
            # log error is printed as a part of exception handling in meta info
            if (readBinaryPath  == "ERROR"):
                return
            else :
                logger.info(" {}".format(readBinaryPath))            
                return
        
        SetBinaryPath(debugger, command, exe_ctx, result, options.image, options.binarypath)
 
        
class imagePath:
    """! 
    This command is used to get the image path or modify the image path and set
    it to an engineering build path.
    Usage :
    image_path -i <imagename> [-s <image-path>]
    """
    program = 'image_path'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()

        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  

        usage = "usage: %prog -i <imagename> -s <image-path> for setting image path" \
                "       %prog -i <imagename> for getting image path"
        description = ("This command is used to get or set the image path" \
                    " to an engineering build path")
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False )

        parser.add_option("-i", "--image",  action="store",
                                type="string", dest="image")
        parser.add_option("-s", "--imagepath",  action="store",
                                type="string", dest="imagepath")
        return parser


    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "command to set or get image path"


    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that is used to get the image path or 
        modify the image path and set it to an engineering build path.

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


        args = shlex.split(command)

        (options, args) = self.parser.parse_args(args)
        if (options.image == None):
            logger.warning (" Please provide valid input arguments.\n" \
                        "Usage: image_path -i <imagename> [-s <image-path]")
            return
        
        if (options.imagepath == None):
            GetImagePath(debugger, command, exe_ctx, result, options.image, options.imagepath)            
            return

        SetImagePath(debugger, command, exe_ctx, result, options.image, options.imagepath)   
        

class coprocessorReg:
    """! 
    This command to read/write Coprocessor registers.\n
    Usage : 
    list categories             :  reg -l category
    dump category of registers  :  reg -c <category_number>
    read                        :  reg -r <address>
    write                       :  reg -w <address> -v <value>
    """
    program = 'reg'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -l category for listing the register categories\n" \
                "       %prog -c <category_number> for reading registers from a category\n" \
                "       %prog -r <address> for reading register\n" \
                "       %prog -w <address> -v <value> for writing data to a register"
        description = ('command to read/write Coprocessor registers')
        
        # Pass add_help_option = False, since this keeps the command in line
        #  with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        parser.add_option("-r", "--read_register_name",  action="store",
                        type="string", dest="read_reg")
        parser.add_option("-l", "--register_list",  action="store",
                        type="string", dest="register_list")
        parser.add_option("-w", "--write_register_name",  action="store",
                        type="string", dest="write_reg")
        parser.add_option("-v", "--value", action="store",
                        type="int", dest="data")
        parser.add_option("-c", "--number", action="store",
                        type="int", dest="category_no")  

        return parser


    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return 'command to read/write Coprocessor registers'


    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that is used to read/write Coprocessor
        registers.

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

        global g_dbgContext
 
        args = shlex.split(command)
        (options, args) = self.parser.parse_args(args)
                
        if (options =='{}'):
            logger.info(
                "Please provide valid input arguments.\n \
                Usage : \n  \
                    dump    :  reg -r <address> -c <units>\n \
                    read    :  reg -r <address>\n    \
                    write   :  reg -w <address> -v <value>" 
                )
            
        if (options.register_list == 'category'):
            registerCategory(debugger, command, exe_ctx, result)

        if (options.category_no):
            categoryList(debugger, command, exe_ctx, result, options.category_no)

        if (options.read_reg):
            registerRead(debugger, command, exe_ctx, result, options.read_reg)

        if (options.write_reg):
            registerWrite(debugger, command, exe_ctx, result, options.write_reg, options.data)

class sourcePath:
    """! 
    This command to map source path \n
    Usage : 
    source_path -i <imagename> -p <source-path>

    """
    program = 'source_path'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -i <imagename> -s <sourcepath>"
        description = ('command to map source path')
        
        # Pass add_help_option = False, since this keeps the command in line
        #  with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        parser.add_option("-i", "--image",  action="store",
                        type="string", dest="image")
        parser.add_option("-p", "--path",  action="store",
                        type="string", dest="path")

        return parser


    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return 'command to map source path'


    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that is used to read/write Coprocessor
        registers.

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
        args = shlex.split(command)
        (options, args) = self.parser.parse_args(args)
        image = options.image
        path = options.path
        if image == None:
            logger.error("Invalid Image")
            return
        if path == None:
            logger.error("Invalid Image path")
            return
        g_dbgContext.sourcePath [str((image.lower()))] = str(path)
        logger.info("MAPPED PATH : {}".format(g_dbgContext.sourcePath[str(options.image)]))
        if (os.path.exists(options.path) != True):
            logger.error("Invalid source path set")

        # to prevent halting of the debug execution due to exception
        # when incorrect or no source path is set
        try :
            setSourceMap(debugger, str(options.image), str(options.path))
        except:
            pass
        
        return

class TargetReset:
    """!
    This command is used to reset the target and then halt it immediately.\n
    Usage : qreset
    """
    program = 'qreset'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):

        usage = "usage: %prog"
        description = ('This command is used to reset the target and then halt it immediately')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)    
        return parser
    
    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "command to reset apss core0 target and halt it immediately after reset"
    
    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string
    
    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that is used to reset apss core0 target
        and halt is immediately after reset

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
        ResetCore(debugger)
    

class mmu:
    """! 
    This command to dump the EL1 pagetable.\n
    Usage : 
    dump pagetable              :  mmu_dump -d EL1 -p <dump_file_path>
    """
    program = 'mmu_dump'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -d EL1 -p <dump_file_path>"
        description = ('command to dump the EL1 pagetable')
        
        # Pass add_help_option = False, since this keeps the command in line
        #  with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        parser.add_option("-d", "--dump_EL1",  action="store",
                        type="string", dest="HLOS")
        parser.add_option("-p", "--path", action="store",
                        type="string", dest="path") 

        return parser


    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return 'command to dump the EL1 pagetable'


    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that is used to read/write Coprocessor
        registers.

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

        global g_dbgContext
 
        args = shlex.split(command)
        (options, args) = self.parser.parse_args(args)
        target = debugger.GetSelectedTarget()
        pt_base = target.EvaluateExpression("pt_context[3].attr.pt_base")
        pt_base = pt_base.GetValueAsUnsigned()
        stage_2 = 0x1
        logger.info("Performing PT Walk.. Please wait...")
        dump_pagetable(debugger, command, exe_ctx, result, pt_base, options.path,stage_2)
        logger.info ("Done Parsing Page Table!")
        logger.info (f"Output Located At {options.path}")
		
class registerDump:
    """!
    This class is used define a command to take register dump, 
    For running this you need to pass register dump cmm file as input parameter.      
    Usage : 
    dump : registerDump -f <register dump cmm file path> 
    """
    program = 'registerDump'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)
  
    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -f <filepath>"
        description = ('command to take register dump')
        
        # Pass add_help_option = False, since this keeps the command in line
        #  with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
            
        parser.add_option("-f", "--filepath",  action="store",
                        type="string", dest="cmmfilepath")
            
        return parser
              
    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "command to take register dump"
    
    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of this command helps to take register dump.

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
        args = shlex.split(command)
        (options, args) = self.parser.parse_args(args)

        if (options.cmmfilepath == None):
            logger.error (" Please provide valid input arguments.\n"
                          "Usage : RegisterDump -f <cmmfilepath>")
            return

        input_file = options.cmmfilepath
        register_dump(debugger, command, result, input_file)

class hwio:
    """!
    This class is used define a command to source hwio, 
    For running this you need to pass hwio cmm file as input parameter.      
    Usage : 
    Read hwio file : hwio -f <hwio.cmm file path> 
    Read register  : hwio -r <register name as mentioned in hwio.cmm>
    Write register : hwip -w <register name as mentioned in hwio.cmm> <value> 
    """
    program = 'hwio'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to source HWIO data.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)
  
    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -f <filepath> for hwio setting\n" \
                "       %prog -r <registerName> for hwio read\n"\
                "       %prog -w <registerName> <value> for hwio write"
        description = ('Command to source hwio cmm file and create database of registers \
                        and values accessible in debug session')
        
        # Pass add_help_option = False, since this keeps the command in line
        #  with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
            
        parser.add_option("-f", "--filepath",  action="store",
                        type="string", dest="cmmfilepath")
        
        parser.add_option("-r", "--read_register_name",  action="store",
                        type="string", dest="read_reg")

        parser.add_option("-w", "--write_register_name",  action="store",
                        type="string", nargs=2, dest="write_reg_val")
        parser.add_option("-v", "--lookup_register_address",  action="store",
                        type="string", nargs=1, dest="lookup_register_address")
        return parser
              
    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "command to source hwio"
    
    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of this command helps to create a database of hwio registers 
        and perform read / write functionality

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
        global ghwioConfig
        regname = None
        val = None
        
        args = shlex.split(command)
        (options, args) = self.parser.parse_args(args)
        invalidInputError = " Please provide valid input arguments.\n" \
            "Usage : \n"  \
            "hwio       :  hwio -f <hmio cmm path> \n" \
            "hwio read  :  hwio -r <register name> \n" \
            "hwio write :  hwio -w <register name> <value> \n" \

        # Logic to handle register name and value space seperated
        if options.write_reg_val:
            if len(options.write_reg_val) == 2:
                regname, val = options.write_reg_val
                try:
                    val = int(val, 0)
                except ValueError:
                    result.SetError("Value for write operation must be an integer.")
                    return
        
        if options.lookup_register_address :
            val = options.lookup_register_address
        # Function call to perform hwio access
        g_hwioConfig.hwio_access(debugger, command, result, options.cmmfilepath, options.read_reg, regname, val)

class SystemPowerReset:
    """!
    This command is used to power off/on a target and also enables EUD if the state is false before doing a power reset.\n
    Usage : 
    """
    program = 'syspwrreset'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):

        usage = "usage: %prog"
        description = ("This command is used to power off/on a target and also"\
        " enables EUD if the state is false before doing a power reset")
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)    
        return parser
    
    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "command to power off/on a target and also enable EUD if its not before doing a power reset"
    
    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string
    
    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that is used to power off/on a target and
        also enables EUD if the state is false before doing a power reset.

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
        SysPowerReset()

class stub:
    """!
    This class is used define a command to perform stubbing of
    a function.      
    Usage : 
    stub <func_name> [ret_val]
    """
    program = 'stub'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to stub a function.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)
  
    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a argparse.ArgumentParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "%(prog)s <func_name> [ret_val]"
        description = ('Command to stub a function')
        
        parser = argparse.ArgumentParser(prog=cls.program, usage=usage, description=description)
        parser.add_argument("func_name", type=str, help="The name of the function")
        parser.add_argument("ret_val", nargs='?', type=int, help="function return val (optional)")
    
        return parser
              
    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "command to stub a function"
    
    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of this command is to stub a function.

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
        args = shlex.split(command)
        try:
            arguments = self.parser.parse_args(args)
        except SystemExit:
            logger.error("The following argument is required: func_name")
            logger.info('Enter "help stub" for more info')
            return

        name = arguments.func_name
        val = arguments.ret_val
        stub_func(debugger, command, exe_ctx, name, val)


class chipset:
    """! 
    This class is used define a command that helps in setting the chipset name/ID.\n
    """
    program = 'set_chipset'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -s <chipset_name>"
        description = (" This command is used for setting the chipset name ")
        
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
        
        parser.add_option('-s', 
                          '--chipname',
                          action='store',
                          type='string', 
                          dest='chipset')
        return parser

    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "command to set the chipset name"

    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of the command that helps establish a gdb connection 
        with openocd.

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
        command_args = shlex.split(command)
        try :
            (options, args) = self.parser.parse_args(command_args)
            if (options == None or options.chipset == None):
                logger.error("Please provide valid chipset name/ID")
                return
            if options.chipset == "notarget":
                g_dbgContext.cli = 1
            else:
                g_dbgContext.chipsetName = options.chipset.lower()

        except :
            logger.warning ("Please provide valid input arguments.\n"
            "Usage: set_chipset -s <chipset_name/ID>")
            result.SetError("option parsing failed")
            return

class gdbRemoteHost:
    """!
    This class is used define a command that helps in setting the host
    (hostname or IP, IPv4/IPv6) of the machine running OpenOCD, so that
    gdb-remote and telnet connections can target a remote OpenOCD instance
    instead of assuming it is always co-located on localhost.\n
    """
    program = 'gdb_remote_host'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that
                                    provides access to SBTargets used for
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!
        This function is used give input arguments used by the command as
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options

        """


        usage = "usage: %prog -s <hostname_or_ip>"
        description = (" This command is used for setting the hostname/IP of "
                       "the machine running OpenOCD, for gdb-remote and telnet "
                       "connections ")

        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        parser.add_option('-s',
                          '--host',
                          action='store',
                          type='string',
                          dest='host')
        return parser

    def get_short_help(self):
        """!
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """
        return "command to set the OpenOCD host (hostname or IP) for gdb-remote/telnet connections"

    def get_long_help(self):
        """!
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that
                                    provides access to SBTargets used for
                                    debugging.

        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python

        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """!
        Implementation of the command that sets the hostname/IP of the
        machine running OpenOCD, used by gdb-remote/telnet connections.

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
        command_args = shlex.split(command)
        try :
            (options, args) = self.parser.parse_args(command_args)
            host = options.host
        except SystemExit:
            # optparse calls sys.exit() when "-s" is given with no value
            # (e.g. GDB_REMOTE_HOST is an empty string) -- treat the same
            # as "-s" being omitted entirely, not as a parse failure.
            host = None
        except :
            logger.warning ("Please provide valid input arguments.\n"
            "Usage: gdb_remote_host -s <hostname_or_ip>")
            result.SetError("option parsing failed")
            return

        host = host.strip() if host else None
        g_dbgContext.gdbRemoteHost = host
        if host:
            logger.info(f"OpenOCD gdb-remote/telnet host is {g_dbgContext.gdbRemoteHost}")
        else:
            logger.info("GDB remote host not selected, defaulting to localhost")

# ---------------------------------------------------------------------------
# Per-target Storage routing (jtagprogrammer / std_loadbuild_la)
#
# jtagprogrammer.py and std_loadbuild_la.py are now target-specific, one
# copy per chipset under lldb_pkg/target/<chip>/Storage/ (e.g. target/clarence/Storage,
# target/kaanapali/Storage). These two commands pick the right copy at call time
# based on g_dbgContext.chipsetName (set via set_chipset -s <chip_name>),
# instead of the launch config importing one hardcoded copy directly.
#
# The folder name is just the chipset name in lowercase
# (e.g. "kaanapali" -> "kaanapali"), and every target/<chip>/Storage/std_loadbuild_la.py
# (and jtagprogrammer.py) exposes the same fixed entry-point names —
# "std_loadbuild_la" / "jtagprogrammer" — regardless of chip.
# ---------------------------------------------------------------------------

class jtagprogrammerRouter:
    """!
    Routes the 'jtagprogrammer' command to lldb_pkg/target/<chip>/Storage/jtagprogrammer.py
    for whichever chipset is set via set_chipset -s <chip_name>.\n
    Usage : jtagprogrammer [elfpath=<path>] [dtbpath=<path>] [slot=0] [<COMMAND>]
    """
    program = 'jtagprogrammer'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration
        @param debugger         -   An instance of the SBDebugger Object that
                                    provides access to SBTargets used for
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented
        """
        command = 'command script add -c %s.%s %s' % (module_name,
                                                       cls.__name__,
                                                       cls.program)
        debugger.HandleCommand(command)

    def get_short_help(self):
        """! @return a short description of this command."""
        return "command to drive JtagProgrammer, routed to the current chipset's implementation"

    def get_long_help(self):
        """! @return a long description of this command."""
        return self.get_short_help()

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that
                                    provides access to SBTargets used for
                                    debugging.
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        """
        pass

    def __call__(self, debugger, command, exe_ctx, result):
        """!
        Implementation of the command that routes to the current chipset's
        jtagprogrammer.py under lldb_pkg/target/<chip>/Storage/.

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
        chip_name = (g_dbgContext.chipsetName or "").lower()
        if not chip_name:
            logger.error("Please run set_chipset -s <chip_name> before using this command.")
            result.SetError("chipset not set or unsupported")
            return

        module_path = f"lldb_pkg.target.{chip_name}.Storage.jtagprogrammer"
        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            logger.error(f"Failed to import {module_path}: {e}")
            result.SetError(str(e))
            return

        # std_loadbuild_la.py's _get_jtp() looks up sys.modules["jtagprogrammer"]
        # to find the live JtagProgrammer instance — alias it here so that
        # lookup finds whichever chipset's module is actually active.
        sys.modules["jtagprogrammer"] = module

        module.jtagprogrammer(debugger, command, exe_ctx, result)


class stdLoadbuildRouter:
    """!
    Routes the 'std_loadbuild_la' command to lldb_pkg/target/<chip>/Storage/std_loadbuild_la.py
    for whichever chipset is set via set_chipset -s <chip_name>.\n
    Usage : std_loadbuild_la <LOADOPTION> [loadimages=<img>] [extraoptions=<opts>]
    """
    program = 'std_loadbuild_la'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration
        @param debugger         -   An instance of the SBDebugger Object that
                                    provides access to SBTargets used for
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented
        """
        command = 'command script add -c %s.%s %s' % (module_name,
                                                       cls.__name__,
                                                       cls.program)
        debugger.HandleCommand(command)

    def get_short_help(self):
        """! @return a short description of this command."""
        return "command to drive std_loadbuild_la, routed to the current chipset's implementation"

    def get_long_help(self):
        """! @return a long description of this command."""
        return self.get_short_help()

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that
                                    provides access to SBTargets used for
                                    debugging.
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        """
        pass

    def __call__(self, debugger, command, exe_ctx, result):
        """!
        Implementation of the command that routes to the current chipset's
        std_loadbuild_la.py under lldb_pkg/target/<chip>/Storage/.

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
        chip_name = (g_dbgContext.chipsetName or "").lower()
        if not chip_name:
            logger.error("Please run set_chipset -s <chip_name> before using this command.")
            result.SetError("chipset not set or unsupported")
            return

        module_path = f"lldb_pkg.target.{chip_name}.Storage.std_loadbuild_la"
        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            logger.error(f"Failed to import {module_path}: {e}")
            result.SetError(str(e))
            return

        module.std_loadbuild_la(debugger, command, exe_ctx, result)



class enableAllClocks:
    """!
    This class is used define a command for enable all clocks functionality
    Usage: enable_all_clocks -f <enable_all_clocks.cmm file path> -s <subsystem>
    """
    program = 'enable_all_clocks'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to enable all clocks for a subsystem

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)
  
    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog -f <filepath> -s <subsystem>"
        description = ('Command to enable all clocks for a subsystem')
        
        # Pass add_help_option = False, since this keeps the command in line
        #  with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
            
        parser.add_option("-f", "--filepath",  action="store",
                        type="string", dest="cmmfilepath")
        
        parser.add_option("-s", "--subsystem",  action="store",
                        type="string", dest="subsys")
    
        return parser
              
    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "command to enable all clocks"
    
    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of this command helps to enable all clocks for a subsystem

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
        args = shlex.split(command)

        (options, args) = self.parser.parse_args(args)
        invalidInputError = "Please provide valid input arguments.\n" \
                            "Usage : enable_all_clocks [-f <filepath> -s <subsystem>] \n"  
            
        if options == None or (options.cmmfilepath == None and options.subsys == None):
            logger.error (invalidInputError)
            return
        
        #Call to Parse and perform enable all clock functionality
        enable_all_clocks(debugger, command, result, options.cmmfilepath, options.subsys)

class q6_entry:
    """!
    This class is used define a command to perform q6 entry configuration at apss
    Usage: q6_entry -s <subsystem>
    """
    program = 'q6_entry'
    
    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands to 
        perform q6 entry configuration at apss

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  
        
        usage = "usage: %prog -f <filepath> -s <subsystem> "
        description = ('Command to perform q6 entry configuration at apss')
        
        # Pass add_help_option = False, since this keeps the command in line
        #  with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
            
        parser.add_option("-s", "--subsystem",  action="store",
                        type="string", dest="subsystem")
    
        return parser
              
    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "Performs q6 entry configuration at apss"
    
    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of this command performs q6 entry configuration at apss

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
        args = shlex.split(command)
        (options, args) = self.parser.parse_args(args)

        if (options.subsystem == 'NULL' or options.subsystem.lower() not in \
            ['adsp','cdsp','mpss','wpss']):
            logger.error("Please specify valid Q6 subsystem. \n" \
            "Options are: \n1. ADSP \n2. CDSP \n3. MPSS \n4. WPSS")
            return

        if (g_dbgContext.chipsetName != None):
            chip_name = g_dbgContext.chipsetName.lower()
        else:
            logger.error("Chipset name is not provided. Please set chipset name in settings.json")
            return

        q6_module = importlib.import_module(f"lldb_pkg.target.{chip_name}.image.q6")
        q6Entry = getattr(q6_module, 'q6Entry')
        q6Entry(debugger, command, exe_ctx, result, options.subsystem)

class root_pd_debug:
    """!
    This class is used define a command to perform root pd debug for q6 subsystems
    Usage: root_pd_debug -o <subsystem> -e <entry_name>
    """
    program = 'root_pd_debug'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined command to 
        perform root pd debug for q6 subsystems

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)


    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = ("usage: %prog -o <subsystem> [-e <entry_name>]\n"
                 " -o, --opt    Q6 subsystem (adsp, cdsp, mpss)\n"
                 " -e, --entry  Entry point to start from (default: main)")
        description = ('Command to perform root pd debug for q6 subsystems')
        
        # Pass add_help_option = False, since this keeps the command in line
        #  with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
    
        parser.add_option("-o", "--opt",  action="store",
                        type="string", dest="dsp",
                        help="Q6 subsystem to debug (adsp, cdsp, mpss)")
        parser.add_option("-e", "--entry",  action="store",
                        type="string", dest="entry_name",
                        help="Name of the entry point. (default: main)")
        
        return parser
              
    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "Performs root pd debug for q6 subsystems"
    
    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of this command to perform root pd debug of q6 subsystems

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

         
        args = shlex.split(command)

        (options, args) = self.parser.parse_args(args)
        dsp = options.dsp
        entry = options.entry_name

        if (dsp == 'NULL' or dsp.lower() not in \
            ['adsp','cdsp','mpss','wpss']):
            logger.error("Please specify valid Q6 subsystem. \n" \
            "Options are: \n1. ADSP \n2. CDSP \n3. MPSS \n4. WPSS")
            return

        # For CLI debug if entry is not provided
        if entry is None:
            entry = "main"

        if entry.lower() not in [
            'sw_entry', 'qurtos_init', 'qurtos_root_entry', \
            'lib_qurt_init', 'main']:
            logger.error("Please specify supported entry name.\n"
                        "Valid entry names are :\n"
                        "sw_entry\n"
                        "qurtos_init\n"
                        "qurtos_root_entry\n"
                        "lib_qurt_init\n"
                        "main"
            )
            return

        if (g_dbgContext.chipsetName != None):
            chip_name = g_dbgContext.chipsetName.lower()
        else:
            logger.error("Chipset name is not provided. Please set chipset name in settings.json")
            return

        tln = None
        try:
            tln = getTelnetHandle()
            q6_module = importlib.import_module(f"lldb_pkg.target.{chip_name}.image.q6")
            debugRootPd = getattr(q6_module, 'debugRootPd')
            debugRootPd(debugger, dsp, entry, tln)
        finally:
            if tln is not None:
                closeTelnetHandle()

class initq6config:
    """!
    This class is used define a command to initialise Q6 settings and perform dynamic settings
    Usage: init -s <subsystem> -m <mode> -p <path>
    """
    """ """
    program = 'init'

    @classmethod    
    def register_lldb_command(cls, debugger, module_name):

        """!   
        This function is used to register an lldb command that
        configures and initializes openocd Q6 with dynamic settings.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  

        usage = "usage: %prog [options]"
        description = ('Command to configure openocd Q6 with dynamic settings ')
        
        # Pass add_help_option = False, since this keeps the command in line
        #  with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
        
        parser.add_option("-s", "--subsystem", action="store",
                        type="string", dest="ss")
        parser.add_option("-m", "--mode",  action="store",
                        type="string", dest="mode")
        parser.add_option("-p", "--path",  action="store",
                        type="string", dest="path")
        
        return parser


    def get_short_help(self):
        return "Command to configure and initialize openocd Q6 subsystems"


    def get_long_help(self):

        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):

        """! 
        Implementation of command to configure and initialize openocd Q6 
        subsystems.
    

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
        args = shlex.split(command)
        (options, args) = self.parser.parse_args(args)
        tln = getTelnetHandle()

        if (options.ss == 'NULL' or options.ss.lower() not in \
            ['adsp','cdsp','mpss','wpss']):
            logger.error("Please specify valid Q6 subsystem. \n" \
            "Options are: \n1. ADSP \n2. CDSP \n3. MPSS \n4. WPSS")
            return

        if (g_dbgContext.chipsetName != None):
            chip_name = g_dbgContext.chipsetName.lower()
        else:
            logger.error("Chipset name is not provided. Please set chipset name in settings.json")
            return

        q6_module = importlib.import_module(f"lldb_pkg.target.{chip_name}.image.q6")
        sendQ6ConfigCommands = getattr(q6_module, 'sendQ6ConfigCommands')
        sendQ6ConfigCommands(debugger, options.ss, tln, options.mode, options.path)

def __lldb_init_module(debugger, dict):
    """! 
    Register all classes that have a register_lldb_command method

    @param debugger         -   An instance of the SBDebugger Object that 
                                provides access to SBTargets used for 
                                debugging.

    @param unused           -   This dict provides direct access to all
                                'built-in' identifiers of Python
            
    """
    for _name, cls in inspect.getmembers(sys.modules[__name__]):
        if inspect.isclass(cls) and callable(getattr(cls,
                                                     "register_lldb_command",
                                                     None)):
            cls.register_lldb_command(debugger, __name__)


class debug_adapter:
    """!
    
    This class is used define a command to get the details of debug adapter and debug protocol.
    
    Usage: debug_adapter
    
    """

    program = 'debug_adapter'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  

        usage = "usage: %prog -d debug adapter, protocol"
        description = ('This command is used to set the adapter details. ')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        parser.add_option("-d", "--debug_adapter",  action="store",
                        type="string", dest="adapter_and_protocol")
        return parser

    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "command to get the details of debug adapter"

    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of This class is used define a command to get the
        details of debug adapter and debug protocol.

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
        dbg_adapter(debugger, command, exe_ctx)

class tz_logs:
    """!
    This class is used define a command for getting tz logs functionality
    Usage: get_tz_logs --tz tz_path -t target
    """

    program = "get_tz_logs"
    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to read tz buffer logs

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented

        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)
    
    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "usage: %prog --tz <tz_path> -t <target>"
        description = ('Command to get tz logs')
        
        # Pass add_help_option = False, since this keeps the command in line
        #  with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
            
        parser.add_option("--tz", "--buildpath",  action="store",
                        type="string", dest="tz_path")
        
        parser.add_option("-t", "--target",  action="store",
                        type="string", dest="targetDevice")
    
        return parser
    
    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "command to read tz logs"
    
    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of this command helps to retrieve Tz-logs from Trust-zone

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
        args = shlex.split(command)

        (options, args) = self.parser.parse_args(args)
        invalidInputError = "Please provide valid input arguments.\n" \
                            "Usage: get_tz_logs --tz tz_path -t target\n"  
            
        if options == None or (options.tz_path == None and options.targetDevice == None):
            logger.error (invalidInputError)
            return
        
        # Call to Parse and retrieve all Tz buffer logs from the trust zone buffer memory
        get_tz_logs(debugger, command, result, options.tz_path, options.targetDevice)

class custom_wait:
    """!
    
    This class is used define a command to add delay of a specified 
    number of seconds.
    
    Usage: custom_wait
    
    """
    program = 'custom_wait'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  

        usage = "usage: %prog -s <seconds>"
        description = ('This command is used to add a delay of a specified number of seconds')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        parser.add_option("-s", "--seconds",  action="store", default="store",
                        type="string", dest="seconds")

        return parser

    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "command to add a delay of a specified number of seconds"

    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of This class is used define a command to get the
        details of debug adapter and debug protocol.

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

        command_args = shlex.split(command)
        try :
            (options, args) = self.parser.parse_args(command_args)
            if (options == None or options.seconds == None):
                logger.error("Please provide valid input in no of seconds")
                return
            no_secs = options.seconds
            time.sleep(int(no_secs))
            logger.info("Delay of {} sec added".format(int(no_secs)))
            return

        except :
            logger.warning ("Please provide valid input arguments.\n"
            "Usage: custom_wait -s <seconds>")
            result.SetError("option parsing failed")
            return

class QuRT:
    """!
    A unified LLDB command dispatcher for QURT debug utilities.

    Usage:
        QuRT <subcommand>

    Available subcommands:
        - futexlist
        - processlist
        - mergebuffer
        - tasklist
        - interruptlist
        - debugbuffer
        - hvxlogs
        - hmxlogs
    """

    program = 'qurt'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!
        Registers the QuRT with LLDB.

        @param debugger: An instance of the SBDebugger object used for debugging.
        @param module_name: The name of the Python module where this class is defined.
        """
        command = f'command script add -c {module_name}.{cls.__name__} {cls.program}'
        debugger.HandleCommand(command)

    @classmethod
    def get_short_help(cls):
        """!
        Returns a brief description of the command.

        @return: A short help string.
        """
        return "Dispatches QURT debug commands like futexlist, processlist, etc."

    def get_long_help(self):
        """!
        Returns a detailed description of the command and its subcommands.

        @return: A long help string.
        """
        return self.help_string

    def __init__(self, debugger, unused):
        """Initializes the command object and builds dynamic help string."""
        subcommands = sorted(command_registry.keys())
        help_lines = ["qurt - A unified LLDB command dispatcher for QURT debug utilities.",
                    "",
                    "Usage:",
                    "  qurt <subcommand>",
                    "",
                    "Available subcommands:"]
        
        for cmd in subcommands:
            help_lines.append(f"  - {cmd}")

        self.help_string = "\n".join(help_lines)

    def __call__(self, debugger, command, exe_ctx, result):
        """!
        Executes the appropriate handler based on the subcommand.

        @param debugger: An instance of the SBDebugger object.
        @param command: The subcommand string passed by the user.
        @param exe_ctx: The execution context for the command.
        @param result: The result object to capture output and status.
        """
        subcommand = command.strip().lower()
        entry = command_registry.get(subcommand)

        if entry:
            func = entry['func']
            args_map = {
                'debugger': debugger,
                'logger': logger
            }

            try:
                func_args = [args_map[arg] for arg in entry['args']]
                func(*func_args)
            except KeyError as e:
                logger.error(f"Missing argument: {e}")
            except Exception as e:
                logger.error(f"Error executing {subcommand}: {e}")
        else:
            logger.info(f"Unknown subcommand: {subcommand}")


class Zephyr:
    """!
    A unified LLDB command dispatcher for Zephyr RTOS debug utilities.

    Usage:
        zephyr <subcommand>

    Available subcommands:
        - threads
        - mutexes
        - semaphores
        - timers
        - stacks
        - msgqs
        - mboxes
        - pipes
        - mem_slabs
    """

    program = 'zephyr'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!
        Registers the Zephyr command with LLDB.

        @param debugger: An instance of the SBDebugger object used for debugging.
        @param module_name: The name of the Python module where this class is defined.
        """
        command = f'command script add -c {module_name}.{cls.__name__} {cls.program}'
        debugger.HandleCommand(command)

    @classmethod
    def get_short_help(cls):
        """!
        Returns a brief description of the command.

        @return: A short help string.
        """
        return "Dispatches Zephyr RTOS debug commands like threads, mutexes, semaphores, etc."

    def get_long_help(self):
        """!
        Returns a detailed description of the command and its subcommands.

        @return: A long help string.
        """
        return self.help_string

    def __init__(self, debugger, unused):
        """Initializes the command object and builds dynamic help string."""
        subcommands = ['threads', 'mutexes', 'semaphores', 'timers', 'stacks', 
                      'msgqs', 'mboxes', 'pipes', 'mem_slabs']
        help_lines = ["zephyr - A unified LLDB command dispatcher for Zephyr RTOS debug utilities.",
                    "",
                    "Usage:",
                    "  zephyr <subcommand>",
                    "",
                    "Available subcommands:"]
        
        for cmd in subcommands:
            help_lines.append(f"  - {cmd}")

        self.help_string = "\n".join(help_lines)

    def __call__(self, debugger, command, exe_ctx, result):
        """!
        Executes the appropriate handler based on the subcommand.

        @param debugger: An instance of the SBDebugger object.
        @param command: The subcommand string passed by the user.
        @param exe_ctx: The execution context for the command.
        @param result: The result object to capture output and status.
        """
        # Call the zephyr dispatcher function from the zephyr module
        zephyr.zephyr(debugger, command, exe_ctx, result, None)


class DebugWithPil:
    """!
    This command is used to debug a sub-system with PIL.\n
    Usage :
    debugwithpil -s <subsystem> -w <waitforpil>
    """
    program = 'debugwithpil'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented

        """
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)


    @classmethod
    def create_options(cls):
        """!
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """

        usage = "usage: %prog -s <subsystem> -w <waitforpil>\n" \
                "valid subsystems are : adsp, cdsp, mpss, soccp, all\n" \
                "valid waitforpil values are : True, False\n"
        description = ('command is used to debug a sub-system with PIL')

        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        parser.add_option("-s", "--subsys",  action="store",
                                type="string", dest="subsystem")
        parser.add_option("-w", "--wait", action="store", 
                                type= "string", dest="wait")
        return parser


    def get_short_help(self):
        """!
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """
        return "command to debug a sub-system with PIL"


    def get_long_help(self):
        """!
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """!
        Implementation of the command is used to debug a sub-system with PIL.

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

        args = shlex.split(command)
        (options, args) = self.parser.parse_args(args)

        valid_subsystems = {"adsp", "cdsp", "mpss", "wpss", "soccp", "all"}

        if options.subsystem is None or options.wait is None:
            logger.error("Missing required arguments.")
            logger.info(self.help_string)
            return

        if options.subsystem.lower() not in valid_subsystems:
            logger.error("Invalid subsystem argument: %s \nPlease check help for correct usage.\n ", options.subsystem)
            return

        if options.wait not in {"True", "False"}:
            logger.error("Invalid wait argument: %s \nPlease check help for correct usage.\n", options.wait)
            return

        if (g_dbgContext.chipsetName != None):
            chip_name = g_dbgContext.chipsetName.lower()
        else:
            logger.error("Chipset name is not provided. Please set chipset name in settings.json")
            return

        pil_module = importlib.import_module(f"lldb_pkg.target.{chip_name}.image.debugwithpil")
        pil_debugger = getattr(pil_module, 'pil_debugger')
        pil_debugger(debugger, command, result, options.subsystem, options.wait)

class runpyscript:
    """! 
    This class is used to define command to facilitate running python script directly in lldb. 
    Usage: runscript <script file path> [<arg1> <arg2> ...]
    
    """
    program = 'runscript'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  


        usage = "runscript <script file path> [<arg1> <arg2> ...]"
        description = ('Run python script directly in lldb')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
        return parser

    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  
        return "Command to run python script directly in lldb"

    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string


    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()


    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        This command is meant to collect the pmic dump of all pmics register

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
        args = shlex.split(command)
        
        if not args:
            logger.error("Usage: runscript <script file path> [<arg1> <arg2> ...]")
            return
        
        if (os.path.exists(args[0])):
            pass
        else:
            logger.error("python script not found please check the file path")

        target = debugger.GetSelectedTarget()
        process = target.GetProcess()

        if not process or not process.IsValid():
            logger.error("Error: No valid process")
            return

        sys.argv = [{', '.join(repr(a) for a in args)}]

        debugger.HandleCommand ("script import sys")
        debugger.HandleCommand ("script __name__='__main__'")
        debugger.HandleCommand (f"script sys.argv = [{', '.join(repr(a) for a in args)}]")
        debugger.HandleCommand ('script exec(open(sys.argv[0]).read())')

class hypervisor_extension:
    """!
    This command is used to get the hypervisor log buffer info
    Usage : \n
    hypervisor logs        :  hyp_ext hyp_log
    hypervisor smc logs    :  hyp_ext hyp_smc_log
    hyp debug info         :  hyp_ext hyp_debug_info
    hyp dump table         :  hyp_ext hyp_dump_table 
    hyp dump table         :  hyp_ext qhee_smc_log
    hyp dump table         :  hyp_ext qhee_smmu_operations
    """
    program = 'hyp_ext'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  

        usage = "\n%prog hyp_log\n%prog hyp_smc_log\n%prog hyp_debug_info\
            \n%prog hyp_dump_table\n%prog qhee_smc_log\n%prog qhee_smmu_operations"
        description = ('This command is used to get the hypervisor log buffer info')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        # parser.add_option("-s", "--seconds",  action="store", default="store",
        #                 type="string", dest="seconds")

        return parser

    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "This command is used to get the hypervisor log buffer info"

    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of This class is used define a command to get the
        hypervisor log buffer logs.

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

        command_args = shlex.split(command)
        interpreter = debugger.GetCommandInterpreter()
        result = lldb.SBCommandReturnObject()
        try :
            sel_target = debugger.GetSelectedTarget()
            current_modules = int(sel_target.GetNumModules()) - 1
            module = sel_target.modules[current_modules]
            file_spec = module.GetFileSpec()
            module_name = file_spec.GetFilename()
            no_of_args = len(command_args)

            if module_name == "hypvm.elf":
                pass
            else:
                logger.error("hypervisor symbols are not loaded")
                return

            if command_args[0] == "hypervisor_log" or command_args[0] == "hyp_log":
                hypervisor_log(interpreter, result, sel_target, module)

            elif command_args[0] == "hyp_smc_log" and no_of_args >= 1:
                if no_of_args == 1:
                    # hyp_smc_log
                    hypervisor_smc_logs(interpreter, result)
                elif no_of_args == 2 and command_args[1].lower() == "more":
                    # hyp_smc_log more
                    hypervisor_smc_logs(interpreter, result, "more")
                else:
                    logger.error("Invalid argument. Use 'more' for complete hyp_smc_log log")

            elif command_args[0] == "hyp_debug_info":
                hypervisor_debug_info(interpreter, result)

            elif command_args[0] == "hyp_dump_table" and no_of_args >= 1:
                if no_of_args == 1:
                    hypervisor_dump_table(interpreter, result)
                elif no_of_args == 2 and command_args[1].lower() == "more":
                        hypervisor_dump_table(interpreter, result, command_args[1].lower())
                else:
                    logger.error("Invalid argument. Use 'more' for complete hyp_dump_table log")
            
            elif command_args[0] == "qhee_smc_log":
                qhee_smc_log(interpreter, result)

            elif command_args[0] == "qhee_smmu_operations" and no_of_args >= 1:
                if no_of_args == 1:
                    qhee_smmu_operations(interpreter, result)
                elif no_of_args == 2 and command_args[1].lower() == "more":
                    qhee_smmu_operations(interpreter, result, command_args[1].lower())
                else:
                    logger.error("Invalid argument. Use 'more' for complete qhee_smmu_operations log")
        except :
            logger.warning ("Please provide valid input arguments.\n"
            "Usage: hyp_ext <name of the log>")
            result.SetError("option parsing failed")
            return

class tmeLogs:
    """!
    This command is used to collect and parse TME logs.
    Usage:
    tme_logs <chipset> <subsystem> [output_path] [dict_path]
    Example: tme_logs poros tme
    Example: tme_logs poros tme C:\\custom\\path
    """
    program = 'tme_logs'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!
        Register the tme_logs command with LLDB.
        """
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!
        Create and return the option parser for this command.
        """
        usage = "usage: %prog <chipset> <subsystem> [output_path] [dict_path]"
        description = ('Collect and parse TME logs from memory')

        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        return parser

    def get_short_help(self):
        """!
        Return brief help text for this command.
        """
        return "Collect and parse TME logs"

    def get_long_help(self):
        """!
        Return detailed help text for this command.
        """
        return self.__doc__

    def __init__(self, debugger, unused):
        """!
        Initialize the command object.
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """!
        Execute the tme_logs command.

        @param debugger         LLDB debugger instance
        @param command          Command string with arguments
        @param exe_ctx          Execution context
        @param result           Result object for output
        """
        args = [arg.lower() for arg in shlex.split(command)]

        if len(args) < 2:
            logger.error("Usage: tme_logs <chipset> <subsystem> [output_path] [dict_path]")
            logger.error("Example: tme_logs poros tme")
            return

        chipset = args[0]
        subsystem = args[1]
        output_path = args[2] if len(args) > 2 else None
        dict_path = args[3] if len(args) > 3 else None

        if chipset != g_dbgContext.chipsetName.lower():
            logger.error("Please provide a valid chipset name.")
            return
        try:
            tme_logs_command(debugger, chipset, subsystem, output_path, dict_path)
        except Exception as e:
            logger.error(f"Failed to execute tme_logs command: {e}")

class symbol_load:
    """!
    This command is used to load symbols
    Usage : \n
    TZ       :  symbol_load tz aslr/noaslr
    HYP      :  symbol_load hyp aslr/noaslr
    UEFI     :  symbol_load uefi
    """
    program = 'symbol_load'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!   
        This function is used to register user defined commands so that it
        can be used for debug via lldb.

        @param cls              -   Represents the current class defined for
                                    command registration

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        @param module_name      -   Represents python script file where the
                                    command is implemented


        """    

        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        
        # Add any commands contained in this module to LLDB
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!   
        This function is used give input arguments used by the command as 
        options.

        @param cls              -   Represents the current class defined for
                                    command registration

        @return  returns a optparse.OptionParser object that is used for
        giving input arguments used by the command as options   

        """  

        usage = "\n%prog tz aslr/noaslr\n%prog hyp aslr/noaslr\n%prog uefi"
        description = ('This command is used to load symbols')
        
        # Pass add_help_option = False, since this keeps the command in line
        # with lldb commands, and we wire up "help command" to work by
        # providing the long & short help methods below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)

        # parser.add_option("-s", "--seconds",  action="store", default="store",
        #                 type="string", dest="seconds")

        return parser

    def get_short_help(self):
        """!   
        This function is to return a brief text message that helps understand
        the utility of the command.

        @return Returns a string that describes usage of the command

        """  

        return "This command is used to get the hypervisor log buffer info"

    def get_long_help(self):
        """!   
        This function returns a description of options and their usage with
        the syntax.

        @return Returns a text description of options and their usage with
        the syntax.

        """  
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger         -   An instance of the SBDebugger Object that 
                                    provides access to SBTargets used for 
                                    debugging.
        
        @param unused           -   This dict provides direct access to all
                                    'built-in' identifiers of Python
        
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """! 
        Implementation of This class is used define a command to load
        symbols for different images.

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

        command_args = shlex.split(command)
        interpreter = debugger.GetCommandInterpreter()
        result = lldb.SBCommandReturnObject()
        chip_name = g_dbgContext.chipsetName.lower()
        try :
            args = len(command_args)
            if args >= 1 and command_args[0].lower() == "tz":
                import_str = f"lldb_pkg.target.{chip_name}.image.{command_args[0].lower()}"
                image_script_module = importlib.import_module(import_str)
                debugTZ = getattr(image_script_module, 'debugTZ')
                debugTZ(debugger, command, exe_ctx, result)

            if args >= 1 and command_args[0].lower() == "hyp":
                import_str = f"lldb_pkg.target.{chip_name}.image.{command_args[0].lower()}"
                image_script_module = importlib.import_module(import_str)
                debugHYP = getattr(image_script_module, 'debugHYP')
                debugHYP(debugger, command, exe_ctx, result)
        except :
            logger.warning ("Please provide valid input arguments.\n"
            "Usage: symbol_load <image>")
            result.SetError("option parsing failed")
            return


class QurtThreadLabel:
    """!
    QuRT thread-label beautifier installer.

    Usage: qurt_labels_install

    Command class (framework convention) that lives in openocd_lldb.py like
    every other command. The actual logic lives in the arch/tech module
    lldb_pkg.tech.QuRT.OSAM.qurt_thread_label; both register_lldb_command()
    and __call__() delegate to install_qurt_thread_label(), passing that
    module's OWN import path -- LLDB resolves the ${script.thread:...} token,
    the `target stop-hook -P`, and the `command script add -f` targets
    through the defining module's script-interpreter namespace, so the path
    must point at the implementation module, not this file.

    Registering this class installs, on load, the QuRT-aware thread-format
    beautifier, the stop-hook that pushes labels into VS Code, and the
    `qurtLabels` / `qurtstack` helper commands. Running the `qurt_labels_install`
    command re-runs that installation on demand.
    """
    program = 'qurt_labels_install'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!
        Register the qurt_labels_install command.

        @param cls          -   The command class being registered.
        @param debugger     -   The SBDebugger instance.
        @param module_name  -   Import path of THIS module (openocd_lldb).
        """
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!
        Build the option parser for the command (no options; takes no args).

        @param cls          -   The command class.
        @return             -   An optparse.OptionParser object.
        """
        usage = "usage: %prog"
        description = ('(Re)install the QuRT thread-label beautifier, stop-hook, '
                       'and the qurtLabels / qurtstack helper commands.')
        # add_help_option=False keeps this in line with the other commands,
        # which wire help up via get_short_help/get_long_help below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
        return parser

    def get_short_help(self):
        """!
        @return  A brief one-line description of the command.
        """
        return "(re)install QuRT thread labels + qurtLabels/qurtstack commands"

    def get_long_help(self):
        """!
        @return  The full option/usage help text.
        """
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger     -   The SBDebugger instance.
        @param unused       -   The 'built-in' identifiers dict (unused).
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """!
        Re-run the QuRT thread-label installation on demand.

        @param debugger     -   The SBDebugger instance.
        @param command      -   The command argument string (none expected).
        @param exe_ctx      -   The execution context object.
        @param result       -   The command return object.
        """
        qurt_thread_label.install_qurt_thread_label(
            debugger, qurt_thread_label.__name__)
        result.AppendMessage("[qurt_thread_label] thread-label beautifier, "
                             "stop-hook, and qurtLabels/qurtstack (re)installed")


class QurtPushOffsets:
    """!
    DWARF-driven QuRT TCB-offset pusher installer.

    Usage: qurt_push_offsets_install

    Command class (framework convention) that lives in openocd_lldb.py like
    every other command. The actual logic lives in the arch/tech module
    lldb_pkg.tech.QuRT.OSAM.push_qurt_offsets; both register_lldb_command()
    and __call__() delegate to install_push_qurt_offsets(), passing that
    module's OWN import path -- LLDB resolves the `target stop-hook -P` and
    `command script add -f` targets through the defining module's
    script-interpreter namespace, so the path must point at the implementation
    module, not this file.

    Registering this class installs, on load, a target stop-hook that
    auto-fires on the first stop and pushes the ELF's DWARF-resolved SIZEOF_TCB
    and per-field CONTEXT_* offsets into the OpenOCD `qurt` RTOS backend (via
    the `hexagon qurtSetOffsets` monitor command), plus the manual
    `hexagonPushQurtOffsets` helper command. This makes the SW-thread walk
    per-build correct with no hardcoded offsets in the C driver. Running the
    `qurt_push_offsets_install` command re-runs that installation on demand.
    """
    program = 'qurt_push_offsets_install'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!
        Register the qurt_push_offsets_install command.

        @param cls          -   The command class being registered.
        @param debugger     -   The SBDebugger instance.
        @param module_name  -   Import path of THIS module (openocd_lldb).
        """
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!
        Build the option parser for the command (no options; takes no args).

        @param cls          -   The command class.
        @return             -   An optparse.OptionParser object.
        """
        usage = "usage: %prog"
        description = ('(Re)install the DWARF-driven QuRT TCB-offset pusher '
                       'stop-hook and the hexagonPushQurtOffsets command.')
        # add_help_option=False keeps this in line with the other commands,
        # which wire help up via get_short_help/get_long_help below.
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
        return parser

    def get_short_help(self):
        """!
        @return  A brief one-line description of the command.
        """
        return "(re)install the DWARF-driven QuRT TCB-offset pusher"

    def get_long_help(self):
        """!
        @return  The full option/usage help text.
        """
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger     -   The SBDebugger instance.
        @param unused       -   The 'built-in' identifiers dict (unused).
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """!
        Re-run the QuRT TCB-offset pusher installation on demand.

        @param debugger     -   The SBDebugger instance.
        @param command      -   The command argument string (none expected).
        @param exe_ctx      -   The execution context object.
        @param result       -   The command return object.
        """
        push_qurt_offsets.install_push_qurt_offsets(
            debugger, push_qurt_offsets.__name__)
        result.AppendMessage("[push_qurt_offsets] stop-hook and "
                             "hexagonPushQurtOffsets command (re)installed")


class HexagonUtilsLoad:
    """!
    Registers the `hexagon_utils_load` LLDB command, which finds a Hexagon
    Tools install and `command script import`s hexagon_utils.py (providing
    tlb / pagetable / pv* / dvf) under an LLDB that did not already load it.

    Usage: hexagon_utils_load

    Command class (framework convention) in openocd_lldb.py; the logic lives
    in lldb_pkg.arch.q6.hexagon_utils_loader. Loading is explicit so importing
    openocd_lldb.py for a non-Hexagon target has no Hexagon side effects.
    """
    program = 'hexagon_utils_load'

    @classmethod
    def register_lldb_command(cls, debugger, module_name):
        """!
        Register the hexagon_utils_load command.

        @param cls          -   The command class being registered.
        @param debugger     -   The SBDebugger instance.
        @param module_name  -   Import path of THIS module (openocd_lldb).
        """
        parser = cls.create_options()
        cls.__doc__ = parser.format_help()
        command = 'command script add -c %s.%s %s' % (module_name,
                                                      cls.__name__,
                                                      cls.program)
        debugger.HandleCommand(command)

    @classmethod
    def create_options(cls):
        """!
        Build the option parser for the command (no options; takes no args).

        @param cls          -   The command class.
        @return             -   An optparse.OptionParser object.
        """
        usage = "usage: %prog"
        description = ('Locate a Hexagon Tools install and import hexagon_utils.py '
                       '(tlb / pagetable / pv* / dvf) if not already loaded.')
        parser = optparse.OptionParser(
            description=description,
            prog=cls.program,
            usage=usage,
            add_help_option=False)
        return parser

    def get_short_help(self):
        """!
        @return  A brief one-line description of the command.
        """
        return "load Hexagon Tools helper commands (tlb / pagetable / pv* / dvf)"

    def get_long_help(self):
        """!
        @return  The full option/usage help text.
        """
        return self.help_string

    def __init__(self, debugger, unused):
        """!
        The class command object base class initializer.

        @param debugger     -   The SBDebugger instance.
        @param unused       -   The 'built-in' identifiers dict (unused).
        """
        self.parser = self.create_options()
        self.help_string = self.parser.format_help()

    def __call__(self, debugger, command, exe_ctx, result):
        """!
        Load hexagon_utils.py on demand.

        @param debugger     -   The SBDebugger instance.
        @param command      -   The command argument string (none expected).
        @param exe_ctx      -   The execution context object.
        @param result       -   The command return object.
        """
        if hexagon_utils_loader.load_hexagon_utils(debugger):
            result.AppendMessage("[hexagon_utils_load] Hexagon utilities loaded "
                                 "(tlb / pagetable / pv* / dvf)")
        else:
            result.AppendMessage("[hexagon_utils_load] Hexagon Tools not found "
                                 "or import failed")


initDebug("", 0)
