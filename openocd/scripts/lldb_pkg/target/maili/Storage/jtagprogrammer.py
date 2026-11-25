#
# Copyright (c) 2026 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file jtagprogrammer.py
#
# @section Description
# Python port of JtagProgrammer.cmm. Loads JtagProgrammer.elf onto the target
# over JTAG, then drives the firmware's command loop by reading/writing the
# jtag_prog_param shared struct in RAM.
#
# Usage (via codelldb launch config):
#   command script import <path>/utils/jtagprogrammer.py
#   meta_dir -s <path/to/metabuild>       # so ELF/DTB resolve from contents.xml
#   jtagprogrammer [elfpath=<path>] [dtbpath=<path>] [slot=0]
#
# elfpath=/dtbpath= are optional once meta_dir has been run — both are
# otherwise resolved automatically from the meta's contents.xml (matched by
# <file_name>, same entries JtagProgrammer.cmm itself ships alongside).
# Passing elfpath=/dtbpath= explicitly always overrides the lookup.
#
# With no auto_command the script enters the interactive menu (mirrors CMM).
# With an auto_command it runs that command non-interactively and exits.

import os
import sys
import shlex
import struct
import time
from enum import IntEnum
from utils.reset import ResetCore
from lldb_pkg.target.maili.target_utils.pbl_load_fbc import pbl_load_fbc

# codelldb's `command script import` does not add scripts/ to sys.path.
# Insert it so `utils.*` imports resolve.
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

import lldb

from utils.debug_context import g_dbgContext, logger
from utils.telnet_client import TelnetClient


# ---------------------------------------------------------------------------
# Command codes  (direct translation of CMM &XxxCmd= constants)
# ---------------------------------------------------------------------------

class Cmd(IntEnum):
    PROGRAM           = 0x01
    ERASE             = 0x02
    CHIP_ERASE        = 0x05
    READ_SECTOR       = 0x09
    WRITE_SECTOR      = 0x0A
    PRINT_MEM_INFO    = 0x20
    SET_HW_PARTITION  = 0x31
    LOAD_PARTITION    = 0x32
    SET_PROG_REDIRECT = 0x33
    SET_ACT_BOOT_PART = 0x35
    ERASE_GENERIC     = 0x40
    COMPUTE_CRC       = 0x41
    PROGRAM_EXTRAS    = 0x42
    FIRMWARE_PROG     = 0x44


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------

def _parse_command_line(command: str):
    """!
    Split raw LLDB command string into (auto_command, arg_line).
    Mirrors CMM: ENTRY &AutoCommand %LINE &ArgLine + gosub FixAutoCommand.

    @param command  - raw command-line string passed by LLDB
    @return (auto_command, arg_line) tuple
    """
    stripped = command.strip() if command else ""
    if not stripped:
        return "", ""

    tokens = shlex.split(stripped)
    first = tokens[0]

    if "=" in first:            # FixAutoCommand: first token is key=value, not a verb
        return "", stripped

    if first in ("0", "0."):    # CMM: "0" or "0." means no command
        return "", " ".join(tokens[1:])

    return first.upper(), stripped[len(first):].strip()


def _parse_arg(name: str, arg_line: str, numeric: bool = False):
    """!
    Extract a named argument from arg_line.
    Mirrors CMM gosub ParseArg.

    @param name      - argument name to look for (matched as " name=")
    @param arg_line  - argument string to search
    @param numeric   - parse the value as an int (base-agnostic) if True
    @return the argument value, or a default if not found
    """
    haystack = " " + arg_line
    needle   = f" {name}="
    idx      = haystack.find(needle)

    if idx == -1:
        if name == "slot":       return 0 if numeric else "0"
        if name == "searchpaths": return "partition_load_pt,."
        return -1 if numeric else ""

    start = idx + len(needle)
    end   = haystack.find(" ", start)
    value = haystack[start:end] if end != -1 else haystack[start:]

    if name == "searchpaths":
        value = f"{value},partition_load_pt,."

    if numeric:
        try:   return int(value, 0)
        except ValueError: return -1
    return value


# ---------------------------------------------------------------------------
# contents.xml lookup — JtagProgrammer.elf / Jtagprogrammer_ufs.dtb are plain
# <file_ref> entries (no cmm_file_var) in the "boot" build block, so they are
# matched by <file_name> text rather than by variable name.
# ---------------------------------------------------------------------------

def _find_file_in_contents_xml(file_name: str, storage_type: str = "ufs") -> str:
    """!
    Resolve a file shipped alongside the boot build (JtagProgrammer.elf,
    Jtagprogrammer_ufs.dtb, ...) by scanning the "boot" build block of
    contents.xml for a <file_ref>/<download_file> whose <file_name> matches.

    Mirrors how the CMM's own tooling locates these files: they live next to
    xbl.elf under the boot buildroot, listed as plain <file_ref> entries
    (no cmm_file_var) tagged storage_type="ufs" — same entries JtagProgrammer.cmm
    itself ships alongside.

    @param file_name     - exact <file_name> text to match, e.g. "JtagProgrammer.elf"
    @param storage_type  - preferred storage_type attribute value ("ufs" or "emmc");
                           entries with no storage_type attribute also match
    @return resolved absolute path, or None if contents.xml/meta isn't set up
            yet or no matching entry is found
    """
    contents_xml_path = g_dbgContext.contentsXmlPath
    if not contents_xml_path or not os.path.isfile(str(contents_xml_path)):
        return None

    import re
    import platform
    from xml.etree import ElementTree as ET

    def _strip_tokens(s: str) -> str:
        s = re.sub(r'\$\{[^:}]+:([^}]+)\}', r'\1', s or "")   # ${var:default} -> default
        s = re.sub(r'\$\{[^}]+\}', '', s)                      # ${var} with no default -> remove
        return s

    def _clean(s: str) -> str:
        s = _strip_tokens(s).replace("\\", "/")
        s = re.sub(r'(?<!^)/{2,}', '/', s)
        return s.rstrip("/")

    def _join(*parts: str) -> str:
        result = ""
        for p in parts:
            p = p.strip("/") if result else p.rstrip("/")
            if not p:
                continue
            result = p if not result else result + "/" + p
        return result

    try:
        tree = ET.parse(str(contents_xml_path))
    except (ET.ParseError, OSError) as e:
        logger.warning(f"Could not parse contents.xml at {contents_xml_path!r}: {e}")
        return None

    builds_flat = tree.getroot().find("builds_flat")
    if builds_flat is None:
        return None

    is_windows = platform.system() == "Windows"

    for build in builds_flat:
        name_elem = build.find("name")
        if name_elem is None or (name_elem.text or "").strip() != "boot":
            continue

        root_path_elem = build.find("windows_root_path" if is_windows else "linux_root_path")
        if root_path_elem is None:
            continue
        build_root = _clean(root_path_elem.text or "")

        fallback_match = None
        for child in build:
            file_name_elem = child.find("file_name")
            if file_name_elem is None or (file_name_elem.text or "").strip() != file_name:
                continue

            file_path_elem = child.find("file_path")
            file_path = _clean(file_path_elem.text or "") if file_path_elem is not None else ""
            full_path = _join(build_root, file_path, file_name)

            child_storage_type = child.get("storage_type")
            if child_storage_type == storage_type:
                return full_path
            if fallback_match is None:
                fallback_match = full_path

        return fallback_match

    return None


# ---------------------------------------------------------------------------
# Main programmer class
# ---------------------------------------------------------------------------

