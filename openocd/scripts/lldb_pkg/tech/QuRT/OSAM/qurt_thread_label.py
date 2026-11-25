# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# All rights reserved.
# Confidential and Proprietary - Qualcomm Technologies, Inc.
#
# @file qurt_thread_label.py
#
# @section Description
# This file contains the implementation that beautifies the per-thread label
# shown by the lldb CLI and by VS Code's CALL STACK panel, so it reflects
# OpenOCD's QuRT-aware qThreadExtraInfo reply. It installs a thread-format
# beautifier, a stop-hook, and the qurtLabels / qurtstack helper commands.
# The QurtThreadLabel command class in openocd_lldb.py registers it.
#

import lldb
import binascii
import re
import shlex

# Names of the SBThread method we try, in order, to set the thread display
# name. Different lldb builds expose different write-paths: SetName(str) on
# newer Python-binding builds, SetQueueName(str) on every binding from lldb
# 3.7+. The first one that returns without raising counts as success.
NAME_SETTER_METHODS = ("SetName", "SetQueueName")


def packet_send(process, packet):
    """
    Sends a raw gdb-remote packet via lldb's command interpreter and returns
    the reply payload (the hex string after "response: "). Never raises into
    the caller; it MUST be safe to call from inside a stop-hook.

    Parameters:
    process (lldb.SBProcess): The process to send the packet on.
    packet (str): The gdb-remote packet to send.

    Returns:
    str: The reply payload, or None on any failure (callers must tolerate
    None and fall through to the lldb default label).
    """
    if not process or not process.IsValid():
        return None
    try:
        interp = process.GetTarget().GetDebugger().GetCommandInterpreter()
        ret = lldb.SBCommandReturnObject()
        interp.HandleCommand("process plugin packet send " + packet, ret)
        if not ret.Succeeded():
            return None
        out = ret.GetOutput() or ""
    except Exception:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("response:"):
            return line[len("response:"):].strip()
    return None


def decode_extra_info(hex_payload):
    """
    Hex-decodes a qThreadExtraInfo reply to a clean label.

    Format from OpenOCD is "Name: <descriptive>, <extra-info>" where
    <descriptive> is either the rich form ("[HW08] (active)" or
    "[SW] name PARKED pri=N tcb=0x...") or the pre-qurtEnable form
    ("HW0" / "HW1" / ...). The <extra-info> column duplicate is dropped for
    the row label.

    Parameters:
    hex_payload (str): The hex-encoded qThreadExtraInfo reply.

    Returns:
    str: The decoded label, or empty string on any decode failure (caller
    must then leave the SBThread name untouched so lldb's default is kept).
    """
    if not hex_payload:
        return ""
    try:
        raw = binascii.unhexlify(hex_payload).decode("utf-8", errors="replace")
    except Exception:
        return ""
    if raw.startswith("Name: "):
        raw = raw[len("Name: "):]
    sep = raw.find(", ")
    if sep > 0:
        raw = raw[:sep]
    return raw.rstrip()


def thread_label(thread, internal_dict=None):
    """
    LLDB thread-format Python entry point, invoked via
    ${script.thread:...thread_label} in the thread-format / thread-stop-format
    settings. Uses a per-process cache keyed by stop_id so a thread-list
    refresh does not replay gdb-remote traffic per-thread.

    Parameters:
    thread (lldb.SBThread): The thread to label.
    internal_dict (dict): The built-in identifiers dict (unused).

    Returns:
    str: The descriptive label for the thread, or empty string on any
    failure.
    """
    if not thread or not thread.IsValid():
        return ""
    process = thread.GetProcess()
    if not process or not process.IsValid():
        return ""

    stop_id = process.GetStopID()
    cache = getattr(process, "_qurt_label_cache", None)
    if cache is None or cache.get("stop_id") != stop_id:
        cache = {"stop_id": stop_id, "labels": {}}
        try:
            process._qurt_label_cache = cache
        except Exception:
            cache = None  # SBProcess refused the attribute; OK, skip cache

    tid = thread.GetThreadID()
    if cache is not None and tid in cache["labels"]:
        return cache["labels"][tid]

    hex_payload = packet_send(process, "qThreadExtraInfo,%x" % tid)
    label = decode_extra_info(hex_payload)

    if cache is not None:
        cache["labels"][tid] = label
    return label


