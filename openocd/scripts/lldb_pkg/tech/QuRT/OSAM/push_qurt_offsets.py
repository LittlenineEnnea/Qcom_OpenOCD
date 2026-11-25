#
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file push_qurt_offsets.py
#
# @section Description
#   Push authoritative QuRT TCB layout (SIZEOF + per-field byte offsets) from
#   the ELF's DWARF into the OpenOCD ``qurt`` RTOS backend so its SW-thread
#   walk is per-build correct on every QuRT variant (SIZEOF_TCB 384/448,
#   utcb_name 28/32, etc.) with NO hardcoded offsets on the C side.
#
#   The layout is read from the ELF/DWARF entirely by *struct member name*
#   using the same lldb type API the rest of the OSAM already uses
#   (SBType.GetByteSize / GetFieldAtIndex / GetOffsetInBytes). This gives
#   T32-grade, build-independent correctness without touching the kernel.
#
#   Delivery to OpenOCD is via the existing OpenOCD command channel (NOT the
#   gdb-remote packet channel, which carries no offsetof/sizeof):
#     - We issue an lldb command ``process plugin packet monitor
#       hexagon qurtSetOffsets <13 uint32 args>`` (aka gdb-remote ``qRcmd``).
#     - That is dispatched to the ``hexagon_qurt_set_offsets_cmd``
#       COMMAND_HANDLER in src/target/hexagon.c, which calls
#       ``qurt_apply_pushed_offsets()`` in src/rtos/qurt.c and sets
#       ``p->offsets_pushed = true``.
#     - ``qurt_cache_constants()`` then uses the pushed values in preference
#       to both the compiled ``qurt_default_offsets`` and the (usually-absent)
#       CONTEXT_* absolute symbols.
#
#   Timing: the push must fire AFTER the ELF is loaded (DWARF types
#   resolvable) and BEFORE the first halt-driven qXfer:threads:read (when
#   ``qurt_update_threads`` first uses these values). We install:
#     (a) an explicit lldb command ``hexagonPushQurtOffsets`` the user or a
#         boot .lldb script can invoke, and
#     (b) an lldb stop-hook that auto-fires on the first target stop after
#         load. The push is idempotent (pushing again just overwrites and
#         forces a re-cache on the next update), so re-firing is harmless.
#
#   Framework wiring: this module follows the same convention as
#   ``qurt_thread_label`` -- it exposes a public ``install_push_qurt_offsets``
#   entry point that is invoked by a command class in ``openocd_lldb.py``
#   (``QurtPushOffsets``). The module intentionally has NO
#   ``__lldb_init_module`` so that ``openocd_lldb.py``'s own
#   ``__lldb_init_module`` class-discovery loop remains the single, unchanged
#   installation path.
#

import logging

import lldb


TCB_TYPE_NAME = "QURTK_thread_context"

# Maximum plausible sizeof(TCB); mirrors QURT_MAX_PLAUSIBLE_CONTEXT_SIZE in
# src/rtos/qurt.h. A struct larger than this is treated as a bad DWARF read.
_MAX_PLAUSIBLE_CTX_SIZE = 0x10000

# Recursion depth bound for the field resolver, guarding against pathological
# or cyclic DWARF.
_MAX_RECURSION_DEPTH = 8

# Fields whose offsets ``qurt_apply_pushed_offsets()`` consumes. The order
# matches the ``hexagon qurtSetOffsets`` command argument order (after the
# leading <ctx_size>).
_TCB_FIELDS = (
    "prio",         # CONTEXT_prio       - phv (valid|prio) word
    "thread_id",    # CONTEXT_thread_id  - QuRT sw thread id
    "ugpgp",        # CONTEXT_ugpgp      - utcb = *(tcb + ugpgp + 4)
    "hthread",      # CONTEXT_hthread    - low byte = HW tnum (parked = 0xff)
    "ssrelr",       # CONTEXT_ssrelr     - elr/pc; ssr at +4 (ASID lives here)
    "r0100",        # CONTEXT_r0100      - r0/r1 pair base
    "r2928",        # CONTEXT_r2928      - r28/r29(sp) pair base
    "r3130",        # CONTEXT_r3130      - r30(fp)/r31(lr) pair base
    "framelimit",   # CONTEXT_framelimit
    "framekey",     # CONTEXT_framekey
    "guest_info",   # CONTEXT_guest_info - for user framekey lookup
)