class JtagProgrammer:
    """!
    @brief Drives the JtagProgrammer ELF over JTAG via OpenOCD + LLDB.

    @details
        jtp = JtagProgrammer(debugger, elf_path=..., card_slot=0)
        jtp.setup()                       # load ELF, run to main_c
        jtp._dispatch(choice, arg_line)   # execute a single menu command
    """
    PARAM_BUFFER_SIZE = 4096

    DEV_TYPE_UFS  = 0x0
    DEV_TYPE_EMMC = 0x1

    _ELF_FILENAME = "JtagProgrammer.elf"

    _ELF_SEARCH_PATHS = [
        "../../../../../../Build/Maili_Default_LA/JtagProgrammer/JtagProgrammer.elf",
        "JtagProgrammer.elf",
    ]

    _STATUS_FAILURE = 0x100

    _DTB_FILENAME = "Jtagprogrammer_ufs.dtb"

    def __init__(self, debugger: lldb.SBDebugger, elf_path: str = None,
                 dtb_path: str = None,
                 card_slot: int = 0, dev_type: int = DEV_TYPE_UFS):
        self.debugger  = debugger
        self.dev_type  = dev_type  #device type UFS or eMMC
        self.card_slot = card_slot
        self.elf_path  = self._resolve_elf_path(elf_path)
        self.dtb_path  = self._resolve_dtb_path(dtb_path)

        # Populated by setup()
        self.addr_main_c            = None
        self.addr_param             = None
        self.addr_cmd_status        = None
        self.addr_param_addr        = None
        self.addr_data_length       = None
        self.addr_param_data        = None
        self.addr_reserved          = None
        self.addr_output_buffer     = None
        self.addr_output_buffer_len = None
        self.addr_block_size        = None
        self.addr_max_partitions    = None
        self.addr_partition_names   = None
        self.addr_card_slot         = None
        self.addr_storage_extras    = None
        self.sector_size            = None

        # Runtime state (mirrors CMM local vars)
        self.current_slot          = card_slot
        self.current_hw_partition  = 0
        self.current_hw_part_name  = "Default (0)"
        self.partition_info_read   = False
        self._main_c_bp            = None   # live LLDB bp object at main_c

    # -----------------------------------------------------------------------
    # ELF path resolution
    # -----------------------------------------------------------------------

    def _resolve_elf_path(self, user_path: str = None) -> str:
        """!
        Resolve the ELF path: explicit user_path, else look it up in the
        active meta's contents.xml (set via meta_dir -s <path>), else fall
        back to _ELF_SEARCH_PATHS.
        """
        user_path = user_path.strip() if user_path else None
        if user_path:
            return user_path

        from_contents = _find_file_in_contents_xml(self._ELF_FILENAME)
        if from_contents:
            logger.info(f"ELF resolved from contents.xml: {from_contents}")
            return from_contents

        for candidate in self._ELF_SEARCH_PATHS:
            if os.path.isfile(candidate):
                logger.info(f"ELF found at: {candidate}")
                return candidate
        logger.warning(
            f"ELF not found; defaulting to: {self._ELF_SEARCH_PATHS[-1]}\n"
            "Pass elfpath=<path> to specify, or run meta_dir -s <path> first "
            "so it can be resolved from contents.xml."
        )
        return self._ELF_SEARCH_PATHS[-1]

    def _resolve_dtb_path(self, user_path: str = None) -> str:
        """!
        Resolve the UFS target-config DTB path: explicit user_path, else
        look it up in the active meta's contents.xml (set via
        meta_dir -s <path>). Older targets may not ship this file at all —
        that's fine, the DTB load step is then skipped harmlessly.

        @param user_path  - dtbpath= value passed by the caller
        @return resolved path, or None if not found (DTB load is then skipped)
        """
        user_path = user_path.strip() if user_path else None
        if user_path:
            if not os.path.isfile(user_path):
                logger.warning(f"dtbpath={user_path!r} does not exist")
            return user_path

        from_contents = _find_file_in_contents_xml(self._DTB_FILENAME)
        if from_contents:
            logger.info(f"DTB resolved from contents.xml: {from_contents}")
            return from_contents

        logger.warning(
            f"No {self._DTB_FILENAME} found via contents.xml and no dtbpath= "
            "given — UFS probe may hang on targets that require a runtime "
            "DTB override. Pass dtbpath=<path> to specify, or run "
            "meta_dir -s <path> first so it can be resolved automatically."
        )
        return None

    # -----------------------------------------------------------------------
    # LLDB — symbol resolution only (no live process needed)
    # -----------------------------------------------------------------------

    def _lldb_symbol_address(self, name: str) -> int:
        """! Resolve a symbol's file address via LLDB. Raises if not found."""
        target = self.debugger.GetSelectedTarget()
        syms   = target.FindSymbols(name)
        if syms.IsValid() and syms.GetSize() > 0:
            addr = syms.GetContextAtIndex(0).GetSymbol().GetStartAddress().GetFileAddress()
            if addr != lldb.LLDB_INVALID_ADDRESS:
                return addr
        raise RuntimeError(f"Symbol not found: {name}")

    # -----------------------------------------------------------------------
    # OpenOCD telnet helpers
    # -----------------------------------------------------------------------

    def _tc_cmd(self, tc: TelnetClient, cmd: str, timeout: float = 1.0) -> str:
        """! Send one command over telnet and return its decoded response text."""
        tc.sock.sendall((cmd + "\n").encode("utf-8"))
        raw  = tc.read_with_timeout(timeout)
        text = raw.decode("utf-8", errors="ignore").strip() if raw else ""
        logger.debug(f"  > {cmd}")
        if text:
            logger.debug(f"  < {text[:200]}")
        return text

    def _parse_mdw_words(self, resp: str) -> list:
        """!
        Parse 32-bit words from an OpenOCD mdw response.

        OpenOCD echoes the command back before the data, e.g.:
            mdw 0x14828138 1        ← echo (ignored)
                                    ← blank
            0x14828138: 00000009    ← data line (parsed)
                                    ← blank
            >                       ← prompt (ignored)

        Only lines whose first token ends with ':' are data lines.

        @param resp  - raw telnet response text from an mdw command
        @return list of parsed 32-bit words
        """
        words = []
        for line in resp.splitlines():
            parts = line.split()
            if not parts:
                continue
            if not parts[0].endswith(":"):
                continue   # skip echo, prompt, blank lines
            for tok in parts[1:]:
                try:
                    val = int(tok, 16)
                    if 0 <= val <= 0xFFFFFFFF:
                        words.append(val)
                except ValueError:
                    continue
        return words

    # -----------------------------------------------------------------------
    # LLDB process memory helpers
    # Used after setup() — process is live via gdb-remote on APSS core.
    # No alignment handling needed — LLDB handles it internally.
    # Pattern from lldb_pkg/tech/QuRT/OSAM/utils.py:72-76
    # -----------------------------------------------------------------------

    def _get_process(self):
        """! Return the live LLDB process connected to the APSS core."""
        process = self.debugger.GetSelectedTarget().GetProcess()
        if not process or not process.IsValid():
            raise RuntimeError("No valid LLDB process — gdb-remote not connected")
        return process

    def _read_bytes(self, addr: int, length: int) -> bytes:
        """! Read raw bytes from target via LLDB process.ReadMemory."""
        error = lldb.SBError()
        data  = self._get_process().ReadMemory(addr, length, error)
        if error.Fail():
            raise RuntimeError(f"ReadMemory @ 0x{addr:08X} len={length}: {error.GetCString()}")
        return bytes(data)

    def _read_u32(self, addr: int) -> int:
        """! Read a 32-bit little-endian word from target via LLDB."""
        raw = self._read_bytes(addr, 4)
        if len(raw) < 4:
            raise RuntimeError(f"ReadMemory @ 0x{addr:08X}: got {len(raw)} bytes")
        return struct.unpack_from("<I", raw)[0]

    def _read_u64(self, addr: int) -> int:
        """! Read a 64-bit little-endian value from target via LLDB."""
        raw = self._read_bytes(addr, 8)
        if len(raw) < 8:
            raise RuntimeError(f"ReadMemory @ 0x{addr:08X}: got {len(raw)} bytes")
        return struct.unpack_from("<Q", raw)[0]

    def _write_u32(self, addr: int, value: int):
        """! Write a 32-bit little-endian word to target via LLDB."""
        data  = struct.pack("<I", value & 0xFFFFFFFF)
        error = lldb.SBError()
        self._get_process().WriteMemory(addr, data, error)
        if error.Fail():
            raise RuntimeError(f"WriteMemory @ 0x{addr:08X}: {error.GetCString()}")

    def _write_u64(self, addr: int, value: int):
        """! Write a 64-bit little-endian value to target via LLDB."""
        data  = struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF)
        error = lldb.SBError()
        self._get_process().WriteMemory(addr, data, error)
        if error.Fail():
            raise RuntimeError(f"WriteMemory @ 0x{addr:08X}: {error.GetCString()}")

    _SIZED_INT_FORMATS = {1: "<B", 2: "<H", 4: "<I", 8: "<Q"}

    def _write_sized_int(self, addr: int, value: int, size: int):
        """!
        Write value to addr using exactly `size` bytes, little-endian.
        Mirrors T32 `v.set <field>=<value>`, which writes exactly the
        field's declared width instead of rounding up to 4 or 8 bytes.

        @param addr   - target address to write
        @param value  - integer value to write
        @param size   - field width in bytes (1, 2, 4, or 8)
        """
        fmt = self._SIZED_INT_FORMATS.get(size)
        if fmt is None:
            raise RuntimeError(f"Unsupported field size {size} @ 0x{addr:08X}")
        data  = struct.pack(fmt, value & ((1 << (size * 8)) - 1))
        error = lldb.SBError()
        self._get_process().WriteMemory(addr, data, error)
        if error.Fail():
            raise RuntimeError(f"WriteMemory @ 0x{addr:08X}: {error.GetCString()}")

    # -----------------------------------------------------------------------
    # Telnet memory helpers — used only during _do_setup() before gdb-remote
    # connects. Kept separate so setup can still use them with a tc handle.
    # -----------------------------------------------------------------------

    def _tc_read_bytes(self, tc: TelnetClient, addr: int, length: int) -> bytes:
        """! Read raw bytes during setup via telnet mdw (alignment-safe)."""
        offset  = addr & 3
        aligned = addr - offset
        n_words = (offset + length + 3) // 4
        resp    = self._tc_cmd(tc, f"mdw 0x{aligned:08X} {n_words}", timeout=5.0)
        words   = self._parse_mdw_words(resp)
        raw     = b"".join(struct.pack("<I", w) for w in words)
        return raw[offset:offset + length]

    def _tc_read_u32(self, tc: TelnetClient, addr: int) -> int:
        """! Read a 32-bit word during setup via telnet."""
        raw = self._tc_read_bytes(tc, addr, 4)
        if len(raw) < 4:
            raise RuntimeError(f"mdw parse failed @ 0x{addr:08X}: got {len(raw)} bytes")
        return struct.unpack_from("<I", raw)[0]

    def _tc_write_u32(self, tc: TelnetClient, addr: int, value: int):
        """! Write a 32-bit word during setup via telnet."""
        self._tc_cmd(tc, f"mww 0x{addr:08X} 0x{value:08X}")

    # -----------------------------------------------------------------------
    # Breakpoint / execution helpers
    # Used only during _do_setup() via telnet — before gdb-remote connects.
    # -----------------------------------------------------------------------

    def _set_bp(self, tc: TelnetClient, addr: int):
        """! Arm a hardware breakpoint at addr via telnet."""
        self._tc_cmd(tc, f"bp 0x{addr:08X} 4 hw")

    def _remove_bp(self, tc: TelnetClient, addr: int):
        """! Remove a hardware breakpoint at addr via telnet."""
        self._tc_cmd(tc, f"rbp 0x{addr:08X}")

    def _step_past_bp(self, tc: TelnetClient, addr: int):
        """! rbp → step → bp  (OpenOCD won't step through an armed bp)"""
        self._remove_bp(tc, addr)
        self._tc_cmd(tc, "step")
        self._set_bp(tc, addr)

    def _wait_halted(self, tc: TelnetClient, timeout_s: float = 30.0,
                     poll_s: float = 0.5, pc_every_s: float = 5.0) -> bool:
        """! Poll telnet until the target halts or timeout_s elapses."""
        deadline = time.time() + timeout_s
        last_pc_log = time.time()
        while time.time() < deadline:
            time.sleep(poll_s)
            resp = self._tc_cmd(tc, "poll", timeout=3.0)
            if "halted" in resp.lower():
                return True
            if time.time() - last_pc_log >= pc_every_s:
                last_pc_log = time.time()
                pc_resp = self._tc_cmd(tc, "reg pc", timeout=3.0)
                logger.info(f"  [still waiting]  poll={resp.strip()!r}  reg pc -> {pc_resp.strip()!r}")
        return False

    # -----------------------------------------------------------------------
    # LLDB execution control helpers
    # Used after setup() — process is live via gdb-remote on APSS core.
    # Pattern from lldb_pkg/image/tme.py:36-52 (wait_for_breakpoint_hit)
    # -----------------------------------------------------------------------

    def _lldb_set_bp(self, addr: int) -> lldb.SBBreakpoint:
        """! Set a hardware breakpoint at addr via LLDB. Returns the bp object."""
        target = self.debugger.GetSelectedTarget()
        bp = target.BreakpointCreateByAddress(addr)
        bp.SetIsHardware(True)
        bp.SetOneShot(True)
        logger.debug(f"  LLDB bp set @ 0x{addr:08X}  id={bp.GetID()}")
        return bp

    def _lldb_remove_bp(self, bp: lldb.SBBreakpoint):
        """! Remove a breakpoint set by _lldb_set_bp."""
        target = self.debugger.GetSelectedTarget()
        target.BreakpointDelete(bp.GetID())
        logger.debug(f"  LLDB bp removed id={bp.GetID()}")

    def _lldb_wait_halted(self, timeout_s: float = 30.0) -> bool:
        """! Poll process state until stopped at a breakpoint or timeout_s elapses."""
        process  = self._get_process()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            state = process.GetState()
            if state in (lldb.eStateExited, lldb.eStateCrashed, lldb.eStateDetached):
                logger.error(f"Process entered terminal state: {state}")
                return False
            if state == lldb.eStateStopped:
                return True
            time.sleep(0.01)
        return False

    def _lldb_step_past_bp(self, bp: lldb.SBBreakpoint) -> lldb.SBBreakpoint:
        """! Remove bp → step one instruction → re-arm bp. Returns new bp object."""
        addr = bp.GetLocationAtIndex(0).GetLoadAddress()
        # self._lldb_remove_bp(bp)
        thread = self._get_process().GetSelectedThread()
        thread.StepInstruction(False)
        return self._lldb_set_bp(addr)

    def _lldb_resume_and_wait(self, timeout_s: float = 30.0) -> bool:
        """! Continue process and wait for halt. process.Continue() + _lldb_wait_halted."""
        error = self._get_process().Continue()
        if error.Fail():
            logger.error(f"process.Continue() failed: {error.GetCString()}")
            return False
        return self._lldb_wait_halted(timeout_s=timeout_s)

    # -----------------------------------------------------------------------
    # Setup
    # -----------------------------------------------------------------------

    def setup(self):
        """!
        Load ELF, run firmware to main_c, drain UFS init call. Leaves
        firmware halted at main_c breakpoint ready for commands. Opens
        and closes its own TelnetClient — firmware stays halted after.
        """


        with TelnetClient() as tc:
            self._do_setup(tc)
        # Telnet is now closed. Connect gdb-remote only after telnet is done
        # so LLDB background polling cannot race with telnet operations.
        # Use bare gdb-remote lookup — connect_remote assumes a full boot debug
        # session (adapter, protocol, meta, telematics) which is not our case.
        # In EDL the APSS core is already halted; we just need the port.
        from utils.debug_context import g_dbgContext
        g_dbgContext.getDbgPortNum("apss3")
        if g_dbgContext.port:
            self.debugger.HandleCommand(f"gdb-remote localhost:{g_dbgContext.port}")
            logger.info(f"LLDB gdb-remote connected to APSS on port {g_dbgContext.port}")
            # Arm the persistent main_c breakpoint via LLDB now that process is live.
            # This replaces the telnet bp that was set during _do_setup — LLDB owns
            # breakpoints from this point forward.
            self._main_c_bp = self._lldb_set_bp(self.addr_main_c)
        else:
            logger.warning("Could not resolve APSS gdb port from chip_config.xml — LLDB memory access unavailable")

    def _do_setup(self, tc: TelnetClient):
        """!
        Load the ELF, reset APSS core3, run the firmware to main_c, and
        drain the UFS init call — all over telnet (gdb-remote is not yet
        connected). Called once by setup() inside a TelnetClient context.

        @param tc  - open TelnetClient used for all setup-time commands
        """
        target = self.debugger.CreateTarget(self.elf_path)
        if not target.IsValid():
            raise RuntimeError(f"Failed to create LLDB target from: {self.elf_path}")
        logger.info(f"LLDB target created from {self.elf_path}")

        # Derive OpenOCD CPU target name from chipset name in chip_config.xml.
        # Matches the pattern in every *.cfg:  "$_CHIPNAME.apss.core3"
        # g_dbgContext.chipsetName is populated by openocd_lldb.py at launch.
        if not g_dbgContext.chipsetName:
            raise RuntimeError("g_dbgContext.chipsetName is not set — run set_chipset before jtagprogrammer")
        target_cpu = f"{g_dbgContext.chipsetName.upper()}.apss.core3"

        # Resolve ELF entry point — replaces hardcoded 0x14800000.
        # e_entry is the reset vector written into the ELF by the linker;
        # it is correct for any JtagProgrammer build regardless of target.
        elf_module   = target.GetModuleAtIndex(0)
        entry_addr   = elf_module.GetObjectFileEntryPointAddress().GetLoadAddress(target)
        if entry_addr == lldb.LLDB_INVALID_ADDRESS:
            entry_addr = elf_module.GetObjectFileEntryPointAddress().GetFileAddress()
        if entry_addr == lldb.LLDB_INVALID_ADDRESS:
            raise RuntimeError("Could not resolve ELF entry point address")

        main_c_addr        = self._lldb_symbol_address("main_c")
        param_addr         = self._lldb_symbol_address("jtag_prog_param")
        dev_type_addr      = self._lldb_symbol_address("device_type")
        slot_addr          = self._lldb_symbol_address("card_slot_in_use")
        ob_addr            = self._lldb_symbol_address("output_buffer")
        ob_len_addr        = self._lldb_symbol_address("output_buffer_length")
        block_size_addr    = self._lldb_symbol_address("BLOCK_SIZE")
        max_part_addr      = self._lldb_symbol_address("max_partitions")
        part_names_addr    = self._lldb_symbol_address("partition_names")
        storage_extras_addr = self._lldb_symbol_address("storage_extras")

        # Resolve jtag_prog_param field offsets via LLDB DWARF type system.
        # Use file address delta (child - parent) — works on a static target
        # before gdb-remote connects. GetLoadAddress() would return invalid here.
        # Mirrors T32: v.set jtag_prog_param.cmd_status which T32 resolves from DWARF.
        param_var = target.FindFirstGlobalVariable("jtag_prog_param")
        if not param_var.IsValid():
            raise RuntimeError("jtag_prog_param DWARF resolution failed — cannot resolve field offsets")
        base_fa = param_var.GetAddress().GetFileAddress()
        def _field_offset(name):
            return param_var.GetChildMemberWithName(name).GetAddress().GetFileAddress() - base_fa
        cmd_status_addr  = param_addr + _field_offset("cmd_status")
        param_addr_field = param_addr + _field_offset("addr")
        data_len_addr    = param_addr + _field_offset("data_length")
        param_data_addr  = param_addr + _field_offset("data")
        reserved_addr    = param_addr + _field_offset("reserved")
        logger.info(
            f"jtag_prog_param fields resolved via LLDB DWARF:\n"
            f"  cmd_status  offset=0x{_field_offset('cmd_status'):X}  addr=0x{cmd_status_addr:08X}\n"
            f"  addr        offset=0x{_field_offset('addr'):X}  addr=0x{param_addr_field:08X}\n"
            f"  data_length offset=0x{_field_offset('data_length'):X}  addr=0x{data_len_addr:08X}\n"
            f"  data        offset=0x{_field_offset('data'):X}  addr=0x{param_data_addr:08X}\n"
            f"  reserved    offset=0x{_field_offset('reserved'):X}  addr=0x{reserved_addr:08X}"
        )

        logger.info(
            f"Symbols:\n"
            f"  main_c           = 0x{main_c_addr:08X}\n"
            f"  jtag_prog_param  = 0x{param_addr:08X}\n"
            f"  cmd_status       = 0x{cmd_status_addr:08X}\n"
            f"  device_type      = 0x{dev_type_addr:08X}\n"
            f"  card_slot_in_use = 0x{slot_addr:08X}\n"
            f"  output_buffer    = 0x{ob_addr:08X}\n"
            f"  BLOCK_SIZE       = 0x{block_size_addr:08X}"
        )

        # Step 1: Reset APSS core3 to the true silicon reset vector and halt
        # it there, before the ELF load. On Maili, LLDB's "qreset" command
        # runs the CPUCP PBL bring-up (init_cpucp_maili), which ends by
        # telling OpenOCD to shut down and waits for the supervised relaunch
        # to come back up. That kills the telnet TCP connection `tc` was
        # opened on, so it must be reconnected before reuse below.
        # self.debugger.HandleCommand("qreset")

        ResetCore(self.debugger)
        if not pbl_load_fbc():
            logger.error(" --- FBC: APSS Core3 bring-up failed, aborting XBL debug")
            sys.exit(1)
        tc.close()
        tc.connect()

        # Step 2: Load ELF via the APSS CPU target now that qreset has halted
        # before PBL/boot ROM executes.
        self._tc_cmd(tc, f"targets {target_cpu}")

        elf_fwd = self.elf_path.replace("\\", "/")
        logger.info(f"Loading ELF: {elf_fwd}")
        resp = self._tc_cmd(tc, f"load_image {elf_fwd} 0 elf", timeout=60.0)
        logger.info(f"load_image: {resp}")

        spot = self._tc_read_u32(tc, entry_addr)
        logger.info(f"ELF spot-check @ 0x{entry_addr:08X} = 0x{spot:08X}")
        if spot in (0x00000000, 0xFFFFFFFF):
            raise RuntimeError(f"ELF load may have failed: 0x{entry_addr:08X} = 0x{spot:08X}")

        # Step 2b: Load UFS target-config DTB into dtb_buffer, overriding the
        # ELF's built-in default. Must happen before main_c/do_probe_ufs runs —
        # a mismatched DTB can hang UFS link training.
        try:
            dtb_buffer_addr = self._lldb_symbol_address("dtb_buffer")
        except RuntimeError:
            dtb_buffer_addr = None

        if dtb_buffer_addr is not None and self.dtb_path:
            dtb_fwd = self.dtb_path.replace("\\", "/")
            logger.info(f"Loading UFS DTB: {dtb_fwd} -> 0x{dtb_buffer_addr:08X}")
            resp = self._tc_cmd(tc, f"load_image {dtb_fwd} 0x{dtb_buffer_addr:08X} bin", timeout=30.0)
            logger.info(f"load_image (dtb): {resp}")

            # Verify write landed — FDT magic is 0xd00dfeed at offset 0.
            magic = self._tc_read_u32(tc, dtb_buffer_addr)
            if magic == 0xEDFE0DD0:  # little-endian readback of d0 0d fe ed
                logger.info(f"DTB write verified — FDT magic OK @ 0x{dtb_buffer_addr:08X}")
            else:
                logger.error(
                    f"DTB write verification FAILED @ 0x{dtb_buffer_addr:08X}: "
                    f"read 0x{magic:08X}, expected 0xEDFE0DD0 (FDT magic). "
                    "dtb_buffer likely still holds the ELF's placeholder — UFS probe may hang."
                )
        elif dtb_buffer_addr is not None:
            logger.warning(
                "dtb_buffer symbol present but no DTB file resolved — "
                "keeping the ELF's built-in default DTB. Pass dtbpath=<path> if UFS probe hangs."
            )

        # Step 3: Redirect PC to ELF entry point — qreset halted at the
        # silicon reset vector, not the ELF's entry point.
        self._tc_cmd(tc, f"reg pc 0x{entry_addr:08X}")

        # Step 4: Arm main_c bp, resume to first call
        self._set_bp(tc, main_c_addr)
        self._tc_cmd(tc, "resume")
        if not self._wait_halted(tc, timeout_s=30.0):
            raise RuntimeError("Timed out waiting for first halt at main_c")
        logger.info("Halted at main_c — first call")

        # Step 5: Write device_type, slot, zero cmd_status (avoids timing=0x100 trap)
        self._tc_write_u32(tc, dev_type_addr,  self.dev_type)
        self._tc_write_u32(tc, slot_addr,       self.card_slot)
        self._tc_write_u32(tc, cmd_status_addr, 0x0)

        # Step 6: Step past bp, resume — first call runs do_probe (UFS init)
        self._step_past_bp(tc, main_c_addr)
        self._tc_cmd(tc, "resume")
        if not self._wait_halted(tc, timeout_s=60.0):
            raise RuntimeError("Timed out waiting for halt at main_c after UFS init")
        logger.info("Halted at main_c — UFS init complete")

        # Step 7: Store addresses
        self.addr_main_c            = main_c_addr
        self.addr_param             = param_addr
        self.addr_cmd_status        = cmd_status_addr
        self.addr_param_addr        = param_addr_field
        self.addr_data_length       = data_len_addr
        self.addr_param_data        = param_data_addr
        self.addr_reserved          = reserved_addr
        self.addr_output_buffer     = ob_addr
        self.addr_output_buffer_len = ob_len_addr
        self.addr_block_size        = block_size_addr
        self.addr_max_partitions    = max_part_addr
        self.addr_partition_names   = part_names_addr
        self.addr_card_slot         = slot_addr
        self.addr_storage_extras    = storage_extras_addr

        # Step 8: Read BLOCK_SIZE — confirms firmware is live
        self.sector_size = self._tc_read_u32(tc, block_size_addr)
        logger.info(f"BLOCK_SIZE = {self.sector_size}  setup complete")

        # partition_info_read stays False until SwitchPartition is called.
        # Mirrors CMM: &CurrentPartitionInfoRead=0.

    # -----------------------------------------------------------------------
    # Command protocol
    # -----------------------------------------------------------------------

    def _issue_command(self, _tc, cmd: int,
                       addr: int = None, data_length: int = None,
                       timeout_s: float = 30.0) -> bool:
        """!
        Write cmd_status, step past main_c bp, resume, wait for halt, check
        result. Mirrors CMM: v.set cmd_status → go → wait !run() → check result.

        @param cmd          - command code to write to cmd_status
        @param addr         - value for jtag_prog_param.addr, or None to skip
        @param data_length  - value for jtag_prog_param.data_length, or None to skip
        @param timeout_s    - max time to wait for the firmware to halt
        @return True on success, False on timeout or firmware-reported failure

        addr/data_length default to None (not written) rather than 0 so that
        callers passing addr=0 (partition 0, sector 0) always write the field.
        """
        self._write_u64(self.addr_cmd_status, cmd)
        if addr is not None:        self._write_u64(self.addr_param_addr,  addr)
        if data_length is not None: self._write_u64(self.addr_data_length, data_length)

        # Step past bp, resume, wait for halt — all via LLDB
        self._main_c_bp = self._lldb_step_past_bp(self._main_c_bp)
        if not self._lldb_resume_and_wait(timeout_s=timeout_s):
            logger.error(f"Timed out waiting for cmd 0x{cmd:02X}")
            return False

        result = self._read_u64(self.addr_cmd_status)
        if result == self._STATUS_FAILURE:
            ext = self._read_u64(self.addr_reserved)
            logger.error(f"cmd 0x{cmd:02X} FAILED  cmd_status=0x{result:X}  extended=0x{ext:X}")
            return False

        logger.info(f"cmd 0x{cmd:02X} OK  (cmd_status=0x{result:X})")
        return True

    def _read_output_buffer(self, _tc) -> str:
        """! Read firmware output_buffer, reset length. Mirrors CMM printOutputBuffer."""
        length = self._read_u32(self.addr_output_buffer_len)
        if not length:
            return ""
        length = min(length, 2048)
        text   = self._read_bytes(self.addr_output_buffer, length).decode("ascii", errors="replace")
        self._write_u32(self.addr_output_buffer_len, 0)
        return text

    # -----------------------------------------------------------------------
    # Commands
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Menu
    # -----------------------------------------------------------------------

    _MENU = """\


       JTAG PROGRAMMER FOR UFS


  Please type HELP for automation examples

  0: Exit
  1: Read sector
  2: Write sector
  3: Download program
  4: Print Memory Card Info
  5: Quick Debug
  6: Erase Card
  7: Switch Hardware Partition
  8: Switch card slot to use
  9: Set active boot partition

  DUMP      : Dump sectors to file
  PROGRAM   : Program specific file from 'rawprogram0.xml'
  ZEROOUT   : Zero out specific regions from 'rawprogram0.xml'
  LOAD      : Load all files from 'rawprogram0.xml'
  READBYTES : READBYTES numbytes=5 sector=0 offset=0 <-- Reads first 5 bytes of sector 0
  PATCH     : Apply all patches from patch XML (xml=patch0.xml[,patch1.xml,...])
  ERASE     : Erase the partition (requires YES, e.g. 'ERASE YES')
            Issue 'erase' enabling card controller to erase a partition
  PROVISION : Storage Extras (creating GPPs/LUNs] [WARNING! DESTRUCTIVE, PERMANENT & IRREVERSIBLE]
  VERIFY    : Dry-run PROVISION - show what values it would set, without writing anything

  HELP      : Display usage and automation info
"""

    def _print_menu(self):
        """! Print the interactive menu and the current partition/slot status line."""
        print(self._MENU)
        print(f"On HW partition: {self.current_hw_part_name}, Card slot {self.current_slot}")
        print()
        print("Please make a choice: ", end="", flush=True)

    def _dispatch(self, choice: str, arg_line: str = "") -> bool:
        """!
        Execute one menu choice. Returns False when the user selects Exit (0).
        Mirrors the CMM if/goto chain from MainMenu through LoopBackToMain.

        @param choice        - upper-case verb or number string
        @param arg_line      - remaining argument string for the chosen command
        @return False on Exit (0), True otherwise
        """
        # targets switching no longer needed — LLDB gdb-remote is pinned to the
        # chipset's apss.core3 target and does not use OpenOCD's target selection.

        # 0 — Exit
        if choice == "0":
            print("...exiting UFS Flash Program")
            return False

        # 1 — Read sector
        if choice == "1":
            print("\nRead sector\n")
            self._cmd_read_sector(arg_line)
            return True

        # 2 — Write sector
        if choice == "2":
            print("\nWrite sector\n")
            self._cmd_write_sector(arg_line)
            return True

        # 3 — Download program
        if choice == "3":
            print("\nDownload program...\n")
            self._cmd_load_program(arg_line)
            return True

        # 4 — Print Memory Card Info
        if choice == "4":
            print("\nPrint Memory Shared Information...\n")
            output = self.cmd_print_mem_info()
            print(output)
            return True

        # 5 — Quick Debug
        if choice == "5":
            print("\nQuick debug script\n")
            self._cmd_quick_debug()
            return True

        # FFU — Firmware update
        if choice == "FFU":
            print("\nDownload firmware...\n")
            self._cmd_load_ffu()
            return True

        # 6 / ERASE — Erase entire User partition (requires "YES" argument)
        if choice in ("6", "ERASE"):
            if "YES" not in arg_line.upper().split():
                print(f"\nMissing confirmation. Run: jtagprogrammer {choice} YES\n")
                return True
            print("      ")
            print(r"      __        __               _             ")
            print(r"      \ \      / /_ _ _ __ _ __ (_)_ __   __ _ ")
            print(r"       \ \ /\ / / _` | '__| '_ \| | '_ \ / _` |")
            print(r"        \ V  V / (_| | |  | | | | | | | | (_| |")
            print(r"         \_/\_/ \__,_|_|  |_| |_|_|_| |_|\__, |")
            print(r"                                         |___/ ")
            print("\n\n")
            print("Erasing entire User partition..... This may take several minutes to complete.")
            sys.stdout.flush()
            res = self._cmd_erase_chip()
            if res:
                logger.info("Erasing complete")
            else:
                logger.error("Erasing failed")
            return True

        # 7 — Switch Hardware Partition
        if choice == "7":
            print("\nSwitch Partition\n")
            self._cmd_switch_partition(arg_line)
            return True

        # 8 — Switch card slot
        if choice == "8":
            print("\nSelect which card slot to use.\n")
            self._cmd_select_card_slot(arg_line)
            return True

        # 9 — Set active boot partition
        if choice == "9":
            print("\nSelect the partition as active boot partition\n")
            self._cmd_select_active_partition(arg_line)
            return True

        # 20 — Debug: Clock frequency scan
        if choice == "20":
            print("\nDebug - Clock frequency scan.\n")
            self._cmd_not_implemented(choice)
            return True

        # 21 — Debug: Single block RX test
        if choice == "21":
            print("\nDebug - Single block RX test.\n")
            self._cmd_not_implemented(choice)
            return True

        # DUMP — Dump sectors to file
        if choice == "DUMP":
            self._cmd_dump(arg_line)
            return True

        # PROGRAM — Program specific file from rawprogram XML
        if choice == "PROGRAM":
            print()
            print()
            print("Program specific file from 'rawprogram0.xml'.")
            print()
            self._cmd_program(arg_line)
            return True

        # ZEROOUT — Zero out specific regions from rawprogram XML
        if choice == "ZEROOUT":
            print()
            print()
            print("Erase specific region from 'rawprogram0.xml'.")
            print()
            self._cmd_zeroout(arg_line)
            return True

        # READBYTES — Read raw bytes from a sector
        if choice == "READBYTES":
            print("\nREADBYTES\n")
            self._cmd_readbytes(arg_line)
            return True

        # LOAD — Program all partitions from rawprogram XML
        if choice == "LOAD":
            print("\nProgram all partitions from 'rawprogram0.xml'.\n")
            self._cmd_load(arg_line)
            return True

        # PATCH — Apply patches from patch XML
        if choice == "PATCH":
            print()
            print()
            xml_arg = _parse_arg("xml", arg_line)
            label = xml_arg if xml_arg else "patch XML"
            print(f"Apply all patches from '{label}'.")
            print()
            self._cmd_patch(arg_line)
            return True

        # PROVISION — Storage Extras (LUN/GPP provisioning)
        if choice == "PROVISION":
            print("\nProcessing XML to provision UFS")
            print("WARNING! DESTRUCTIVE, PERMANENT & IRREVERSIBLE\n")
            self._cmd_provision(arg_line)
            return True

        # VERIFY — dry-run PROVISION: show what values it would set, no writes
        if choice == "VERIFY":
            print("\nProcessing XML to verify what PROVISION would set")
            print("(dry run — no values are written to the target)\n")
            self._cmd_provision(arg_line, dry_run=True)
            return True

        # HELP
        if choice == "HELP":
            self._print_help()
            return True

        print(f"\n  Unknown choice: '{choice}'\n")
        return True

    # -----------------------------------------------------------------------
    # Menu helpers
    # -----------------------------------------------------------------------

    def _print_help(self):
        """! Print argument reference and automation examples for the HELP command."""
        print("""
Arguments:
[NOTE: Commas separate search paths and files]
===================================================
    xml=<filename>
    slot=0|1|2|3
    files=file1,file2,file3
    searchpaths=path1,path2,path3

Automation examples:
===================================================
    Erase the user partition
        jtagprogrammer ERASE YES
    Program ALL files from rawprogram0.xml
        jtagprogrammer LOAD xml=rawprogram0.xml
    Apply ALL disk patches from patch0.xml
        jtagprogrammer PATCH xml=patch0.xml
    Show what PROVISION would set, without writing anything
        jtagprogrammer VERIFY xml=provision.xml
""")

    @staticmethod
    def _cmd_not_implemented(choice: str):
        """! Print a stub message for a menu choice with no implementation yet."""
        print(f"  '{choice}' is not yet implemented.")

    # -----------------------------------------------------------------------
    # Command implementations
    # -----------------------------------------------------------------------

    def _locate_file(self, filename: str, search_dirs: list) -> str:
        """! Find filename in search_dirs. Mirrors CMM LocateFile gosub. Returns None if not found."""
        for d in search_dirs:
            candidate = os.path.join(d, filename)
            if os.path.isfile(candidate):
                return candidate
        return None

    def _write_file_at_sector(self, filepath: str, file_sector_offset: int,
                               num_partition_sectors: int, start_sector: int) -> bool:
        """!
        Write a file to UFS via a WRITE_SECTOR loop, one sector per call.
        Mirrors CMM WriteFileAtSector's DataLength calculation exactly, but
        uses WRITE_SECTOR (0x0A) for both small and large files instead of
        CMM's WriteSmallFile/flash.program split.

        @param filepath                - file to write
        @param file_sector_offset      - sector offset into the file to start reading from
        @param num_partition_sectors   - partition size in sectors (0 for the last partition)
        @param start_sector            - destination sector on UFS
        @return True on success, False on read/write failure
        """
        file_size = os.path.getsize(filepath)
        if file_size == 0:
            print(f"ERROR: Size of file {os.path.basename(filepath)} is 0")
            return False

        data_length = file_size

        # DataLength calculation — mirrors CMM WriteFileAtSector exactly
        if (file_sector_offset + num_partition_sectors) * self.sector_size > file_size:
            data_length = data_length - (file_sector_offset * self.sector_size)
            # Round up to sector boundary only for small files — mirrors CMM comment:
            # "flash.program does not work for small writes, it fails silently"
            if data_length <= self.sector_size:
                remainder = file_size % self.sector_size
                if remainder != 0:
                    data_length = data_length + (self.sector_size - remainder)
        else:
            data_length = num_partition_sectors * self.sector_size

        file_offset = file_sector_offset * self.sector_size

        # Handle last partition case where num_partition_sectors could be zero
        if data_length == 0:
            data_length = file_size
            if data_length <= self.sector_size:
                remainder = file_size % self.sector_size
                if remainder != 0:
                    data_length = data_length + (self.sector_size - remainder)

        if data_length <= 0:
            print(f"ERROR: Computed data_length <= 0 for {os.path.basename(filepath)}")
            return False

        print(f"  Writing {os.path.basename(filepath)}"
              f"  data_length={data_length}  start_sector={start_sector}")

        # One sector per WRITE_SECTOR call: param.data = chunk, param.addr = sector
        sector = start_sector
        with open(filepath, "rb") as f:
            f.seek(file_offset)
            bytes_remaining = data_length
            while bytes_remaining > 0:
                chunk = f.read(self.sector_size)
                if not chunk:
                    break
                # zero-pad last chunk — mirrors CMM: data.set ParmData 0x0 before load
                chunk = chunk.ljust(self.sector_size, b"\x00")

                error = lldb.SBError()
                self._get_process().WriteMemory(self.addr_param_data, chunk, error)
                if error.Fail():
                    print(f"  WriteMemory failed @ sector {sector}: {error.GetCString()}")
                    return False

                ok = self._issue_command(None, Cmd.WRITE_SECTOR,
                                         addr=sector, data_length=1)
                if not ok:
                    print(f"  WRITE_SECTOR failed @ sector {sector}")
                    return False

                sector          += 1
                bytes_remaining -= self.sector_size

        print(f"  {os.path.basename(filepath)} written"
              f"  ({sector - start_sector} sectors)")
        return True

    def _raw_program(self, operation: int, specific_number, xml_path: str,
                     search_dirs: list) -> int:
        """!
        Mirrors CMM RawProgram gosub — all 6 operations.
        operation=0  list all entries (files + partitions)
        operation=1  program specific file by file number
        operation=2  erase specific partition by partition number
        operation=3  program all files (specific_number=0) or one filename
        operation=4  list files only
        operation=5  list partitions only

        @param operation       - operation code, see above
        @param specific_number - file number, partition number, or filename (op-dependent)
        @param xml_path        - rawprogram XML to read entries from
        @param search_dirs     - directories to search for referenced files
        @return write_count (files written or erased)
        """
        import xml.etree.ElementTree as ET

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except Exception as e:
            print(f"Failed to parse XML {xml_path}: {e}")
            return 0

        print()
        print()

        write_count     = 0
        file_count      = 0
        partition_count = 0

        for entry in root.iter("program"):
            filename = entry.get("filename", "")

            # parse all fields for every entry — mirrors CMM which reads all fields
            # before branching on operation
            file_sector_offset    = int(entry.get("file_sector_offset",
                                        entry.get("offset", "0")), 0)
            label                 = entry.get("label", "")
            num_partition_sectors = int(entry.get("num_partition_sectors",
                                        entry.get("num_sectors", "0")), 0)
            physical_partition    = int(entry.get("physical_partition_number", "0"), 0)
            size_in_kb            = float(entry.get("size_in_KB", "0") or "0")
            start_sector_str      = entry.get("start_sector", "0")

            # EvalCardSectorsExpr on start_sector — mirrors CMM which evaluates
            # for ALL operations, not just operation 3
            if "NUM_DISK_SECTORS" in start_sector_str:
                target   = self.debugger.GetSelectedTarget()
                mem_info = target.FindFirstGlobalVariable("current_mem_info_ufs")
                if mem_info.IsValid():
                    total_blocks = mem_info.GetChildMemberWithName(
                        "dLuTotalBlocks").GetValueAsUnsigned()
                    logger.debug(f"NUM_DISK_SECTORS resolved via DWARF: {total_blocks}")
                else:
                    logger.warning("current_mem_info_ufs not found — cannot resolve NUM_DISK_SECTORS")
                    continue
                start_sector_str = start_sector_str.replace("NUM_DISK_SECTORS",
                                                              str(total_blocks))
            try:
                start_sector = int(eval(start_sector_str.rstrip(".")))
            except Exception:
                print(f"  Could not evaluate start_sector: {start_sector_str!r}")
                continue

            # increment counters — mirrors CMM which counts regardless of operation
            if filename:
                file_count += 1
            else:
                partition_count += 1

            # SwitchPartition if needed — mirrors CMM guard (all operations)
            if physical_partition != self.current_hw_partition or not self.partition_info_read:
                ok = self._issue_command(None, Cmd.SET_HW_PARTITION,
                                         addr=physical_partition)
                if not ok:
                    print(f"  Failed to switch to partition {physical_partition}")
                    return write_count
                self.current_hw_partition = physical_partition
                self.partition_info_read  = True
                self.cmd_print_mem_info()
                logger.info(f"Switched to partition {physical_partition}")

            # --- operation 0: list all ---
            if operation == 0:
                if filename:
                    print(f"#{file_count} '{filename}' on partition {physical_partition} '{label}' at sector {start_sector}")
                else:
                    print(f"Partition # {partition_count} '{filename}' on partition {physical_partition} '{label}' at sector {start_sector}")

            # --- operation 4: list files only ---
            if operation == 4:
                if filename:
                    print(f"#{file_count} '{filename}' on partition {physical_partition} '{label}' at sector {start_sector}")

            # --- operation 5: list partitions only ---
            if operation == 5:
                if not filename:
                    print(f"#{partition_count} on partition {physical_partition} '{label}' at sector {start_sector} ({num_partition_sectors} sectors)")

            # --- operation 1: program specific file by number ---
            if operation == 1:
                if specific_number == file_count:
                    filepath = self._locate_file(filename, search_dirs)
                    if not filepath:
                        print(f"  File not found: {filename}")
                        return write_count
                    ok = self._write_file_at_sector(
                        filepath, file_sector_offset,
                        num_partition_sectors, start_sector)
                    return write_count + (1 if ok else 0)

            # --- operation 2: erase specific partition by number ---
            if operation == 2:
                if specific_number == partition_count:
                    ok = self._erase_sector(start_sector, num_partition_sectors)
                    return write_count + (1 if ok else 0)

            # --- operation 3: program all or matching filename ---
            if operation == 3:
                if not filename:
                    continue
                if specific_number != 0 and specific_number != filename:
                    continue

                filepath = self._locate_file(filename, search_dirs)
                if not filepath:
                    print(f"  File not found: {filename}")
                    print(f"  Searched: {search_dirs}")
                    continue

                print(f"Writing file {filename}  partition={physical_partition}"
                      f"  label={label!r}  start_sector={start_sector}")

                ok = self._write_file_at_sector(filepath, file_sector_offset,
                                                num_partition_sectors, start_sector)
                if not ok:
                    print(f"  FAILED writing {filename}")
                    return write_count
                write_count += 1

        # end-of-loop error messages — mirrors CMM exactly
        if operation == 3 and write_count == 0 and specific_number != 0:
            print(f"ERROR: The file {specific_number!r} was not found in {xml_path}.")

        if operation == 1:
            print("ERROR: The file number was not found.")
            print("NOTE: This file number input is collected as an INTEGER!!!")
            print("do not forget 0x or . to define radix")
            print("Ex. To select 12 type 12. or 0xC ::")

        if operation == 2:
            print("ERROR: The partition number was not found.")
            print("NOTE: This partition number input is collected as an INTEGER!!!")
            print("do not forget 0x or . to define radix")
            print("Ex. To select 12 type 12. or 0xC ::")

        return write_count

    def _cmd_load(self, arg_line: str = ""):
        """!
        LOAD command — mirrors CMM LOAD choice handler.
        Usage: jtagprogrammer LOAD xml=<path> [files=f1,f2,...] [searchpaths=p1,p2,...]

        files= not given  → write all files in XML  (specific_number=0)
        files= given      → write only listed files, one _raw_program call per file

        @param arg_line  - LOAD command argument string
        """
        xml_path = _parse_arg("xml", arg_line)
        if not xml_path:
            print("  Pass xml=<path>, e.g.:  jtagprogrammer LOAD xml=rawprogram0.xml"
                  " searchpaths=//server/path")
            return

        search_arg  = _parse_arg("searchpaths", arg_line)
        search_dirs = [s.strip() for s in search_arg.split(",") if s.strip()] \
                      if search_arg else []

        # always include the XML's own directory — mirrors CMM where XML_Location
        # is always first in searchpaths (gpt bins live next to the XML)
        xml_dir = os.path.dirname(os.path.abspath(xml_path))
        if xml_dir not in search_dirs:
            search_dirs.insert(0, xml_dir)

        files_arg = _parse_arg("files", arg_line)

        total_write_count = 0

        if files_arg:
            # one _raw_program call per filename — mirrors CMM SplitMultipleArgs loop
            for filename in files_arg.split(","):
                filename = filename.strip()
                if not filename:
                    continue
                count = self._raw_program(3, filename, xml_path, search_dirs)
                total_write_count += count
        else:
            # write all files in XML
            total_write_count = self._raw_program(3, 0, xml_path, search_dirs)

        print(f"{total_write_count} file(s) loaded successfully")


    def _cmd_program(self, arg_line: str = ""):
        """!
        PROGRAM command — mirrors CMM PROGRAM choice handler.
        Usage:
            jtagprogrammer PROGRAM xml=<path> [searchpaths=p1,p2,...]
                → lists all files with numbers (RawProgram operation=4)
            jtagprogrammer PROGRAM xml=<path> filenumber=<n> [searchpaths=p1,p2,...]
                → programs file number N (RawProgram operation=1)

        CMM loops: list → prompt → program → list again until user enters 0.
        Python has no input() loop here — list if no filenumber, else program it.

        @param arg_line  - PROGRAM command argument string
        """
        xml_path = _parse_arg("xml", arg_line)
        if not xml_path:
            print("  Pass xml=<path>, e.g.:  jtagprogrammer PROGRAM xml=rawprogram0.xml searchpaths=//path")
            return

        search_arg  = _parse_arg("searchpaths", arg_line)
        search_dirs = [s.strip() for s in search_arg.split(",") if s.strip()] \
                      if search_arg else []
        xml_dir = os.path.dirname(os.path.abspath(xml_path))
        if xml_dir not in search_dirs:
            search_dirs.insert(0, xml_dir)

        file_number = _parse_arg("filenumber", arg_line, numeric=True)

        if file_number == -1 or file_number == 0:
            # No filenumber — list files only (RawProgram operation=4)
            # mirrors CMM: gosub RawProgram 4. 0
            self._raw_program(4, 0, xml_path, search_dirs)
            print("Enter 0 to exit this menu")
            print("NOTE: This input must be taken as an INTEGER!!!")
            print("**IMPORTANT** ----> For 12 type 12. or 0xC ::")
            print("Enter file number to program: ")
            print("  Pass filenumber=N to program a specific file, e.g.:  jtagprogrammer PROGRAM xml=rawprogram0.xml filenumber=1")
        else:
            # filenumber given — program that file (RawProgram operation=1)
            # mirrors CMM: gosub RawProgram 1. &RawProgramFile
            self._raw_program(1, file_number, xml_path, search_dirs)

    def _cmd_dump(self, arg_line: str = ""):
        """!
        DUMP command — mirrors CMM ReadSectorToFile gosub.
        Usage: jtagprogrammer DUMP startsector=<n> numsectors=<n> physicalpartition=<n> dumpfile=<path>

        @param arg_line  - DUMP command argument string
        """
        max_sectors_to_read = self.PARAM_BUFFER_SIZE // self.sector_size

        print()
        print(f"NOTE: The maximum number of {self.sector_size} byte sectors you can read is {max_sectors_to_read}")
        print(f"NOTE: The maximum number of {self.sector_size} byte sectors you can read is {max_sectors_to_read}")
        print()

        start_sector = _parse_arg("startsector", arg_line, numeric=True)
        if start_sector == -1:
            print("  Pass startsector=N, e.g.:  jtagprogrammer DUMP startsector=0 numsectors=1 physicalpartition=0 dumpfile=out.bin")
            return

        num_sectors = _parse_arg("numsectors", arg_line, numeric=True)
        if num_sectors == -1:
            print("  Pass numsectors=N")
            return

        if num_sectors > max_sectors_to_read:
            print(f"ERROR: Maximum number of sectors is {max_sectors_to_read} Exiting SD Flash Program")
            print(f"ERROR: Maximum number of sectors is {max_sectors_to_read} Exiting SD Flash Program")
            return

        physical_partition = _parse_arg("physicalpartition", arg_line, numeric=True)
        if physical_partition == -1:
            print("  Pass physicalpartition=N")
            return

        if physical_partition != self.current_hw_partition or not self.partition_info_read:
            ok = self._issue_command(None, Cmd.SET_HW_PARTITION,
                                     addr=physical_partition)
            if not ok:
                print(f"Failed to switch to partition {physical_partition}")
                return
            self.current_hw_partition = physical_partition
            self.partition_info_read  = True
            self.cmd_print_mem_info()

        file_spec = _parse_arg("dumpfile", arg_line)
        if not file_spec:
            print("  Pass dumpfile=<path>")
            return

        print("Read sector begin.")

        sectors_remaining = num_sectors
        collected = bytearray()

        while sectors_remaining > 0:
            if (sectors_remaining * self.sector_size) < self.PARAM_BUFFER_SIZE:
                sectors_next = sectors_remaining
            else:
                sectors_next = self.PARAM_BUFFER_SIZE // self.sector_size

            print(f"No of sectors to read = {sectors_next}")

            ok = self._issue_command(None, Cmd.READ_SECTOR,
                                     addr=start_sector + num_sectors - sectors_remaining,
                                     data_length=sectors_next)
            if not ok:
                return

            chunk = self._read_bytes(self.addr_param_data,
                                     sectors_next * self.sector_size)
            collected.extend(chunk)
            sectors_remaining -= sectors_next

        vram_base  = 0x00000000
        vram_end   = vram_base + (num_sectors * self.sector_size) - 1
        print(f"{file_spec}, {vram_base:#x}, {vram_end:#x}")

        try:
            with open(file_spec, "wb") as f:
                f.write(collected)
            print("Read Sector completed.")
        except OSError as e:
            print(f"Failed to save file: {e}")

    def _cmd_readbytes(self, arg_line: str = ""):
        """!
        READBYTES command — mirrors CMM ReadBytes gosub.
        Usage: jtagprogrammer READBYTES numbytes=5 sector=0 offset=0

        Always reads exactly 1 sector via READ_SECTOR then slices
        param.data[offset:offset+numbytes] — same as CMM prettyPrintOneSector.

        @param arg_line  - READBYTES command argument string
        """
        numbytes = _parse_arg("numbytes", arg_line, numeric=True)
        if numbytes == -1:
            numbytes = 1
        if numbytes == 0:
            print("numbytes was zero, Setting it to 1")
            numbytes = 1

        sector = _parse_arg("sector", arg_line, numeric=True)
        if sector == -1:
            sector = 0

        offset = _parse_arg("offset", arg_line, numeric=True)
        if offset == -1:
            offset = 0

        print(f"numbytes={numbytes}")
        print(f"sector={sector}")
        print(f"offset={offset}")

        ok = self._issue_command(None, Cmd.READ_SECTOR,
                                 addr=sector, data_length=1)
        if not ok:
            return

        # read full sector from param.data then slice — mirrors CMM prettyPrintOneSector
        full_sector = self._read_bytes(self.addr_param_data, self.sector_size)
        data = full_sector[offset:offset + numbytes]

        print(f"\nShowing {numbytes} HEX bytes from SECTOR:{sector} OFFSET:{offset}\n")
        for i in range(0, len(data), 16):
            chunk = data[i:i+16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            asc_part = "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in chunk)
            print(f"  {offset + i:04X}: {hex_part:<48}  {asc_part}")

    def cmd_print_mem_info(self) -> str:
        """! Choice 4 — CMM gosub PrintFlashInfo. Issues PRINT_MEM_INFO and refreshes sector_size."""
        ok     = self._issue_command(None, Cmd.PRINT_MEM_INFO)
        output = self._read_output_buffer(None)
        if ok:
            self.sector_size = self._read_u32(self.addr_block_size)
        return output

    def _cmd_read_sector(self, arg_line: str = ""):
        """!
        Choice 1 — CMM gosub ReadSector.
        Usage: jtagprogrammer 1 sector=<n> [numsectors=<n>] [file=<path>]
        Issues READ_SECTOR (0x09), reads param.data, prints hex dump and optionally saves.

        @param arg_line  - read-sector command argument string
        """
        val = _parse_arg("sector", arg_line, numeric=True)
        if val == -1:
            print("  Pass sector=N, e.g.:  jtagprogrammer 1 sector=0")
            return
        start_sector = val

        num_sectors = _parse_arg("numsectors", arg_line, numeric=True)
        if num_sectors == -1:
            num_sectors = 1
        if num_sectors == 0:
            print("numSectors was zero, Setting it to 1")
            num_sectors = 1

        max_sectors = self.PARAM_BUFFER_SIZE // self.sector_size
        if num_sectors > max_sectors:
            print(f"ERROR: Maximum number of sectors is {max_sectors}")
            return

        ok = self._issue_command(None, Cmd.READ_SECTOR,
                                 addr=start_sector, data_length=num_sectors)
        if not ok:
            return

        data = self._read_bytes(self.addr_param_data,
                                num_sectors * self.sector_size)

        # Always print hex dump — visible inline in LLDB output panel
        print(f"\nSector {start_sector}  ({len(data)} bytes):")
        for i in range(0, len(data), 16):
            chunk = data[i:i+16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            asc_part = "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in chunk)
            print(f"  {start_sector * self.sector_size + i:08X}: {hex_part:<48}  {asc_part}")

        filespec = _parse_arg("file", arg_line)
        if filespec:
            with open(filespec, "wb") as f:
                f.write(data)
            print(f"{len(data)} bytes saved to {filespec}")

        print("Read Sector completed.")

    def _cmd_write_sector(self, arg_line: str = ""):
        """!
        Choice 2 — CMM gosub WriteSector.
        Usage: jtagprogrammer 2 sector=<n> [numsectors=<n>] file=<path>
        Loads file bytes into param.data BEFORE issuing WRITE_SECTOR (0x0A) —
        the firmware reads the buffer on resume, so it must be filled first.

        @param arg_line  - write-sector command argument string
        """
        print("Write sector begin.")

        val = _parse_arg("sector", arg_line, numeric=True)
        if val == -1:
            print("  Pass sector=N file=<path>, e.g.:  jtagprogrammer 2 sector=0 numsectors=1 file=data.bin")
            return
        start_sector = val

        num_sectors = _parse_arg("numsectors", arg_line, numeric=True)
        if num_sectors == -1:
            num_sectors = 1
        if num_sectors == 0:
            return

        filename = _parse_arg("file", arg_line)
        if not filename:
            print("  Pass file=<path>, e.g.:  jtagprogrammer 2 sector=0 numsectors=1 file=data.bin")
            return

        try:
            with open(filename, "rb") as f:
                file_bytes = f.read()
        except OSError as e:
            print(f"Failed to read file: {e}")
            return

        # Zero-pad to sector boundary — mirrors CMM data.set ParmData 0x0 then load
        target_size = num_sectors * self.sector_size
        if len(file_bytes) < target_size:
            file_bytes = file_bytes + b"\x00" * (target_size - len(file_bytes))
        else:
            file_bytes = file_bytes[:target_size]

        # Write data into param.data BEFORE issuing command — firmware reads on resume
        error = lldb.SBError()
        self._get_process().WriteMemory(self.addr_param_data, file_bytes, error)
        if error.Fail():
            print(f"Failed to write data buffer: {error.GetCString()}")
            return

        ok = self._issue_command(None, Cmd.WRITE_SECTOR,
                                 addr=start_sector, data_length=num_sectors)
        if not ok:
            print("Write Sector FAILED")
            return
        print("Write Sector completed.")

    def _cmd_load_program(self, arg_line: str = ""):
        """!
        Choice 3 — CMM gosub LoadProgram. Delegates to _cmd_load — same
        flow as the LOAD command.
        Usage: jtagprogrammer 3 xml=<path> [files=f1,f2,...] [searchpaths=p1,p2,...]

        @param arg_line  - load-program command argument string
        """
        self._cmd_load(arg_line)

    def _cmd_quick_debug(self):
        """! Choice 5 — CMM gosub QuickDebugScript. Not yet implemented."""
        self._cmd_not_implemented("5 / QuickDebugScript")

    def _cmd_load_ffu(self):
        """! FFU — CMM gosub LoadFFU. Not yet implemented."""
        self._cmd_not_implemented("FFU / LoadFFU")

    def _cmd_erase_chip(self) -> bool:
        """! Choice 6/ERASE — CMM gosub EraseChip. Issues CHIP_ERASE (0x05)."""
        ok = self._issue_command(None, Cmd.CHIP_ERASE, addr=0, data_length=1,
                                 timeout_s=300.0)
        if not ok:
            print("Chip Erase FAILED")
        return ok

    def _erase_sector(self, start_sector: int, num_partition_sectors: int) -> bool:
        """!
        Erase a specific sector range. Mirrors CMM EraseSector gosub.
        Issues ERASE_GENERIC (0x40) with addr=start_sector, data_length=num_sectors.

        @param start_sector             - first sector to erase
        @param num_partition_sectors    - number of sectors to erase
        @return True on success
        """
        ok = self._issue_command(None, Cmd.ERASE_GENERIC,
                                 addr=start_sector,
                                 data_length=num_partition_sectors)
        if ok:
            print("Erase Sector completed.")
        return ok

    def _compute_crc(self, crc_start: int, crc_len: int):
        """!
        Mirrors CMM ComputeCRC gosub. Issues COMPUTE_CRC (0x41); the result
        comes back in jtag_prog_param.data_length, not cmd_status.

        @param crc_start  - start sector for the CRC computation
        @param crc_len    - number of sectors to include
        @return computed CRC value, or None if the command failed on hardware
        """
        ok = self._issue_command(None, Cmd.COMPUTE_CRC,
                                 addr=crc_start, data_length=crc_len)
        if not ok:
            return None
        # result is in data_length field after command completes
        return self._read_u64(self.addr_data_length)

    def _patch_sector(self, start_sector: int, byte_offset: int,
                      size_in_bytes: int, value: int) -> bool:
        """!
        Mirrors CMM PatchSector gosub: READ_SECTOR → modify
        param.data[byte_offset] → WRITE_SECTOR. Retries READ_SECTOR up to
        3 times — firmware occasionally returns 0x100 transiently after a
        partition switch.

        @param start_sector    - sector to patch
        @param byte_offset     - byte offset within the sector to modify
        @param size_in_bytes   - value width, must be 4 or 8
        @param value           - value to write at byte_offset
        @return True on success
        """
        # READ_SECTOR with retry — firmware can return 0x100 transiently
        ok = False
        for attempt in range(3):
            ok = self._issue_command(None, Cmd.READ_SECTOR,
                                     addr=start_sector, data_length=1)
            if ok:
                break
            logger.warning(f"READ_SECTOR @ sector {start_sector} attempt {attempt+1} failed — retrying")
            time.sleep(0.2)
        if not ok:
            return False
        print("Read Sector completed.")

        # read full sector from param.data, modify bytes at byte_offset
        sector_data = bytearray(self._read_bytes(self.addr_param_data,
                                                  self.sector_size))

        if size_in_bytes == 4:
            struct.pack_into("<I", sector_data, byte_offset,
                             value & 0xFFFFFFFF)
        elif size_in_bytes == 8:
            struct.pack_into("<Q", sector_data, byte_offset,
                             value & 0xFFFFFFFFFFFFFFFF)
        else:
            print("Unsupported value for size_in_bytes.")
            return False

        # write modified sector back to param.data before WRITE_SECTOR
        error = lldb.SBError()
        self._get_process().WriteMemory(self.addr_param_data,
                                         bytes(sector_data), error)
        if error.Fail():
            print(f"WriteMemory failed: {error.GetCString()}")
            return False

        # WRITE_SECTOR — write modified data back to UFS
        ok = self._issue_command(None, Cmd.WRITE_SECTOR,
                                 addr=start_sector, data_length=1)
        if not ok:
            return False
        print("Write Sector completed.")
        return True

    def _cmd_patch(self, arg_line: str = ""):
        """!
        PATCH command — accepts comma-separated xml= paths.
        Usage: jtagprogrammer PATCH xml=patch0.xml[,patch1.xml,...]
        Each XML is applied in order via _apply_patch_xml.

        @param arg_line  - PATCH command argument string
        """
        xml_arg = _parse_arg("xml", arg_line)
        if not xml_arg:
            print("  Pass xml=<path>, e.g.:  jtagprogrammer PATCH xml=patch0.xml")
            return

        xml_paths = [p.strip() for p in xml_arg.split(",") if p.strip()]
        total = 0
        for xml_path in xml_paths:
            count = self._apply_patch_xml(xml_path)
            total += count
        print(f"{total} patch(es) applied successfully across {len(xml_paths)} file(s)")

    def _apply_patch_xml(self, xml_path: str) -> int:
        """!
        Apply all patches from a single patch XML file. Extracted from
        _cmd_patch so it can loop over multiple XMLs.

        @param xml_path  - patch XML file to apply
        @return number of patches applied
        """
        import xml.etree.ElementTree as ET

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except Exception as e:
            print(f"Failed to parse XML {xml_path}: {e}")
            return 0

        print()
        print()

        write_count  = 0
        total_blocks = 0
        total_blocks_for_partition = None  # track which partition total_blocks was resolved for

        # ensure current_mem_info_ufs is populated before resolving NUM_DISK_SECTORS
        # if partition_info_read is False we haven't called PRINT_MEM_INFO yet
        if not self.partition_info_read:
            self.cmd_print_mem_info()
            self.partition_info_read = True

        for entry in root.iter("patch"):
            byte_offset        = int(entry.get("byte_offset", "0"), 0)
            filename           = entry.get("filename", "")
            physical_partition = entry.get("physical_partition_number", "0")
            size_in_bytes      = int(entry.get("size_in_bytes", "0"), 0)
            start_str          = entry.get("start_sector", "0")
            value_str          = entry.get("value", "0")

            # only DISK patches — mirrors CMM: if "&filename"=="DISK"
            if filename != "DISK":
                continue

            try:
                physical_partition = int(physical_partition, 0)
            except ValueError:
                print(f"  Invalid physical_partition_number: {physical_partition!r}")
                continue

            # SwitchPartition if needed
            if physical_partition != self.current_hw_partition or not self.partition_info_read:
                ok = self._issue_command(None, Cmd.SET_HW_PARTITION,
                                         addr=physical_partition)
                if not ok:
                    print(f"  Failed to switch to partition {physical_partition}")
                    return write_count
                self.current_hw_partition = physical_partition
                self.partition_info_read  = True
                self.cmd_print_mem_info()
                total_blocks_for_partition = None  # force re-resolve after switch

            # Resolve NUM_DISK_SECTORS for this partition if not yet done.
            # CMM EvalCardSectorsExpr reads current_mem_info_ufs lazily, after
            # the partition switch — so total_blocks must reflect the active LUN.
            if total_blocks_for_partition != self.current_hw_partition:
                target   = self.debugger.GetSelectedTarget()
                mem_info = target.FindFirstGlobalVariable("current_mem_info_ufs")
                if mem_info.IsValid():
                    total_blocks = mem_info.GetChildMemberWithName(
                        "dLuTotalBlocks").GetValueAsUnsigned()
                    logger.debug(f"NUM_DISK_SECTORS for partition {self.current_hw_partition} = {total_blocks}")
                else:
                    logger.warning("current_mem_info_ufs not found — NUM_DISK_SECTORS will be 0")
                    total_blocks = 0
                total_blocks_for_partition = self.current_hw_partition

            # EvalCardSectorsExpr on start_sector
            if "NUM_DISK_SECTORS" in start_str:
                start_str = start_str.replace("NUM_DISK_SECTORS", str(total_blocks))
            try:
                start_sector = int(eval(start_str.rstrip(".")))
            except Exception:
                print(f"  Could not evaluate start_sector: {start_str!r}")
                continue

            # CRC32(...) or plain value — mirrors CMM crc_flag check
            if "CRC32(" in value_str:
                # extract CRC32(crc_start, crc_len)
                try:
                    inner    = value_str[value_str.index("CRC32(") + 6 : value_str.index(")")]
                    parts    = inner.split(",")
                    crc_start_str = parts[0].strip()
                    crc_len_str   = parts[1].strip()
                except (ValueError, IndexError):
                    print(f"  Malformed CRC32(...) value: {value_str!r}")
                    continue

                if "NUM_DISK_SECTORS" in crc_start_str:
                    crc_start_str = crc_start_str.replace("NUM_DISK_SECTORS", str(total_blocks))
                try:
                    crc_start = int(eval(crc_start_str.rstrip(".")))
                    crc_len   = int(eval(crc_len_str.rstrip(".")))
                except Exception:
                    print(f"  Could not evaluate CRC args: {inner!r}")
                    continue

                value = self._compute_crc(crc_start, crc_len)
                if value is None:
                    print(f"  COMPUTE_CRC failed for start={crc_start} len={crc_len} — skipping patch")
                    continue
                print(f"CRC value: {value}")
            else:
                # EvalCardSectorsExpr on value
                if "NUM_DISK_SECTORS" in value_str:
                    value_str = value_str.replace("NUM_DISK_SECTORS", str(total_blocks))
                try:
                    value = int(eval(value_str.rstrip(".")))
                except Exception:
                    print(f"  Could not evaluate value: {value_str!r}")
                    continue

            ok = self._patch_sector(start_sector, byte_offset, size_in_bytes, value)
            print(f"Sector: {start_sector}, offset {byte_offset},"
                  f" partition {physical_partition}, {value}, {size_in_bytes}")
            if ok:
                write_count += 1

        print(f"{write_count} patch(es) applied from {os.path.basename(xml_path)}")
        return write_count

    def _cmd_zeroout(self, arg_line: str = ""):
        """!
        ZEROOUT command — automation path only. Mirrors CMM ZeroOut gosub.
        Usage: jtagprogrammer ZEROOUT xml=<path>

        Parses <zeroout> tags from XML. For each entry: switches partition
        if needed, evaluates NUM_DISK_SECTORS in start_sector/num_partition_sectors,
        then calls _erase_sector. Interactive path not implemented — in CMM
        it only prints an XML hint and does not erase anything.

        @param arg_line  - ZEROOUT command argument string
        """
        import xml.etree.ElementTree as ET

        xml_path = _parse_arg("xml", arg_line)
        if not xml_path:
            print("  Pass xml=<path>, e.g.:  jtagprogrammer ZEROOUT xml=rawprogram0.xml")
            return

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except Exception as e:
            print(f"Failed to parse XML {xml_path}: {e}")
            return

        print()
        print()

        operation_count = 0

        for entry in root.iter("zeroout"):
            label              = entry.get("label", "")
            physical_partition = int(entry.get("physical_partition_number", "0"), 0)
            num_str            = entry.get("num_partition_sectors",
                                           entry.get("num_sectors", "0"))
            start_str          = entry.get("start_sector", "0")

            # EvalCardSectorsExpr — substitute NUM_DISK_SECTORS in both fields
            if "NUM_DISK_SECTORS" in num_str or "NUM_DISK_SECTORS" in start_str:
                target   = self.debugger.GetSelectedTarget()
                mem_info = target.FindFirstGlobalVariable("current_mem_info_ufs")
                if mem_info.IsValid():
                    total_blocks = mem_info.GetChildMemberWithName(
                        "dLuTotalBlocks").GetValueAsUnsigned()
                else:
                    print("  current_mem_info_ufs not found — cannot resolve NUM_DISK_SECTORS")
                    return
                num_str   = num_str.replace("NUM_DISK_SECTORS", str(total_blocks))
                start_str = start_str.replace("NUM_DISK_SECTORS", str(total_blocks))

            try:
                num_partition_sectors = int(eval(num_str.rstrip(".")))
                start_sector          = int(eval(start_str.rstrip(".")))
            except Exception:
                print(f"  Could not evaluate sector expressions: num={num_str!r} start={start_str!r}")
                continue

            # SwitchPartition if needed — mirrors CMM guard
            if physical_partition != self.current_hw_partition or not self.partition_info_read:
                ok = self._issue_command(None, Cmd.SET_HW_PARTITION,
                                         addr=physical_partition)
                if not ok:
                    print(f"  Failed to switch to partition {physical_partition}")
                    return
                self.current_hw_partition = physical_partition
                self.partition_info_read  = True
                self.cmd_print_mem_info()

            ok = self._erase_sector(start_sector, num_partition_sectors)
            print(f"Sector: {start_sector}, num sectors {num_partition_sectors}, partition {physical_partition}")
            if ok:
                operation_count += 1

        print(f"{operation_count} area(s) zeroed out successfully")
        if operation_count == 0:
            print()
            print("NOTE: No <zeroout> tags found in the XML.")
            print("To zero out a region, add a line like this to your rawprogram XML:")
            print('  <zeroout label="temp" num_partition_sectors="<num_sectors>"'
                  ' physical_partition_number="<partition>" start_sector="<start_sector>"/>')

    def _read_partition_names(self, tc=None) -> list:
        """!
        Read partition_names[] from device RAM — populated by do_probe during
        setup. partition_names is an array of 9 64-bit pointers to
        null-terminated strings; each pointer is dereferenced to get the name.

        @return list of partition name strings
        """
        self._write_u32(self.addr_cmd_status, 0x0)
        num = self._read_u32(self.addr_max_partitions)
        num = min(num, 9)
        names = []
        for i in range(num):
            ptr = self._read_u64(self.addr_partition_names + i * 8)
            if ptr == 0 or ptr > 0xFFFFFFFF:
                names.append("")
                continue
            raw  = self._read_bytes(ptr, 16)
            name = raw.split(b"\x00")[0].decode("ascii", errors="replace")
            names.append(name)
        return names

    def _cmd_switch_partition(self, arg_line: str = ""):
        """!
        Choice 7 — CMM gosub Switch_Partition.
        With no partition= arg: lists available partitions.
        With partition=N arg: switches to that partition and refreshes info.

        @param arg_line  - switch-partition command argument string
        """
        names = self._read_partition_names()
        for i, name in enumerate(names):
            print(f"{i} {name}")

        val = _parse_arg("partition", arg_line)
        if not val:
            print("\n  Pass partition=N to switch, e.g.:  jtagprogrammer 7 partition=2")
            return
        print()

        try:
            partition_num = int(val, 0)
        except ValueError:
            print(f"Invalid partition number: {val!r}")
            return

        ok = self._issue_command(None, Cmd.SET_HW_PARTITION, addr=partition_num)
        if not ok:
            print(f"Switch partition to {partition_num} FAILED")
            return

        self.current_hw_partition = partition_num
        self.current_hw_part_name = names[partition_num] if partition_num < len(names) else str(partition_num)
        self.partition_info_read  = True
        output = self.cmd_print_mem_info()
        print(output)

    def _cmd_select_card_slot(self, arg_line: str = ""):
        """!
        Choice 8 — CMM gosub RunSelectCardSlot. Writes card_slot_in_use and
        calls PrintFlashInfo to confirm the new slot responds.
        Without slot= arg: prints hint so you know what to pass.

        @param arg_line  - select-card-slot command argument string
        """
        val = _parse_arg("slot", arg_line)
        if val == "0" and "slot=" not in arg_line:
            print("Which card slot to use (0-3) >")
            print("\n  Pass slot=N to switch, e.g.:  jtagprogrammer 8 slot=1")
            return

        try:
            slot_num = int(val, 0)
        except ValueError:
            print(f"Invalid slot: {val!r}")
            return

        if slot_num not in (0, 1, 2, 3):
            print(f"Invalid slot {slot_num} — must be 0, 1, 2, or 3")
            return

        self._write_u32(self.addr_card_slot, slot_num)
        output = self.cmd_print_mem_info()
        if not output:
            print(f"WARNING: slot {slot_num} not responding — reverting to slot {self.current_slot}")
            self._write_u32(self.addr_card_slot, self.current_slot)
        else:
            print("Updated current card slot used.")
            self.current_slot = slot_num
            print(output)

    def _cmd_select_active_partition(self, arg_line: str = ""):
        """!
        Choice 9 — CMM gosub SelectActivePartition.
        With no activeboot= arg: lists partitions.
        With activeboot=N arg: sets that partition as active boot.

        @param arg_line  - select-active-partition command argument string
        """
        val = _parse_arg("activeboot", arg_line)
        if not val:
            names = self._read_partition_names()
            for i, name in enumerate(names):
                print(f"{i} {name}")
            print("\n  Pass activeboot=N to set, e.g.:  jtagprogrammer 9 activeboot=1")
            return
        print()

        try:
            partition_num = int(val, 0)
        except ValueError:
            print(f"Invalid partition number: {val!r}")
            return

        print(f"Setting Active Boot Partition to {partition_num}")
        ok = self._issue_command(None, Cmd.SET_ACT_BOOT_PART, addr=partition_num)
        if not ok:
            print("Set Active Boot Partition FAILED")

    def _write_storage_extras_field(self, field_name: str, value: int):
        """!
        Write a scalar field of the storage_extras struct on the target.
        Resolves the field's byte offset and width via LLDB DWARF.
        Mirrors CMM: v.set storage_extras.<field> = <value>

        @param field_name  - storage_extras field name
        @param value       - value to write
        """
        target = self.debugger.GetSelectedTarget()
        se_var = target.FindFirstGlobalVariable("storage_extras")
        if not se_var.IsValid():
            raise RuntimeError("storage_extras symbol not found in LLDB — cannot write field")
        child = se_var.GetChildMemberWithName(field_name)
        if not child.IsValid():
            raise RuntimeError(f"storage_extras.{field_name} not found via LLDB DWARF")
        base_fa  = se_var.GetAddress().GetFileAddress()
        child_fa = child.GetAddress().GetFileAddress()
        offset   = child_fa - base_fa
        size     = child.GetByteSize()
        addr     = self.addr_storage_extras + offset
        self._write_sized_int(addr, value, size)
        logger.debug(f"  storage_extras.{field_name} @ +0x{offset:X} (size={size}) = 0x{value:X}")

    def _write_storage_extras_unit_field(self, lun: int, field_name: str, value: int):
        """!
        Write a field of storage_extras.unit[lun].<field_name> on the target.
        Resolves offsets fully via LLDB DWARF — unit[0] base + stride * lun + field offset.
        Mirrors CMM: v.set storage_extras.unit[LUNum].<field> = <value>

        @param lun         - logical unit index
        @param field_name  - storage_extras.unit[] field name
        @param value       - value to write
        """
        target = self.debugger.GetSelectedTarget()
        se_var = target.FindFirstGlobalVariable("storage_extras")
        if not se_var.IsValid():
            raise RuntimeError("storage_extras symbol not found")
        unit_arr = se_var.GetChildMemberWithName("unit")
        if not unit_arr.IsValid():
            raise RuntimeError("storage_extras.unit not found via LLDB DWARF")
        unit_0   = unit_arr.GetChildAtIndex(0)
        unit_n   = unit_arr.GetChildAtIndex(lun)
        child    = unit_n.GetChildMemberWithName(field_name)
        if not child.IsValid():
            raise RuntimeError(f"storage_extras.unit[{lun}].{field_name} not found")
        base_fa  = se_var.GetAddress().GetFileAddress()
        child_fa = child.GetAddress().GetFileAddress()
        offset   = child_fa - base_fa
        size     = child.GetByteSize()
        addr     = self.addr_storage_extras + offset
        self._write_sized_int(addr, value, size)
        logger.debug(f"  storage_extras.unit[{lun}].{field_name} @ +0x{offset:X} (size={size}) = 0x{value:X}")

    def _write_storage_extras_gpp(self, index: int, value: int):
        """!
        Write storage_extras.GPP_size[index] on the target via LLDB DWARF.
        Mirrors CMM: v.set storage_extras.GPP_size[N] = KB * 2

        @param index  - GPP_size array index
        @param value  - value to write (sectors, already scaled by caller)
        """
        target = self.debugger.GetSelectedTarget()
        se_var = target.FindFirstGlobalVariable("storage_extras")
        if not se_var.IsValid():
            raise RuntimeError("storage_extras symbol not found")
        gpp_arr = se_var.GetChildMemberWithName("GPP_size")
        if not gpp_arr.IsValid():
            raise RuntimeError("storage_extras.GPP_size not found via LLDB DWARF")
        elem     = gpp_arr.GetChildAtIndex(index)
        base_fa  = se_var.GetAddress().GetFileAddress()
        elem_fa  = elem.GetAddress().GetFileAddress()
        offset   = elem_fa - base_fa
        size     = elem.GetByteSize()
        addr     = self.addr_storage_extras + offset
        self._write_sized_int(addr, value, size)
        logger.debug(f"  storage_extras.GPP_size[{index}] @ +0x{offset:X} (size={size}) = 0x{value:X}")

    def _cmd_provision(self, arg_line: str = "", dry_run: bool = False):
        """!
        PROVISION command — mirrors CMM ProcessXMLFile gosub.
        Usage: jtagprogrammer PROVISION xml=<path> [searchpaths=p1,p2,...]

        Parses UFS provisioning XML. For each <ufs> tag dispatches to one of:
          - GPP sizes (DRIVE4..7_SIZE_IN_KB present)
          - LUNtoGrow
          - Per-LUN descriptor (LUNum present)
          - Device descriptor (bNumberLU present)
        When commit="1" is present on a <ufs> tag, calls _storage_extras().

        @param arg_line  - PROVISION command argument string
        @param dry_run   - when True (VERIFY), only print the values that would
                           be written/committed — no writes to the target at all
        """
        xml_path = _parse_arg("xml", arg_line)
        if not xml_path:
            print("  Pass xml=<path>, e.g.:  jtagprogrammer PROVISION xml=provision.xml")
            return

        # xml= may be a bare filename (e.g. provision_ufs40.xml) meant to be
        # resolved against searchpaths= — mirrors how _cmd_load/_cmd_program
        # resolve file names via _locate_file, since xml_path here IS the
        # file to find, not just a directory hint.
        if not os.path.isfile(xml_path):
            search_arg  = _parse_arg("searchpaths", arg_line)
            search_dirs = [s.strip() for s in search_arg.split(",") if s.strip()] \
                          if search_arg else []
            located = self._locate_file(xml_path, search_dirs)
            if located:
                xml_path = located

        if not os.path.isfile(xml_path):
            print(f"  XML file not found: {xml_path}")
            return

        def _to_int_if_numeric(val: str):
            try:
                return int(val, 0)   # handles decimal, 0x hex, 0b binary
            except (ValueError, TypeError):
                return val

        def _scan_ufs_entries(path):
            # Mirrors CMM ProcessXMLFile: scans each line for a bare
            # '<tag attr="val" ...>' pattern — no real XML parsing, so
            # comments, XML declarations, etc. are just lines whose tag
            # name never matches "ufs" and are silently ignored.
            entries = []
            with open(path, "r", errors="replace") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    lt = line.find("<")
                    if lt == -1:
                        continue
                    rest = line[lt + 1:]
                    sp = rest.find(" ")
                    if sp == -1:
                        continue
                    tag_name = rest[:sp]
                    if tag_name != "ufs":
                        continue
                    rest = rest[sp + 1:].strip()
                    attribs = {}
                    while True:
                        eq = rest.find('="')
                        if eq == -1:
                            break
                        attr_name = rest[:eq]
                        rest = rest[eq + 2:]
                        q = rest.find('"')
                        if q == -1:
                            break
                        attribs[attr_name] = _to_int_if_numeric(rest[:q])
                        rest = rest[q + 1:].strip()
                    entries.append(attribs)
            return entries

        try:
            ufs_entries = _scan_ufs_entries(xml_path)
        except Exception as e:
            print(f"Failed to parse XML {xml_path}: {e}")
            return

        for attribs in ufs_entries:

            drive4              = attribs.get("DRIVE4_SIZE_IN_KB")
            lun_to_grow         = attribs.get("LUNtoGrow")
            lun_num             = attribs.get("LUNum")
            b_number_lu         = attribs.get("bNumberLU")
            b_rpmb_region_enable = attribs.get("bRPMBRegionEnable")

            if drive4 is not None:
                # GPP sizes — mirrors CMM v.set storage_extras.GPP_size[0..3]
                # GPP_size is in 512-byte sectors: KB * 2
                gpp_keys = ["DRIVE4_SIZE_IN_KB", "DRIVE5_SIZE_IN_KB",
                            "DRIVE6_SIZE_IN_KB", "DRIVE7_SIZE_IN_KB"]
                for i, key in enumerate(gpp_keys):
                    kb = attribs.get(key, 0)
                    val = int(kb) * 2 if isinstance(kb, int) else 0
                    if not dry_run:
                        self._write_storage_extras_gpp(i, val)
                    print(f"  GPP_size[{i}] = {val}  ({kb} KB)")

            elif lun_to_grow is not None:
                # LUNtoGrow — mirrors CMM v.set storage_extras.lun_to_grow
                if not dry_run:
                    self._write_storage_extras_field("lun_to_grow", int(lun_to_grow))
                print(f"  LUNtoGrow: {lun_to_grow}")

            elif lun_num is not None:
                # Per-LUN descriptor — mirrors CMM v.set storage_extras.unit[LUNum].*
                # CMM bails if BLOCK_SIZE is not a multiple of 1 KB
                block_size = self._read_u32(self.addr_block_size)
                if block_size % 1024 != 0:
                    print("Error: Block size is not a multiple of 1KB")
                    return

                lun = int(lun_num)
                wb_buf_kb = attribs.get("wb_buffer_size_in_kb", 0)
                if wb_buf_kb == "" or wb_buf_kb is None:
                    wb_buf_kb = 0

                # mirrors CMM v.set storage_extras.unit[LUNum].* (13 fields)
                unit_fields = [
                    ("bLUEnable",             attribs.get("bLUEnable",             0)),
                    ("bBootLunID",            attribs.get("bBootLunID",            0)),
                    ("bLUWriteProtect",       attribs.get("bLUWriteProtect",       0)),
                    ("bMemoryType",           attribs.get("bMemoryType",           0)),
                    ("bDataReliability",      attribs.get("bDataReliability",      0)),
                    ("bLogicalBlockSize",     attribs.get("bLogicalBlockSize",     0)),
                    ("bProvisioningType",     attribs.get("bProvisioningType",     0)),
                    ("wContextCapabilities",  attribs.get("wContextCapabilities",  0)),
                    ("qSizeInKb",             attribs.get("size_in_kb",            0)),
                    ("qWriteBoosterBufferSizeInKb", int(wb_buf_kb)),
                    ("wLUMaxActiveHPBRegions",     attribs.get("wLUMaxActiveHPBRegions",     0)),
                    ("wHPBPinnedRegionStartIdx",   attribs.get("wHPBPinnedRegionStartIdx",   0)),
                    ("wNumHPBPinnedRegions",       attribs.get("wNumHPBPinnedRegions",       0)),
                ]
                for field, val in unit_fields:
                    v = int(val) if val is not None else 0
                    if not dry_run:
                        self._write_storage_extras_unit_field(lun, field, v)
                print(f"  LUN {lun_num}:")
                for field, val in unit_fields:
                    print(f"    {field} = {val}")

            elif b_number_lu is not None:
                # Device descriptor — mirrors CMM v.set storage_extras.bNumberLU etc. (14 fields)
                # If Write Booster fields absent, default to 0xFF — matches CMM sentinel
                wb_preserve = attribs.get("bWriteBoosterBufferPreserveUserSpaceEn")
                if wb_preserve is None or wb_preserve == "":
                    wb_preserve = 0xFF
                wb_type = attribs.get("bWriteBoosterBufferType")
                if wb_type is None or wb_type == "":
                    wb_type = 0xFF

                dev_fields = [
                    ("bNumberLU",           int(b_number_lu)),
                    ("bBootEnable",         attribs.get("bBootEnable",         0)),
                    ("bDescrAccessEn",      attribs.get("bDescrAccessEn",      0)),
                    ("bInitPowerMode",      attribs.get("bInitPowerMode",      0)),
                    ("bHighPriorityLUN",    attribs.get("bHighPriorityLUN",    0)),
                    ("bSecureRemovalType",  attribs.get("bSecureRemovalType",  0)),
                    ("bInitActiveICCLevel", attribs.get("bInitActiveICCLevel", 0)),
                    ("wPeriodicRTCUpdate",  attribs.get("wPeriodicRTCUpdate",  0)),
                    ("bConfigDescrLock",    attribs.get("bConfigDescrLock",    0)),
                    ("qVendorConfigCode",   attribs.get("qVendorConfigCode",   0)),
                    ("bHPBControl",         attribs.get("bHPBControl",         0)),
                    ("bWriteBoosterBufferPreserveUserSpaceEn", int(wb_preserve)),
                    ("bWriteBoosterBufferType",                int(wb_type)),
                    ("qSharedWriteBoosterBufferSizeInKb",
                     attribs.get("shared_wb_buffer_size_in_kb", 0)),
                ]
                for field, val in dev_fields:
                    v = int(val) if val is not None else 0
                    if not dry_run:
                        self._write_storage_extras_field(field, v)
                print(f"  Device descriptor:")
                for field, val in dev_fields:
                    print(f"    {field} = {val}")

            elif b_rpmb_region_enable is not None:
                # RPMB region sizing — mirrors CMM v.set storage_extras.bRPMBRegion* (4 fields)
                rpmb_fields = [
                    ("bRPMBRegionEnable", int(b_rpmb_region_enable)),
                    ("bRPMBRegion1Size",  attribs.get("bRPMBRegion1Size", 0)),
                    ("bRPMBRegion2Size",  attribs.get("bRPMBRegion2Size", 0)),
                    ("bRPMBRegion3Size",  attribs.get("bRPMBRegion3Size", 0)),
                ]
                for field, val in rpmb_fields:
                    v = int(val) if val is not None else 0
                    if not dry_run:
                        self._write_storage_extras_field(field, v)
                print(f"  RPMB regions:")
                for field, val in rpmb_fields:
                    print(f"    {field} = {val}")

            # commit="1" on any tag fires the provisioning command
            commit = attribs.get("commit")
            if commit == 1:
                if dry_run:
                    print("  [VERIFY] commit=1 — PROGRAM_EXTRAS would be issued here")
                else:
                    self._storage_extras()

    def _storage_extras(self):
        """! Mirrors CMM StorageExtras gosub — issues PROGRAM_EXTRAS (0x42) to commit provisioning."""
        print("Issuing the Provisioning command...")
        ok = self._issue_command(None, Cmd.PROGRAM_EXTRAS, timeout_s=60.0)
        if not ok:
            print("StorageExtras FAILED")
        else:
            print("+++++ Provisioning Succeeded")


# ---------------------------------------------------------------------------
# LLDB command entry point — registered by command script import
# ---------------------------------------------------------------------------

# Persists the JtagProgrammer instance across LLDB command calls so setup
# only runs once. Subsequent calls reuse the existing instance and jump
# straight to dispatch.
_jtp_instance: "JtagProgrammer | None" = None


def jtagprogrammer(debugger, command, exe_ctx, result):
    """!
    LLDB command:  jtagprogrammer elfpath=<path> [dtbpath=<path>] [slot=<n>] [<COMMAND>]

    First call (no COMMAND or with elfpath):
        Runs full setup — loads ELF, runs to main_c.
        Stores instance globally so subsequent calls skip setup.

    Subsequent calls:
        jtagprogrammer 4     → PRINT_MEM_INFO
        jtagprogrammer 0     → exit / cleanup
        jtagprogrammer ERASE YES → erase entire User partition

    @param debugger  - SBDebugger instance provided by LLDB
    @param command   - full command-line string typed by the user
    @param exe_ctx   - SBExecutionContext provided by LLDB (unused)
    @param result    - SBCommandReturnObject to report success/failure to LLDB
    """
    global _jtp_instance
    try:
        auto_command, arg_line = _parse_command_line(command)
        elf_path = _parse_arg("elfpath", arg_line) or None
        dtb_path = _parse_arg("dtbpath", arg_line) or None
        slot     = _parse_arg("slot",    arg_line, numeric=True)
        if not isinstance(slot, int):
            slot = 0

        # If elfpath supplied or no instance yet — run setup
        if elf_path or _jtp_instance is None:
            logger.info(f"Running setup  elf={elf_path!r}  dtb={dtb_path!r}  slot={slot}")
            jtp = JtagProgrammer(debugger, elf_path=elf_path, dtb_path=dtb_path, card_slot=slot)
            jtp.setup()
            _jtp_instance = jtp   # publish only after setup succeeds
            logger.info("Setup complete. Call 'jtagprogrammer <choice>' to run a command.")
            if not auto_command:
                _jtp_instance._print_menu()
                return

        # Extract choice: use auto_command (first token, uppercased) but handle
        # "0" specially — _parse_command_line maps "0" to "" per CMM convention,
        # but in dispatch context "0" means Exit, so restore it.
        raw_first = command.strip().split()[0].upper() if command.strip() else ""
        choice = auto_command if auto_command else raw_first
        logger.info(f"Dispatching choice={choice!r}")
        _jtp_instance._dispatch(choice, arg_line=arg_line)

    except Exception as e:
        import traceback
        msg = traceback.format_exc()
        logger.error(f"jtagprogrammer failed:\n{msg}")
        result.SetError(str(e))