def apply_labels_to_process(process, verbose=False):
    """
    Walks every thread in the process and pushes the qThreadExtraInfo-derived
    label into the SBThread display-name slot (whichever variant the current
    lldb's Python binding exposes). Idempotent.

    If a label is empty for a thread, no setter is called so lldb's default
    label is preserved. If no setter method is callable on SBThread at all,
    it still returns cleanly (count 0); the CLI thread-format rendering
    continues to work via the ${script.thread:...} format token.

    Parameters:
    process (lldb.SBProcess): The process whose threads to label.
    verbose (bool): When True, capture extra per-call diagnostic strings.

    Returns:
    tuple: (count_set, diagnostic_strings) where count_set is how many
    setter calls succeeded and diagnostic_strings lists per-call status.
    """
    diag = []
    if not process or not process.IsValid():
        diag.append("process invalid")
        return 0, diag

    # Probe ONCE which setter the SBThread Python binding actually exposes,
    # and cache the result on the process object so the per-stop iteration
    # doesn't re-probe. We must NOT cache a decision until we have a VALID
    # thread to probe: if the first stop-hook call happens before any thread
    # is populated, GetThreadAtIndex(0) is invalid, and caching "no setter"
    # then would permanently disable labels for the session even after
    # threads appear. So cache only once a valid probe succeeds, otherwise
    # return early to retry on the next stop.
    chosen = getattr(process, "_qurt_label_setter", None)
    if not chosen:
        probe = process.GetThreadAtIndex(0)
        if not (probe and probe.IsValid()):
            # No thread yet -> do NOT cache; allow retry on the next stop.
            diag.append("no threads yet; deferring setter probe")
            return 0, diag
        for name in NAME_SETTER_METHODS:
            m = getattr(probe, name, None)
            if callable(m):
                chosen = name
                diag.append("using SBThread.%s()" % name)
                break
        else:
            diag.append("no settable name method on SBThread "
                        "(tried: %s)" % ", ".join(NAME_SETTER_METHODS))
        # Cache only now that we probed a valid thread (chosen may be "" if
        # this lldb build exposes no setter at all -- that is a stable fact
        # for the session, so caching "" here is correct and avoids re-probing
        # on every stop; the CLI thread-format path still works regardless).
        try:
            process._qurt_label_setter = chosen or ""
        except Exception:
            pass

    if not chosen:
        return 0, diag

    n = 0
    for thread in process:
        try:
            label = thread_label(thread)
        except Exception as exc:
            if verbose:
                diag.append("thread_label() raised: %s" % exc)
            label = ""
        if not label:
            continue
        setter = getattr(thread, chosen, None)
        if not callable(setter):
            continue
        try:
            setter(label)
            n += 1
        except Exception as exc:
            if verbose:
                diag.append("%s(%r) raised: %s" % (chosen, label, exc))
    diag.append("applied to %d/%d threads via SBThread.%s()"
                % (n, process.GetNumThreads(), chosen))
    return n, diag


class QurtThreadLabelStopHook(object):
    """
    Class-form stop-hook registered via
    "target stop-hook add -P <module>.QurtThreadLabelStopHook".

    LLDB invokes handle_stop() on every halt; it is used to push the
    descriptive labels into SBThread.SetName() so lldb-dap (VS Code) sees
    them in DAP Thread.name on its next Threads request. handle_stop() always
    returns True (the hook is observational and never halts execution).
    """

    def __init__(self, target, extra_args, internal_dict):
        """
        Initializes the stop-hook. No per-target state is needed; everything
        is read from the exe_ctx passed to handle_stop().

        Parameters:
        target (lldb.SBTarget): The target the hook is registered on.
        extra_args (lldb.SBStructuredData): Extra arguments (unused).
        internal_dict (dict): The built-in identifiers dict (unused).
        """
        pass

    # Process-id of the last process we successfully probed (so we only
    # print the API-selection diagnostic once per process attachment).
    _diag_announced_for_pid = None

    def handle_stop(self, exe_ctx, stream):
        """
        Applies thread labels on every halt.

        Parameters:
        exe_ctx (lldb.SBExecutionContext): The execution context.
        stream (lldb.SBStream): The output stream for diagnostics.

        Returns:
        bool: Always True so the stop dispatch continues.
        """
        try:
            process = exe_ctx.GetProcess()
            n, diag = apply_labels_to_process(process)
            # Print the SBThread.<method>() selection diagnostic the first
            # time we see each new process, so the user knows which API
            # the stop-hook is using. After that we stay silent on stops.
            pid = process.GetProcessID() if (process and process.IsValid()) else None
            if pid is not None and pid != QurtThreadLabelStopHook._diag_announced_for_pid:
                QurtThreadLabelStopHook._diag_announced_for_pid = pid
                if stream is not None:
                    for line in diag:
                        try:
                            stream.Print("[qurt_thread_label] %s\n" % line)
                        except Exception:
                            pass
        except Exception:
            # If anything goes wrong, never break the stop dispatch.
            # Worst case: VS Code keeps showing lldb's default labels.
            pass
        return True