_AGGREGATE_TYPE_CLASSES = (
    lldb.eTypeClassStruct,
    lldb.eTypeClassUnion,
    lldb.eTypeClassClass,
)

try:
    from logger import logger as _default_logger
except Exception:
    _default_logger = logging.getLogger("push_qurt_offsets")

# Module-level state guards. ``_setup_done`` makes install idempotent within a
# debugger session; ``_pushed_once`` makes the auto stop-hook push exactly
# once.
_setup_done = False
_pushed_once = False


# ---------------------------------------------------------------------------
# DWARF field-offset resolution
# ---------------------------------------------------------------------------
def _find_field_offset_with_depth(struct_type, field_name, base, depth):
    """Find ``field_name`` in ``struct_type`` returning ``(offset, depth)``.

    Recurses into nested (named or anonymous) struct/union members, returning
    the SHALLOWEST match so callers can compare depths across sibling
    subtrees.

    :param struct_type: The lldb.SBType to search.
    :param field_name: The member name to locate.
    :param base: Absolute byte offset accumulated to this level.
    :param depth: Current recursion depth.
    :return: ``(absolute_offset, depth)`` of the shallowest match, else None.
    """
    if (not struct_type or not struct_type.IsValid()
            or depth > _MAX_RECURSION_DEPTH):
        return None

    best = None  # (offset, depth)
    for i in range(struct_type.GetNumberOfFields()):
        field = struct_type.GetFieldAtIndex(i)
        field_off = field.GetOffsetInBytes()

        if field.GetName() == field_name:
            candidate = (base + field_off, depth)
            if best is None or candidate[1] < best[1]:
                best = candidate
            continue

        field_type = field.GetType()
        if field_type and field_type.IsValid():
            canon = field_type.GetCanonicalType()
            if canon.GetTypeClass() in _AGGREGATE_TYPE_CLASSES:
                sub = _find_field_offset_with_depth(
                    canon, field_name, base + field_off, depth + 1)
                if sub is not None and (best is None or sub[1] < best[1]):
                    best = sub
    return best


def find_field_offset_recursive(struct_type, field_name):
    """Return the absolute byte offset of ``field_name`` in ``struct_type``.

    Searches nested anonymous (and named) struct/union members recursively
    and applies SHALLOWEST-MATCH-WINS semantics.

    ``QURTK_thread_context`` contains the same field name at more than one
    nesting level. The critical example is ``hthread``:

      * the REAL current-scheduling byte is a TOP-LEVEL member at offset 42
        (this is what CONTEXT_hthread / the qurt.c walker needs), while
      * a DIFFERENT, unrelated ``hthread`` lives three levels deep inside the
        anonymous ``error`` sub-struct at offset 30.

    A naive depth-first "first match" returns the offset-30 one (it is visited
    first), which mis-classifies every parked SW thread as RUNNING and breaks
    the thread list. Preferring the shallowest depth makes top-level fields --
    which is what all the CONTEXT_* offsets are conceptually -- always win over
    a same-named field buried inside some other sub-struct.

    :param struct_type: The lldb.SBType to search.
    :param field_name: The member name to locate.
    :return: The absolute byte offset (int), or None if not found.
    """
    match = _find_field_offset_with_depth(struct_type, field_name, 0, 0)
    return match[0] if match is not None else None


def get_struct_field_offset(struct_type, field_name, logger=None):
    """Absolute byte offset of ``field_name`` in ``struct_type``.

    Recurses into nested anonymous struct/union members. Returns 0 when the
    field is not found, matching the OSAM's existing convention (the C side
    treats 0 as "keep the compiled default for this field").

    :param struct_type: The lldb.SBType to search.
    :param field_name: The member name to locate.
    :param logger: Unused; accepted for signature parity with the OSAM helper.
    :return: The absolute byte offset (int); 0 if not found.
    """
    off = find_field_offset_recursive(struct_type, field_name)
    return off if off is not None else 0


