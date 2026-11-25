#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file hyp_extension.py
#
# @section Description
# This file contains script to dump hypervisor log buffer logs

import lldb
from utils.debug_context import logger

def hypervisor_log(interpreter, result, sel_target, module):
    """!
    Reads and prints the hypervisor log buffer from target memory.

    Looks up the symbol 'hyp_log_buffer' in the provided LLDB module,
    resolves its load address in the selected target, and issues a
    formatted memory-read command to dump the log contents as a string.

    @param interpreter  -   command interpreter to run
                            command over lldb.
    @param result       -   command interpreter result object
                            that receives the output and status.
    @param sel_target   -   lldb.SBTarget representing the currently
                            selected debug target, used to resolve
                            the symbol load address.
    @param module       -   lldb.SBModule in which to search for the
                            'hyp_log_buffer' symbol.

    """
    syms = module.FindSymbols("hyp_log_buffer")
    if len(syms) == 0:
        logger.error("Symbol 'hyp_log_buffer' not found")
        return

    sym = syms[0].symbol

    # Get the address/value correctly
    addr = sym.GetStartAddress().GetLoadAddress(sel_target)
    addr = hex(addr)

    cmd = f"memory read -f s {addr}"
    interpreter.HandleCommand(cmd, result)
    # Check status
    if result.Succeeded():
        out = result.GetOutput()
        err = result.GetError()
        print(out if out else "[hyp_ext] <no stdout>")
        if err:
            logger.error("[hyp_ext] stderr:")
            logger.error(err)
    else:
        logger.error("[hyp_ext] command failed:")
        logger.error(result.GetError())

def hypervisor_smc_logs(interpreter, result, input_arg = None):
    """!
    Dumps the hypervisor SMC (Secure Monitor Call) log variable.

    Executes a target-aware variable inspection command on 'hyp_smc_log'.
    When 'input_arg' is provided, the '--show-all-children' flag is
    appended to reveal the full log contents. Otherwise only a summary
    is printed and the user is advised to re-run with the flag.

    @param interpreter  -   command interpreter to run
                            command over lldb.
    @param result       -   command interpreter result object
                            that receives the output and status.
    @param input_arg    -   optional argument; when truthy, enables full
                            child expansion of the log variable.
                            Defaults to None.

    """
    if input_arg:
        interpreter.HandleCommand("ta v hyp_smc_log --show-all-children", result)
    else:
        interpreter.HandleCommand("ta v hyp_smc_log", result)
        logger.info("Run hyp_ext hyp_smc_log more for complete log")        

    # Check status
    if result.Succeeded():
        out = result.GetOutput()
        err = result.GetError()
        print(out if out else "[hyp_ext] <no stdout>")
        if err:
            logger.error("[hyp_ext] stderr:")
            logger.error(err)
    else:
        logger.error("[hyp_ext] command failed:")
        logger.error(result.GetError())

def hypervisor_debug_info(interpreter, result):
    """!
    Dumps the hypervisor debug information structure.

    Executes a target-aware variable inspection command on
    'hyp_debug_info' and prints the resulting output. Errors
    encountered during command execution are forwarded to the logger.

    @param interpreter  -   command interpreter to run
                            command over lldb.
    @param result       -   command interpreter result object
                            that receives the output and status.

    """
    interpreter.HandleCommand("ta v hyp_debug_info", result)

    # Check status
    if result.Succeeded():
        out = result.GetOutput()
        err = result.GetError()
        print(out if out else "[hyp_ext] <no stdout>")
        if err:
            logger.error("[hyp_ext] stderr:")
            logger.error(err)
    else:
        logger.error("[hyp_ext] command failed:")
        logger.error(result.GetError())

def hypervisor_dump_table(interpreter, result, input_arg = None):
    """!
    Dumps the hypervisor dump table from the target.

    Dereferences and inspects the 'hyp_dump_table' pointer variable.
    When 'input_arg' is provided, the '--show-all-children' flag is
    appended to expand all nested fields. Otherwise a summary is
    printed and the user is advised to re-run with the flag.

    @param interpreter  -   command interpreter to run
                            command over lldb.
    @param result       -   command interpreter result object
                            that receives the output and status.
    @param input_arg    -   optional argument; when truthy, enables full
                            child expansion of the dump table variable.
                            Defaults to None.

    """
    if input_arg:
        interpreter.HandleCommand("ta v *hyp_dump_table --show-all-children", result)
    else:
        interpreter.HandleCommand("ta v *hyp_dump_table", result)
        logger.info("Run hyp_ext hyp_dump_table more for complete log")

    # Check status
    if result.Succeeded():
        out = result.GetOutput()
        err = result.GetError()
        print(out if out else "[hyp_ext] <no stdout>")
        if err:
            logger.error("[hyp_ext] stderr:")
            logger.error(err)
    else:
        logger.error("[hyp_ext] command failed:")
        logger.error(result.GetError())

def qhee_smc_log(interpreter, result):
    """!
    Dumps the QHEE SMC log index and the full SMC log.

    First reads the 'hyp_smc_log_idx' variable in hexadecimal format
    to display the current write index, then delegates to
    hypervisor_smc_logs() to print the complete SMC log contents.

    @param interpreter  -   command interpreter to run
                            command over lldb.
    @param result       -   command interpreter result object
                            that receives the output and status.

    """
    interpreter.HandleCommand("ta v -f x hyp_smc_log_idx", result)

    # Check status
    if result.Succeeded():
        out = result.GetOutput()
        err = result.GetError()
        print(out if out else "[hyp_ext] <no stdout>")
        if err:
            logger.error("[hyp_ext] stderr:")
            logger.error(err)
    else:
        logger.error("[hyp_ext] command failed:")
        logger.error(result.GetError())

    hypervisor_smc_logs(interpreter, result)


def qhee_smmu_operations(interpreter, result, input_arg = None):
    """!
    Dumps the QHEE SMMU virtual operation history.

    Inspects the 'aSMMU_HYP_Virtual_Oper_History' array variable.
    When 'input_arg' is provided, the '--show-all-children' flag is
    appended to reveal all history entries. Otherwise a summary is
    printed and the user is advised to re-run with the flag.

    @param interpreter  -   command interpreter to run
                            command over lldb.
    @param result       -   command interpreter result object
                            that receives the output and status.
    @param input_arg    -   optional argument; when truthy, enables full
                            child expansion of the operation history.
                            Defaults to None.

    """
    if input_arg:
        interpreter.HandleCommand("ta v aSMMU_HYP_Virtual_Oper_History --show-all-children", result)
    else:
        interpreter.HandleCommand("ta v aSMMU_HYP_Virtual_Oper_History", result)
        logger.info("Run hyp_ext qhee_smmu_operations more for complete log")

    # Check status
    if result.Succeeded():
        out = result.GetOutput()
        err = result.GetError()
        print(out if out else "[hyp_ext] <no stdout>")
        if err:
            logger.error("[hyp_ext] stderr:")
            logger.error(err)
    else:
        logger.error("[hyp_ext] command failed:")
        logger.error(result.GetError())