def qurt_labels_command(debugger, command, exe_ctx, result, internal_dict):
    """
    Implementation of the qurtLabels lldb command that does a manual one-shot
    re-apply of labels. Forces a re-probe of the SBThread setter (clears the
    cached choice) and re-applies all labels. Useful as a debugging aid (the
    output tells which SBThread API path is used) and as an escape hatch if
    the stop-hook ever fails to fire.

    Parameters:
    debugger (lldb.SBDebugger): The LLDB debugger instance.
    command (str): The command argument string (none expected).
    exe_ctx (lldb.SBExecutionContext): The execution context object.
    result (lldb.SBCommandReturnObject): The command return object.
    internal_dict (dict): The built-in identifiers dict (unused).
    """
    target = debugger.GetSelectedTarget()
    if not target or not target.IsValid():
        result.AppendMessage("[qurt_thread_label] no target selected")
        return
    process = target.GetProcess()
    if not process or not process.IsValid():
        result.AppendMessage("[qurt_thread_label] no process attached")
        return

    # Force a fresh probe so the user sees the diagnostic each time they
    # run this command (the stop-hook caches; the manual command does not).
    try:
        if hasattr(process, "_qurt_label_setter"):
            del process._qurt_label_setter
    except Exception:
        pass

    n, diag = apply_labels_to_process(process, verbose=True)
    result.AppendMessage("[qurt_thread_label] applied to %d thread(s)" % n)
    for line in diag:
        result.AppendMessage("[qurt_thread_label] %s" % line)


def monitor_reply_text(debugger, monitor_cmd):
    """
    Sends an OpenOCD monitor command via gdb-remote and returns the
    hex-decoded reply text.

    OpenOCD emits "hexagon qurtStack" output through command_print(), which
    over gdb-remote arrives as a hex-encoded qRcmd reply. LLDB's raw
    "process plugin packet monitor" prints that hex verbatim; this helper
    decodes it (the same pattern hexagon_utils.py uses for tlb/pagetable),
    so the CodeLLDB user sees readable text.

    Parameters:
    debugger (lldb.SBDebugger): The LLDB debugger instance.
    monitor_cmd (str): The OpenOCD monitor command to send.

    Returns:
    str: The decoded reply text, or empty string on failure.
    """
    interp = debugger.GetCommandInterpreter()
    ret = lldb.SBCommandReturnObject()
    interp.HandleCommand("process plugin packet monitor " + monitor_cmd, ret)
    out = ret.GetOutput() or ""
    text = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("response:"):
            hexpayload = line[len("response:"):].strip()
            try:
                text += binascii.unhexlify(hexpayload).decode("utf-8",
                                                              errors="replace")
            except (binascii.Error, ValueError):
                # Not hex (e.g. an already-plaintext error line) -> pass through
                text += hexpayload
    return text


# Matches a qurtStack frame line, e.g. "  #3  0xf005a2fc". Captures the
# leading text (indent + "#N  ") and the hex address so we can append a
# symbol without disturbing the original formatting.
_QSTACK_FRAME_RE = re.compile(r"^(\s*#\d+\s+)(0x[0-9a-fA-F]+)\s*$")