def _resolve_utcb_name_offset(target, logger):
    """Resolve OFFSET_UTCB_thread_name via the ``ugp -> utcb -> attr -> name``
    member chain, entirely by NAME (per-build safe).

    :param target: The lldb.SBTarget whose DWARF is queried.
    :param logger: Logger for diagnostics.
    :return: Byte offset of the thread-name field within the UTCB, or 0 on
             failure (the C side then keeps its compiled default).
    """
    ugp_type = target.FindFirstType("QURT_ugp_ptr")
    if not ugp_type or not ugp_type.IsValid():
        logger.warning("push_qurt_offsets: type 'QURT_ugp_ptr' not found; "
                       "utcb_name offset stays at compiled default")
        return 0

    utcb_field = None
    for i in range(ugp_type.GetNumberOfFields()):
        field = ugp_type.GetFieldAtIndex(i)
        if field.GetName() == "utcb":
            utcb_field = field
            break
    if utcb_field is None:
        logger.warning("push_qurt_offsets: field 'ugp.utcb' not found")
        return 0

    utcb_type = utcb_field.GetType()
    if utcb_type.IsPointerType():
        utcb_type = utcb_type.GetPointeeType()

    attr_off = get_struct_field_offset(utcb_type, "attr", logger)
    attr_field = None
    for i in range(utcb_type.GetNumberOfFields()):
        field = utcb_type.GetFieldAtIndex(i)
        if field.GetName() == "attr":
            attr_field = field
            break
    if attr_field is None:
        logger.warning("push_qurt_offsets: field 'utcb.attr' not found")
        return 0

    attr_type = attr_field.GetType()
    if attr_type.IsPointerType():
        attr_type = attr_type.GetPointeeType()

    name_off = get_struct_field_offset(attr_type, "name", logger)
    # The kernel API expects the offset of ``name[]`` from the START of the
    # UTCB (matching OFFSET_UTCB_thread_name in consts_autogen.h), so sum
    # attr_off + name_off. The offset of ``utcb`` inside ``ugp`` is NOT added:
    # the C driver reaches the UTCB base separately via
    # CONTEXT_ugpgp + QURT_UTCB_PTR_OFFSET_FROM_UGPGP.
    return attr_off + name_off


def resolve_qurt_layout(target, logger=None):
    """Resolve the QuRT TCB layout from the ELF's DWARF, entirely by NAME.

    :param target: The lldb.SBTarget whose DWARF is queried.
    :param logger: Optional logger; defaults to the module logger.
    :return: A dict with ``ctx_size``, every name in ``_TCB_FIELDS``,
             ``utcb_name`` and a ``valid`` bool. On failure (type missing /
             stripped ELF) ``valid`` is False and offsets are 0, so the caller
             skips the push and OpenOCD keeps its compiled defaults.
    """
    logger = logger or _default_logger
    out = {"ctx_size": 0, "utcb_name": 0, "valid": False}
    for name in _TCB_FIELDS:
        out[name] = 0

    tcb_type = target.FindFirstType(TCB_TYPE_NAME)
    if not tcb_type or not tcb_type.IsValid():
        logger.warning(
            "push_qurt_offsets: type '%s' not found in ELF's DWARF "
            "(image stripped?) -- OpenOCD will keep its compiled defaults",
            TCB_TYPE_NAME)
        return out

    ctx_size = int(tcb_type.GetByteSize())
    if ctx_size == 0 or ctx_size > _MAX_PLAUSIBLE_CTX_SIZE:
        logger.warning(
            "push_qurt_offsets: sizeof(%s)=%d implausible; skipping push",
            TCB_TYPE_NAME, ctx_size)
        return out

    out["ctx_size"] = ctx_size
    for name in _TCB_FIELDS:
        off = get_struct_field_offset(tcb_type, name, logger)
        if off == 0 and name != "prio":
            # Only ``prio`` is realistically at offset 0; a 0 elsewhere most
            # likely means the field was renamed. The C side treats 0 as
            # "keep compiled default" per-field, so partial resolution still
            # helps.
            logger.debug(
                "push_qurt_offsets: field '%s' resolved to offset 0 in %s "
                "(may be a struct rename)", name, TCB_TYPE_NAME)
        out[name] = int(off)

    out["utcb_name"] = int(_resolve_utcb_name_offset(target, logger))
    out["valid"] = True
    return out


