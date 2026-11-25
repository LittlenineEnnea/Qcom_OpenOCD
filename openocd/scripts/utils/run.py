#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file run.py
#
# @section Description
# This file contains a script to start a Python Window that runs OpenOCD.

import argparse
import os
import sys
import platform
from debug_context import logger
from time import sleep

def main():

    """!
    This function is used to start the openocd window                                                                        

    """

    argparser = argparse.ArgumentParser(
    usage=' %(prog)s <openocd-config-file>',
            description="Start OpenOCD with supplied config file in a loop",
            add_help=True,
            prog='run.py')
    argparser.add_argument('cfgFile',
            nargs='?', default=None,
            help='OpenOCD config file')
    argparser.add_argument('chipsetName',
            nargs='?', default=None,
            help='chipset info')
    argparser.add_argument('dbgInterface',
            nargs='?', default=None,
            help='chipset info')
    
    args = argparser.parse_args()

    if not args.cfgFile:
        argparser.print_help()
        sys.exit(0)
       
    logger.info ("Config file = " + args.cfgFile) 
    os_name = platform.system()
    try:
        openocdDir = os.environ['OPENOCD_DIR']
    except:
        if os_name != 'Darwin':
            logger.warning("Please set OPENOCD_DIR environment variable")
            return
        # macOS: default to <package>/bin, relative to this script
        openocdDir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'bin')

    if (os_name == 'Windows'):
        dbgInterfaceFilePath = openocdDir.replace("\\bin","")
        # cmd = '{}/openocd.exe -c "set _CHIPSET {}" -f {}/config/interface/{} -f {} -d4'.format (openocdDir, dbgInterfaceFilePath, args.dbgInterface, args.cfgFile)
        cmd = '{}/openocd.exe -f {}/config/interface/{} -f {}'.format (openocdDir, dbgInterfaceFilePath, args.dbgInterface, args.cfgFile)
    elif (os_name == 'Linux'):
        dbgInterfaceFilePath = openocdDir.replace("bin","")
        cmd = '{}/openocd -f {}/config/interface/{} -f {}'.format (openocdDir, dbgInterfaceFilePath, args.dbgInterface, args.cfgFile)
    elif (os_name == 'Darwin'):
        dbgInterfaceFilePath = os.path.dirname(openocdDir.rstrip('/'))
        cmd = '"{}/openocd" -f "{}/config/interface/{}" -f "{}"'.format (openocdDir, dbgInterfaceFilePath, args.dbgInterface, args.cfgFile)

    while (True):
        os.system(cmd)
        if (os_name in ('Linux', 'Darwin')):
            sleep(0.2)
main()
