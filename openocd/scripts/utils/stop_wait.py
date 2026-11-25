#
# Copyright (c) 2025 Qualcomm Innovation Center, Inc.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file stop_wait.py
#
# @section Description
# Helpers for polling an LLDB process until it reaches a specific stop
# state (e.g. a breakpoint hit).
#

import time
import lldb


def wait_for_breakpoint_hit(process: lldb.SBProcess, timeout_sec=30):
    """!
    @brief Poll a process until it stops on a breakpoint or times out.

    @param process      The LLDB process object to monitor.
    @param timeout_sec  Maximum time (in seconds) to wait for the hit.

    @return True if a breakpoint was hit within the timeout, False otherwise.
    """
    start = time.time()

    while time.time() - start < timeout_sec:
        state = process.GetState()

        if state in (lldb.eStateExited, lldb.eStateCrashed, lldb.eStateDetached):
            return False

        if state == lldb.eStateStopped:
            thread = process.GetSelectedThread()
            if thread is not None and thread.GetStopReason() == lldb.eStopReasonBreakpoint:
                return True
        time.sleep(0.01)

    return False