# ---------------------------------------------------------------------------
# Push to OpenOCD
# ---------------------------------------------------------------------------
def _run_lldb_command(debugger, cmd, logger):
    """Run an lldb command, logging any command-level failure.

    :param debugger: The lldb.SBDebugger instance.
    :param cmd: The command string to run.
    :param logger: Logger for diagnostics.
    :return: True on success, False otherwise.
    """
    ret = lldb.SBCommandReturnObject()
    debugger.GetCommandInterpreter().HandleCommand(cmd, ret)
    if not ret.Succeeded():
        logger.error("push_qurt_offsets: lldb command failed: %s -- %s",
                     cmd, (ret.GetError() or "").strip())
        return False
    return True


def push_qurt_offsets(target=None, debugger=None, logger=None):
    """Resolve the QuRT TCB layout from DWARF and push it into OpenOCD.

    Delivery is via ``process plugin packet monitor hexagon qurtSetOffsets
    ...`` (gdb-remote ``qRcmd``). Idempotent: repeated calls overwrite the
    pushed values and force a re-cache on the next ``qurt_update_threads``.

    :param target: The lldb.SBTarget; defaults to the selected target.
    :param debugger: The lldb.SBDebugger; defaults to ``lldb.debugger``.
    :param logger: Optional logger; defaults to the module logger.
    :return: True on a successful push, False otherwise.
    """
    logger = logger or _default_logger
    if debugger is None:
        debugger = lldb.debugger
    if target is None:
        target = debugger.GetSelectedTarget()
    if not target or not target.IsValid():
        logger.warning("push_qurt_offsets: no valid target; skipping")
        return False

    layout = resolve_qurt_layout(target, logger)
    if not layout["valid"]:
        logger.warning(
            "push_qurt_offsets: could not resolve QURTK_thread_context "
            "layout -- leaving OpenOCD's compiled defaults in place")
        return False

    args = [
        layout["ctx_size"],
        layout["prio"], layout["thread_id"], layout["ugpgp"],
        layout["hthread"], layout["ssrelr"],
        layout["r0100"], layout["r2928"], layout["r3130"],
        layout["framelimit"], layout["framekey"],
        layout["guest_info"], layout["utcb_name"],
    ]
    # OpenOCD routes qRcmd through its command interpreter, matching
    # ``hexagon qurtSetOffsets ...``. Values are decimal --
    # COMMAND_PARSE_NUMBER(u32, ...) on the C side accepts decimal
    # unambiguously (avoiding any 0x-prefix escaping subtleties over qRcmd).
    cmd = ("process plugin packet monitor hexagon qurtSetOffsets "
           + " ".join(str(v) for v in args))

    logger.info(
        "push_qurt_offsets: pushing DWARF-resolved layout: "
        "ctx_size=%d prio=%d tid=%d ugpgp=%d hthread=%d ssrelr=%d "
        "r0100=%d r2928=%d r3130=%d flim=%d fkey=%d ginfo=%d utcb=%d",
        *args)

    return _run_lldb_command(debugger, cmd, logger)


# ---------------------------------------------------------------------------
# lldb command + stop-hook wiring
# ---------------------------------------------------------------------------
def push_qurt_offsets_command(debugger, command, exe_ctx, result,
                              internal_dict):
    """``hexagonPushQurtOffsets`` command handler (manual entry point).

    :param debugger: The lldb.SBDebugger instance.
    :param command: The command argument string (unused).
    :param exe_ctx: The execution context (may carry the target).
    :param result: The command return object.
    :param internal_dict: lldb's internal dict (unused).
    """
    global _pushed_once
    target = exe_ctx.GetTarget() if exe_ctx else debugger.GetSelectedTarget()
    if push_qurt_offsets(target=target, debugger=debugger):
        _pushed_once = True
        result.PutCString("qurt: TCB layout pushed OK")
    else:
        result.SetError("qurt: TCB layout push FAILED (see logs)")


