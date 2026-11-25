#===========================================================================
#  Copyright (c) Qualcomm Technologies, Inc.
#  All Rights Reserved.
#  Qualcomm Technologies Proprietary and Confidential
#===========================================================================

import argparse
import collections
import glob
import os.path
import re
import subprocess
import sys
import traceback

from sys import argv
from tempfile import gettempdir

# parsed cmdline args
args = None

def dprint(obj):
  if args.debug:
    print("[DEBUG] " + str(obj))

#################################################################################
# Functions for augmenting symbol information on callstack logs on TA crash
#################################################################################
# current TA on log stream
g_cur_ta_name = None

# list of (vaddr, size, elfpath)
#  ex> [(1000, 3000, './somewhere/cmnlib.pbn'), (30000, 32000, './somewhere/smplap64.elf')]
g_elf_info = []

def ta_elf_path(name_to_find):
  for x in args.elf:
    try:
      (ta_name, elf_path) = x.split('=')
      if ta_name == name_to_find:
        return elf_path
    except Exception as e:
      dprint("Error on parsing elf arguments : " + x)

  return None

def cmnlib_elf_path():
  # prefer user input. then, find from TZ build path if not specified
  cmnlib_in_arg = ta_elf_path('cmnlib')
  if cmnlib_in_arg:
    return cmnlib_in_arg

  # don't use '**' to keep it python2 compatible
  path_to_find=os.path.join(args.tz, 'trustzone_images', 'ssg','bsp','trustzone','qsapps', 'commonlib', 'build', '*', '*', '*', 'cmnlib.pbn')
  founds = glob.glob(path_to_find)

  if founds:
    return founds[0]

  # one more round for different dir structure
  path_to_find=os.path.join(args.tz, 'trustzone_images', 'ssg','bsp','trustzone','qsapps', 'commonlib', 'build', '*', '*', 'cmnlib.pbn')
  founds = glob.glob(path_to_find)

  if founds:
    return founds[0]

  return None

# caching external commmand result
g_command_cached = {}

# get output for external command, cached
def run_command(cmd_args):
  dprint("@run_command: " + str(cmd_args))

  if cmd_args in g_command_cached.keys():
    return g_command_cached[cmd_args]

  output = subprocess.check_output(cmd_args)

  if not output:
    dprint("failed to run proess: " + str(cmd_args))
    return None

  output = output.decode('utf-8')

  g_command_cached[cmd_args] = output

  return output

# returns function name + offset (inside function) pointed by addr.
# for example, "printf+0x10" when addr points 0x10th byte of printf code
def get_symbol_info(elf_path, addr):
  try:
    nm_out = run_command(('nm', '-n', '-S', elf_path))

    for line in nm_out.splitlines():
      parts = line.split()
      if len(parts) != 4:
        continue

      symbol_addr = int(parts[0], 16)
      symbol_size = int(parts[1], 16)
      symbol_name = parts[3]

      if symbol_addr <= addr and addr < (symbol_addr + symbol_size):
        return "%s+0x%x" % (symbol_name, addr - symbol_addr)

  except Exception as e:
    if args.debug:
      traceback.print_exc()
      raise e
    return "['nm' failed. Make sure it's in PATH]"

  return "[symbol not found on addr 0x%x]" % (addr)

# source code/line info for code at addr provided by addr2line
# for example, "path/../cmnlib_mink.c:131"
def get_src_info(elf_path, addr):
  try:
    adl_out = run_command(('addr2line', '-e', elf_path, hex(addr)))

    return adl_out.strip()

  except Exception as e:
    if args.debug:
      traceback.print_exc()
      raise e
    return "['addr2line' failed. Make sure it's in PATH]"

  return "['addr2line' can't find src]"

# additionally logged symbol and src info string
def get_symbol_and_src_info(addr):
  for ei in g_elf_info:
    (ei_load_addr, ei_size, ei_elf_path) = ei

    if ei_load_addr <= addr and addr < (ei_load_addr + ei_size):
      rel_addr = addr - ei_load_addr
      return " => " + get_symbol_info(ei_elf_path, rel_addr) + " at " + get_src_info(ei_elf_path, rel_addr)

  return " => Elf isn't provided or invalid address"