def symbolize_address(target, addr):
    """
    Resolve a code address to a "module`function + offset at file:line" label
    using LLDB's own symbol table (the ELFs are loaded as LLDB modules, so
    this works even though OpenOCD's C side has no symbol table and prints raw
    addresses).

    Parameters:
    target (lldb.SBTarget): The target whose modules/symbols are queried.
    addr (int): The code address to symbolize.

    Returns:
    str: A human-readable label, or "" if the address cannot be resolved.
    """
    # Every failure mode below (invalid target, address in a region no loaded
    # module covers, missing symbol/debug info, or any SB-API quirk) is caught
    # and turned into "" so the caller keeps the raw address for that one
    # frame. A single unresolvable address must never blank the line or abort
    # the whole qurtstack command.
    try:
        if target is None or not target.IsValid():
            return ""
        sb_addr = target.ResolveLoadAddress(addr)
        if not sb_addr or not sb_addr.IsValid():
            return ""
        sym_ctx = target.ResolveSymbolContextForAddress(
            sb_addr, lldb.eSymbolContextEverything)
        if not sym_ctx or not sym_ctx.IsValid():
            return ""

        module = sym_ctx.GetModule()
        mod_name = ""
        if module and module.IsValid():
            fspec = module.GetFileSpec()
            if fspec:
                mod_name = fspec.GetFilename() or ""

        # Prefer the debug-info function; fall back to the raw symbol.
        name = ""
        start_addr = None
        func = sym_ctx.GetFunction()
        if func and func.IsValid():
            name = func.GetName() or ""
            start_addr = func.GetStartAddress()
        else:
            symbol = sym_ctx.GetSymbol()
            if symbol and symbol.IsValid():
                name = symbol.GetName() or ""
                start_addr = symbol.GetStartAddress()
        if not name:
            return ""

        label = ("%s`%s" % (mod_name, name)) if mod_name else name

        # Offset into the function (mirrors lldb's "+ N" suffix).
        if start_addr and start_addr.IsValid():
            off = addr - start_addr.GetLoadAddress(target)
            if off > 0:
                label += " + %d" % off

        # Append file:line when line-table info is available.
        line_entry = sym_ctx.GetLineEntry()
        if line_entry and line_entry.IsValid():
            lfspec = line_entry.GetFileSpec()
            if lfspec:
                fname = lfspec.GetFilename()
                lineno = line_entry.GetLine()
                if fname and lineno:
                    label += " at %s:%d" % (fname, lineno)
        return label
    except Exception:
        # Any unexpected SB-API failure -> behave as "unresolved" so the
        # caller falls back to the raw address for this frame.
        return ""


def qurt_stack_command(debugger, command, exe_ctx, result, internal_dict):
    """
    Implementation of the qurtstack lldb command that prints a decoded
    FRAMEKEY-unscrambled deep backtrace of a parked QuRT SW thread. Thin
    wrapper over OpenOCD's "hexagon qurtStack" monitor command: forwards the
    swid, hex-decodes the qRcmd reply, and prints the readable trace. Each
    "#N 0xADDR" frame is then symbolized on the LLDB side (module`function +
    offset at file:line), since OpenOCD's C walker has no symbol table and
    emits raw addresses.

    Parameters:
    debugger (lldb.SBDebugger): The LLDB debugger instance.
    command (str): The command argument string ("<sw_thread_id>").
    exe_ctx (lldb.SBExecutionContext): The execution context object.
    result (lldb.SBCommandReturnObject): The command return object.
    internal_dict (dict): The built-in identifiers dict (unused).
    """
    args = shlex.split(command)
    if len(args) != 1:
        result.AppendMessage("usage: qurtstack <sw_thread_id>")
        result.SetStatus(lldb.eReturnStatusFailed)
        return
    swid = args[0]
    text = monitor_reply_text(debugger, "hexagon qurtStack %s" % swid)
    if not text:
        result.AppendMessage("[qurtStack] no reply (is OpenOCD attached and "
                             "the SW-thread walk enabled?)")
        result.SetStatus(lldb.eReturnStatusFailed)
        return

    # Symbolize each frame line using LLDB's loaded modules. Symbolization is
    # strictly additive and best-effort: any per-line failure (unresolvable
    # address, no loaded binary, or an unexpected SB-API error) falls back to
    # the original raw "#N 0xADDR" line. It NEVER blanks a line and NEVER
    # aborts the command -- the full trace is always printed.
    target = exe_ctx.GetTarget() if exe_ctx else debugger.GetSelectedTarget()
    out_lines = []
    for line in text.rstrip("\n").splitlines():
        try:
            match = _QSTACK_FRAME_RE.match(line)
            if match:
                addr = int(match.group(2), 16)
                label = symbolize_address(target, addr)
                if label:
                    line = "%s%s  %s" % (match.group(1), match.group(2), label)
        except Exception:
            # Keep the original raw line for this frame; never fail the trace.
            pass
        out_lines.append(line)

    result.AppendMessage("\n".join(out_lines))
    result.SetStatus(lldb.eReturnStatusSuccessFinishResult)


def check(debugger, cmd, label, problems):
    """
    Runs an lldb command, capturing failures instead of assuming success.
    HandleCommand() does not raise on command-level failures, so this routes
    through an SBCommandReturnObject and records the error text for the caller
    to report.

    Parameters:
    debugger (lldb.SBDebugger): The LLDB debugger instance.
    cmd (str): The lldb command to run.
    label (str): A short label used in the failure message.
    problems (list): A list that failure messages are appended to.

    Returns:
    bool: True on success, False on failure.
    """
    ret = lldb.SBCommandReturnObject()
    debugger.GetCommandInterpreter().HandleCommand(cmd, ret)
    if not ret.Succeeded():
        problems.append("%s failed: %s" % (label, (ret.GetError() or "").strip()))
        return False
    return True