class QurtPushOffsetsStopHook(object):
    """Auto-firing target stop-hook that pushes the TCB layout once.

    lldb instantiates this class on every stop and calls ``handle_stop()``.
    On the FIRST stop after install we resolve the QuRT TCB layout from DWARF
    and push it into OpenOCD; subsequent stops are silent no-ops (guarded by
    the module-level ``_pushed_once``).

    A ``target stop-hook add -P`` target MUST be a class exposing:
        ``__init__(self, target, extra_args, internal_dict)``
        ``handle_stop(self, exe_ctx, stream) -> bool``  (True => report stop)
    """

    def __init__(self, target, extra_args, internal_dict):
        """Initialise the stop-hook.

        :param target: The lldb.SBTarget the hook is attached to.
        :param extra_args: lldb.SBStructuredData of extra args (unused).
        :param internal_dict: lldb's internal dict (unused).
        """
        # No per-hook state is needed; everything comes from the module-level
        # guard and the exe_ctx passed to handle_stop().
        self.target = target

    def handle_stop(self, exe_ctx, stream):
        """Push the DWARF-resolved layout on the first stop.

        :param exe_ctx: The lldb.SBExecutionContext for this stop.
        :param stream: The lldb.SBStream for user-visible output.
        :return: Always True, so the stop is reported normally.
        """
        global _pushed_once
        if _pushed_once:
            return True

        target = exe_ctx.GetTarget() if exe_ctx else None
        if not target or not target.IsValid():
            return True

        try:
            debugger = target.GetDebugger()
            if push_qurt_offsets(target=target, debugger=debugger):
                _pushed_once = True
        except Exception as exc:
            print("[push_qurt_offsets] stop-hook error: %s" % exc)
        return True


def _check(debugger, cmd, label):
    """Run an lldb command, capturing command-level failures.

    ``HandleCommand`` does not raise on command-level failure, so this routes
    through an SBCommandReturnObject and prints the error text.

    :param debugger: The lldb.SBDebugger instance.
    :param cmd: The command string to run.
    :param label: Short label used in the failure message.
    :return: True on success, False otherwise.
    """
    ret = lldb.SBCommandReturnObject()
    debugger.GetCommandInterpreter().HandleCommand(cmd, ret)
    if not ret.Succeeded():
        print("[push_qurt_offsets] WARNING: %s failed: %s"
              % (label, (ret.GetError() or "").strip()))
        return False
    return True


def install_push_qurt_offsets(debugger, module_name):
    """Install the ``hexagonPushQurtOffsets`` command and the auto stop-hook.

    Mirrors the working recipe used by ``qurt_thread_label``:

      1. ``script import <module_name> as <alias>`` -- imports THIS module
         into lldb's script-interpreter namespace under a short alias. lldb's
         ``-P`` (stop-hook) and ``-f`` (command) resolvers walk that namespace
         and do NOT reliably resolve a deep dotted package path, so the alias
         is required for the class/function to be found when they fire.
         Importing under a DIFFERENT alias than this module's own ``__name__``
         does not re-trigger any module init, so there is no recursion (and
         ``_setup_done`` guards it regardless).
      2. ``target stop-hook add -P <alias>.QurtPushOffsetsStopHook``
      3. ``command script add -f <alias>.push_qurt_offsets_command
         hexagonPushQurtOffsets``

    Idempotent within a debugger session via ``_setup_done``. It is silent on
    success; errors are reported so a broken installation remains diagnosable.

    :param debugger: The lldb.SBDebugger instance.
    :param module_name: The fully-qualified import path of THIS module, i.e.
                        ``lldb_pkg.tech.QuRT.OSAM.push_qurt_offsets``.
    """
    global _setup_done
    if _setup_done:
        return
    _setup_done = True

    alias = "push_qurt_offsets"

    _check(debugger,
           "script import %s as %s" % (module_name, alias),
           "alias module")

    _check(debugger,
           "target stop-hook add -P %s.QurtPushOffsetsStopHook" % alias,
           "register stop-hook")

    _check(debugger,
           "command script add -f %s.push_qurt_offsets_command "
           "hexagonPushQurtOffsets" % alias,
           "register hexagonPushQurtOffsets command")