# collect TA name mapping information for printing symbol information on callstack log
def collecting_info_for_symbolinfo_on_callstack(str):
  # (only when symbol option (--elf) is spiecified)
  if args.elf:
    global g_elf_info
    global g_cur_ta_name
    global g_command_cached

    m = re.search("TZBSP_MINK_APP_FAULT_NAME_STRING.*Name: ([^\)]+)\)", str)
    if m:
      g_cur_ta_name = m.group(1)
      dprint("@collecting_info, extracted ta name: %s" % (g_cur_ta_name))

      # reset db on new TA crash log
      g_elf_info = []
      g_command_cached = {}

    m = re.search("TZBSP_MINK_APP_KMEMMAP_INFO.*usage: *([0-9a-fA-Fx]+).*vaddr: *([0-9a-fA-Fx]+).*size: *([0-9a-fA-Fx]+).*perm: *([0-9a-fA-Fx]+)", str)
    if m:
      usage = int(m.group(1), 16)
      vaddr = int(m.group(2), 16)
      size = int(m.group(3), 16)
      perm = int(m.group(4), 16)
      dprint("@collecting_info, extracted memmap : %x  vaddr: %x  size: %x perm: %x" % (usage, vaddr, size, perm))
      # if usage is cmnlib or elf and executable
      if usage in [1, 2] and perm == 5:
        if usage == 2:
          elf_path = cmnlib_elf_path()
        else:
          elf_path = ta_elf_path(g_cur_ta_name)

        if elf_path:
          if os.path.isfile(elf_path) and os.access(elf_path, os.R_OK):
            g_elf_info.insert(0, (vaddr, size, elf_path))
          else:
            dprint("Elf file not found or not accessible : %s" % (elf_path))

# printing symbol information on callstack log
def post_process_augmenting_symbolinfo_on_callstack(str):
  # only when symbol option is specified
  if args.elf:
    m = re.search("TZBSP_MINK_APP_FAULT_LOGCS_.*:([0-9a-fA-Fx]+)\)", str)
    if m:
      addr = int(m.group(1), 16)
      aug_str = get_symbol_and_src_info(addr)

      # insert at the end of string, right before newlines
      str = re.sub(r"([\n\r]*$)", aug_str + r"\1", str, count = 1)
  return str

#################################################################################
# Post processing and output final logs.
#
# Collecting-info and post-processing is done line by line.
#################################################################################

# Collecting useful information for post processing
def collecting_info(str):
  collecting_info_for_symbolinfo_on_callstack(str)

  # new collecting can be added here

# Augment logs with additional information
def post_process(str):
  str = post_process_augmenting_symbolinfo_on_callstack(str)

  # new post_process can be added here

  return str

def process_output(outFile, args, str):
  # Try our best to suppress error unless debug enabled (not to break any)
  # (Not surprisingly, input is not normalized)
  try:
    collecting_info(str)
    str = post_process(str)
  except Exception as e:
    if args.debug:
      traceback.print_exc()
      raise e

  outFile.write(str)
  if args.stdout==True:
    print(str)