def install_qurt_thread_label(debugger, module_name):
    """
    Installs the QuRT thread-label beautifier, stop-hook, and the qurtLabels /
    qurtstack commands. Pure implementation entry point, invoked by the
    QurtThreadLabel command class in openocd_lldb.py (which owns the framework
    registration).

    Invoked during launch.json targetCreateCommands, BEFORE the process
    attaches, so it ONLY installs the thread-format/thread-stop-format
    settings (passive string templates), registers a target stop-hook
    (passive), and registers the qurtLabels / qurtstack CLI commands. It does
    NOT touch the process, spawn Python threads, or register SBListeners.

    LLDB's ${script.thread:<dotted>.<func>} thread-format token resolves the
    callback through LLDB's OWN script interpreter namespace, not the Python
    package that "from ... import" populated. A deep package path is not
    reliably importable in that context, so the module is imported into the
    LLDB script interpreter under a stable short alias and that alias is used
    in the token, the stop-hook (-P), and command (-f) registrations.

    Parameters:
    debugger (lldb.SBDebugger): The LLDB debugger instance.
    module_name (str): The fully-qualified import path of THIS module, e.g.
    "lldb_pkg.tech.QuRT.OSAM.qurt_thread_label".
    """
    problems = []
    alias = "qurt_thread_label"
    # Make qurt_thread_label resolve to THIS module inside the LLDB script
    # interpreter, regardless of where the file physically lives.
    check(debugger,
          "script import %s as %s" % (module_name, alias),
          "alias module for thread-format", problems)

    # 1. CLI label beautification (thread-format).
    #
    # The format MUST end with a literal newline. LLDB's built-in default
    # thread-format ends with \n, and "thread list" prints only the per-thread
    # header (this format) for each thread back-to-back; without the trailing
    # \n every thread row concatenates onto a single line. We start from our
    # QuRT-rich label + tid, then append the SAME frame / stop-reason tokens
    # LLDB's built-in default thread-format uses, so each row also shows where
    # it is executing and why it stopped. The "{ ... }" blocks are LLDB
    # conditional sub-expressions: each renders only if its tokens resolve, so
    # threads with no debug info degrade gracefully. For PARKED SW threads LLDB
    # renders the frame from the registers reconstructed in
    # qurt_get_thread_reg_list(), so they show their saved PC/function.
    frame_tokens = ("{ ${frame.pc}}"
                    "{ ${module.file.basename}`${function.name-with-args}}"
                    "{ at ${line.file.basename}:${line.number}}"
                    "{, stop reason = ${thread.stop-reason}}")

    fmt = ("${script.thread:%s.thread_label}" % alias
           + " tid=${thread.id%tid}"
           + frame_tokens
           + "\n")
    stop_fmt = ("${script.thread:%s.thread_label}" % alias
                + " tid=${thread.id%tid}"
                + frame_tokens
                + "\n")
    check(debugger, 'settings set thread-format "%s"' % fmt,
          "set thread-format", problems)
    check(debugger, 'settings set thread-stop-format "%s"' % stop_fmt,
          "set thread-stop-format", problems)

    # 2. Stop-hook that pushes labels into SBThread.SetName().
    #
    # Use the short alias (imported above) rather than the full package path:
    # LLDB's "target stop-hook add -P" resolver walks its own script
    # interpreter namespace and, like the ${script.thread:...} token, does NOT
    # reliably resolve a deep package path. The alias makes
    # qurt_thread_label.QurtThreadLabelStopHook resolvable.
    check(debugger,
          "target stop-hook add -P %s.QurtThreadLabelStopHook" % alias,
          "register stop-hook", problems)

    # 3. Manual escape-hatch command (alias for the same reason).
    check(debugger,
          "command script add -f %s.qurt_labels_command qurtLabels" % alias,
          "register qurtLabels command", problems)

    # 4. Decoded qurtstack wrapper (readable output under CodeLLDB). The LLDB
    # command is lowercase qurtstack; it forwards to the OpenOCD monitor
    # command "hexagon qurtStack" (mixed-case) internally.
    check(debugger,
          "command script add -f %s.qurt_stack_command qurtstack" % alias,
          "register qurtstack command", problems)

    if problems:
        for msg in problems:
            print("[qurt_thread_label] WARNING: %s" % msg)
        print("[qurt_thread_label] install completed with %d warning(s); "
              "thread labels may be degraded" % len(problems))