#################################################################################
# main
#################################################################################
try:
  parser_args = argparse.ArgumentParser(description='Process the input log file details.')
  optional = parser_args._action_groups.pop()
  required = parser_args.add_argument_group("required arguments")
  #check the arguments passed by user.
  required.add_argument('-t','--tz',
                      help='Location of tz build to pick dictionary',
                      required=True)
  required.add_argument('-l','--log',
                      help='Absolute Location of log file. "-" for standard input',
                      type=argparse.FileType('r'),
                      required=True)
  optional.add_argument('-e','--errs',
                      help='Location of error code dictionary')
  optional.add_argument('-o','--output',
                      help='Absolute Location of output file')
  optional.add_argument('-s','--stdout',
                      help='Option to disable printing on standard out. This is true by default. Pass False to disable',
                      default=True)
  optional.add_argument('-f','--elf',
                      metavar='ta_name=ta_name.elf',
                      nargs='+',
                      help='Option to specify TA elf file(s) for printing symbol information on callstack in TA crash log',
                      default=[])
  optional.add_argument('-d','--debug',
                      help='Option to print debug information on standard out.',
                      action='store_true')

  parser_args._action_groups.append(optional)

  args = parser_args.parse_args()

  if args.tz is None and args.errs is None or args.log is None :
    parser_args.print_help()
    exit()
  if(os.path.isdir(args.tz)==False):
    raise argparse.ArgumentTypeError("{0} is not a valid path".format(args.tz))
  if(os.access(args.tz,os.R_OK)==False):
    raise argparse.ArgumentTypeError("{0} is not readable directory".format(args.tz))

  if args.errs is None :
    dict_txt=os.path.join(args.tz,'trustzone_images', 'ssg','bsp','qsee','errorCodesDict.txt')
  else :
    dict_txt=args.errs

  if args.output is None :
    output_txt=os.path.join(gettempdir() , 'TZ_LOG_STR.txt')
  else :
    output_txt=args.output

  with args.log as logFile, open(output_txt, 'w') as outFile, open(dict_txt, 'r') as errCodesDictFile :

    '''create a collection from dictionary the first element is error code second element is the list containing value of error code and string.'''
    errCodeDict = collections.defaultdict(list)
    errorCodeDesc = {}
    errFatalDict = dict()
    fatal_codes = False
    errFatalMacroName = 'TZBSP_KRNL_FATAL_ERR_TYPE'

    for line in errCodesDictFile:
      '''generate a dictionary for error fatal codes.'''
      if  "Start Error Fatal Codes" in line:
        if 'for' in line:
          errFatalMacroName = line.split('for')[1].split('+')[0].strip()
        fatal_codes = True
      if  "End Error Fatal Codes" in line:
        fatal_codes = False
      if (fatal_codes and ':' in line):
        errFatalDict[line.split(':')[0].rstrip("\r\n")] =  line.split(':')[1].rstrip("\r\n")

      '''generate Error code dictionary.'''
      if ':' in line and (not fatal_codes)  :
        (errorCodeNumber,errorCodeString,errorCodeDesc)= line.strip().split(':',2)
        errCodeDict[errorCodeNumber].append(errorCodeString)
        if (errorCodeDesc) :
          errCodeDict[errorCodeNumber].append(errorCodeDesc)
    '''end creating a collection from dictionary--> errCodeDict
    write to the new file, converted strings
    each line is --> (600003a 10e 76048 0 100 0 0 20 ffffffff00000020 0)'''
    for line in logFile :
      line =line.strip()
      line = line.rstrip("\r\n")
      if '](' in line:
        timestamp=line.split('](')[0]
        line='('+  line.split('](')[1]
      '''if line has ( and ) at start and end we process it else print as it is'''
      if line[:1]=='(' and line[-1]==')' :
        errorCodeInLog=line.strip().split('(')[1]
        '''split each line with space to get list of error codes and args'''
        if ' ' in errorCodeInLog :
          arguments = line.replace(')', '').split(' ')
          errorCodeInLog = arguments[0]
          errorCodeInLog = errorCodeInLog.replace('(', '').strip()
          arguments.pop(0)
        else :
          arguments=')'

        '''remove any ) or space if present in errorCode'''
        errorCodeInLog = errorCodeInLog.replace(')', '').strip()
        '''list of format specifiers in C'''
        formatSpecifiers=['%X', '%x', '%c', '%d','%e', '%f', '%g', '%o', '%p',  '%u' , '%08x', '%16x', '%s']
        '''map all formart specifiers to %s, since we replace all format specifiers with %s'''
        mapping = { '%X':'%s', '%x':'%s', '%c':'%s', '%d':'%s','%e':'%s', '%f':'%s', '%g':'%s', '%o':'%s', '%p':'%s',  '%u':'%s', '%08x':'%s','%16x':'%s'}
        ''' get the error code value from dictionary -->errCodeDict[errorCodeInLog])'''
        if errorCodeInLog in errCodeDict :
          if (len(errCodeDict[errorCodeInLog]) >1) :
            stringValue=errCodeDict[errorCodeInLog][1].replace('\n', '')
          else:
            stringValue=' '
            stringValueOrig=''

          ''' if any format specifier is present convert it to %s and replace with arguments'''
          if any(fmtSpecifier in stringValue for fmtSpecifier in formatSpecifiers):
            stringValueOrig=stringValue
            for k, v in mapping.items():
              stringValue = stringValue.replace(k, v)
            ''' if count of format specifiers is not equal then don't error out hence check is added.'''
            if stringValue.count('%s')==len(arguments) :
              stringValue=stringValue % tuple(arguments)
              stringValue=" "+ stringValue
            else :
              argsVal= " ".join((str(p) for p in arguments) )
              argsVal=argsVal.replace('\n', '')
              stringValue=" "+ argsVal +'  '+stringValueOrig

          if  stringValue == ' ' and    len(arguments)>0 :
            argsVal= " ".join((str(p) for p in arguments) )
            argsVal=argsVal.replace('\n', '')
            stringValue=" "+ argsVal +'  '+stringValueOrig

          '''Process error fatal codes'''
          if errFatalMacroName in errCodeDict[errorCodeInLog][0] and ':' in stringValue:
            errFatalCode =  stringValue.split(':')[1].strip()
            errFatalCode = int(errFatalCode,16)
            if str(errFatalCode) in errFatalDict :
              stringValue = stringValue.split(':')[0]+ ':' +errFatalDict[str(errFatalCode)]

          process_output(outFile, args, (timestamp +']('+errCodeDict[errorCodeInLog][0]+stringValue +')'+'\n'))
        else :
          process_output(outFile, args, (timestamp +']'+line+ '\n'))
      else:
        process_output(outFile, args, (line+ '\n'))

    print("The output file is at: "+outFile.name)

    exit(0)

except Exception as e:
  #taceback.print_exc(file = sys.stderr)
  print(e)
  exit(1)
