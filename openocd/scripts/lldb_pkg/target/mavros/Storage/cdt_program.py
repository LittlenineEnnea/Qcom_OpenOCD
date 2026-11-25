#
# Copyright (c) 2026 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file cdt_program.py
#
# @section Description
# Python port of cdt_program.cmm — CDT (Chip Data Table) partition
# read / write / erase via JtagProgrammer, driven from a self-contained
# CDT fixture kit rather than a live metabuild (see _CDT_FIXTURE_ROOT).
#
# Mavros-specific implementation using the same workflow as Clarence,
# with platform-specific paths and storage parameters.
#
# Usage (standalone, no openocd_lldb.py needed):
#   command script import <path>/lldb_pkg/target/mavros/Storage/cdt_program.py
#   cdt_program VERIFY|WRITE|READ|ERASE [cdtpath=] [xmlpath=] [elfpath=] [zerospath=] [fixturepath=]

import os
import re
import glob
import importlib
import shutil
import sys
from dataclasses import dataclass, field
from typing import Optional

# command script import does not add scripts/ to sys.path — insert it so
# utils.* imports resolve. This file lives 4 levels under scripts/.
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from utils.debug_context import g_dbgContext, logger
from utils.meta_info import metaInfo


def _std_loadbuild_module():
    """!
    Import the current chipset's Storage/<Chip>/std_loadbuild_la module.
    Folder names under lldb_pkg/target/ are the chipset name in lowercase
    (e.g. "mavros" -> "mavros").

    @return the imported module for g_dbgContext.chipsetName
    @raise RuntimeError if the chipset is unset or has no Storage/<Chip> copy
    """
    chip_name = (g_dbgContext.chipsetName or "").lower()
    if not chip_name:
        raise RuntimeError("g_dbgContext.chipsetName is not set — run set_chipset before cdt_program")

    module_path = f"lldb_pkg.target.{chip_name}.Storage.std_loadbuild_la"
    try:
        return importlib.import_module(module_path)
    except ImportError as e:
        raise RuntimeError(f"No std_loadbuild_la implementation for chipset '{chip_name}': {e}")

# Fixed staging name/dir — mirrors cdt_program.cmm's &temp_cdt_path / &cdt_filename.
_TEMP_CDT_DIR         = "C:/Temp"
_CDT_STAGED_FILENAME  = "cdt.bin"


# ---------------------------------------------------------------------------
# Phase 1 — CDT variable container
# ---------------------------------------------------------------------------

# Self-contained CDT fixture kit (see @section Description). Top-level is the
# UFS set; emmc\ is the eMMC set. Default is Mavros's kit — pass
# fixturepath=<path> on any cdt_program call to point at a different kit.
_CDT_FIXTURE_ROOT = (
    r"\\anusat\ProductSW_NoAutoDel\Mavros\UFS_CDT"
)
_CDT_FIXTURE_EMMC_SUBDIR = "emmc"


def _zeros_cdt_binary(fixture_root=None):
    """! Path to zeros_cdt.bin at the fixture root (only exists at the root, not under emmc\\)."""
    return os.path.join(fixture_root or _CDT_FIXTURE_ROOT, "zeros_cdt.bin")


def _parse_cdt_script(fixture_root=None):
    """! Path to parse_cdt.py at the fixture root (only exists at the root, not under emmc\\)."""
    return os.path.join(fixture_root or _CDT_FIXTURE_ROOT, "parse_cdt.py")


@dataclass
class CdtVars:
    """!
    All path/partition variables cdt_program.cmm resolves before flashing.

    @var RAWPROGRAM_XML     rawprogramN.xml containing the cdt <program> entry
    @var PATCH_XML          matching patchN.xml (same N)
    @var CDT_PARTITION_NUM  physical_partition_number of the cdt entry
    @var CDT_SECTOR_NUM     start_sector of the cdt entry (raw string)
    @var CDT_LABEL          label attribute of the matched <program> entry
    @var CDT_FILENAME       filename attribute of the matched <program> entry
    @var CDT_BINARY         source CDT .bin to write (board-specific, no default)
    @var JTAGPROG_ELF       JtagProgrammer.elf path
    @var STORAGE_TYPE       "ufs" or "emmc"
    @var FIXTURE_ROOT       CDT fixture kit root actually used to resolve RAWPROGRAM_XML
    """
    RAWPROGRAM_XML:     Optional[str] = None
    PATCH_XML:          Optional[str] = None
    CDT_PARTITION_NUM:  Optional[int] = None
    CDT_SECTOR_NUM:     Optional[str] = None
    CDT_LABEL:          Optional[str] = None
    CDT_FILENAME:       Optional[str] = None
    CDT_BINARY:         Optional[str] = None
    JTAGPROG_ELF:       Optional[str] = None
    STORAGE_TYPE:       Optional[str] = None
    FIXTURE_ROOT:       Optional[str] = None


def _find_cdt_entry(xml_path: str):
    """!
    Scan a single rawprogramN.xml for the <program> entry that carries the CDT image.

    @param xml_path     path to a rawprogramN.xml file
    @return (physical_partition_number, start_sector, filename, label), or None
    """
    import xml.etree.ElementTree as ET

    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, OSError) as e:
        logger.warning(f"Could not parse {xml_path}: {e}")
        return None

    for entry in tree.getroot().iter("program"):
        label = entry.get("label", "")
        filename = entry.get("filename", "")
        if "cdt" in label.lower() or "cdt" in filename.lower():
            partition = int(entry.get("physical_partition_number", "0"), 0)
            start_sector = entry.get("start_sector", "0")
            return partition, start_sector, filename, label

    return None


def resolve_cdt_vars(debugger, meta: metaInfo, cdt_binary: Optional[str] = None,
                      xml_override: Optional[str] = None,
                      elf_override: Optional[str] = None,
                      fixture_root: Optional[str] = None) -> CdtVars:
    """!
    Phase 1 — locate the CDT partition entry and resolve all paths.

    @param debugger         LLDB debugger instance
    @param meta             metaInfo instance for the live metabuild
    @param cdt_binary       source CDT .bin to write (WRITE only)
    @param xml_override     explicit rawprogram XML to search instead of the fixture kit
    @param elf_override     explicit JtagProgrammer.elf instead of the metabuild's boot ELF
    @param fixture_root     explicit CDT fixture kit root instead of _CDT_FIXTURE_ROOT
    @return populated CdtVars
    """
    from lldb_pkg.target.clarence.Storage.std_loadbuild_la import (
        BuildVars,
        resolve_from_contents_xml as _la_resolve,
        _get_jtp,
    )
    jtp = _get_jtp()
    bv_mod = _std_loadbuild_module()
    bv = bv_mod.resolve_from_meta(meta)
    cv = CdtVars()
    cv.CDT_BINARY   = cdt_binary
    # cv.JTAGPROG_ELF = elf_override or bv_mod._jtagprog_elf_path(bv)
    cv.JTAGPROG_ELF = elf_override or jtp.elf_path

    storage_type, _xml_location, _xml_provision, _max_partitions, _programmer = \
        bv_mod._detect_storage(debugger, bv)
    cv.STORAGE_TYPE = storage_type

    if xml_override:
        candidates = [xml_override]
    else:
        fixture_root = fixture_root or _CDT_FIXTURE_ROOT
        cv.FIXTURE_ROOT = fixture_root
        fixture_dir = fixture_root if storage_type != "emmc" \
            else os.path.join(fixture_root, _CDT_FIXTURE_EMMC_SUBDIR)
        candidates = sorted(glob.glob(os.path.join(fixture_dir, "rawprogram*.xml")))

    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        found = _find_cdt_entry(candidate)
        if not found:
            continue

        phys_partition, start_sector, filename, label = found
        cv.RAWPROGRAM_XML    = candidate
        cv.CDT_PARTITION_NUM = phys_partition
        cv.CDT_SECTOR_NUM    = start_sector
        cv.CDT_LABEL         = label
        cv.CDT_FILENAME      = filename

        m = re.search(r'rawprogram(\d+)\.xml$', candidate, re.IGNORECASE)
        if m:
            patch_candidate = os.path.join(os.path.dirname(candidate), f"patch{m.group(1)}.xml")
            cv.PATCH_XML = patch_candidate if os.path.isfile(patch_candidate) else None

        logger.info(
            f"CDT entry found in {os.path.basename(candidate)}: "
            f"label={label!r} filename={filename!r} "
            f"partition={phys_partition} start_sector={start_sector}"
        )
        break

    return cv


def verify_cdt_vars(cv: CdtVars) -> None:
    """!
    Phase 1, step B — assert everything Phase 2 will need is present.

    @param cv       CdtVars from resolve_cdt_vars()
    @note CDT_BINARY is intentionally NOT checked here — see cdt_write's own check.
    """

    def _require(value, label):
        if value is None:
            raise RuntimeError(f"Could not resolve: {label}")

    _require(cv.RAWPROGRAM_XML, "RAWPROGRAM_XML (no rawprogramN.xml contained a cdt entry)")
    _require(cv.PATCH_XML, f"PATCH_XML alongside {cv.RAWPROGRAM_XML}")
    _require(cv.CDT_PARTITION_NUM, "CDT_PARTITION_NUM")
    _require(cv.CDT_SECTOR_NUM, "CDT_SECTOR_NUM")

    if not os.path.isfile(cv.JTAGPROG_ELF):
        raise FileNotFoundError(f"JTAGPROG_ELF not found: {cv.JTAGPROG_ELF!r}")

    logger.info("CdtVars verification passed.")


# ---------------------------------------------------------------------------
# Phase 2 — Exact CMM flow
# ---------------------------------------------------------------------------

def _stage_as_cdt_bin(source_path: str) -> str:
    """!
    Copy source_path to C:/Temp/cdt.bin, mirroring cdt_program.cmm Copy_cdt_as_cdt_bin.

    @param source_path      binary to stage
    @return staged path (always C:/Temp/cdt.bin)
    """
    if not os.path.isdir(_TEMP_CDT_DIR):
        os.makedirs(_TEMP_CDT_DIR)

    staged_path = os.path.join(_TEMP_CDT_DIR, _CDT_STAGED_FILENAME)
    if os.path.isfile(staged_path):
        os.remove(staged_path)

    shutil.copyfile(source_path, staged_path)
    logger.info(f"Staged {source_path} -> {staged_path}")
    return staged_path


def _program_cdt(cv: CdtVars, source_path: str) -> None:
    """!
    Stage source_path as cdt.bin, then LOAD the whole rawprogram XML and PATCH it.
    Mirrors cdt_program.cmm Program_cdt exactly, quirks included (see
    @section Description for the filename="" skip quirk on the cdt row).

    @param cv            resolved CdtVars
    @param source_path   binary to program (real CDT for WRITE, zeros for ERASE)
    """
    _stage_as_cdt_bin(source_path)

    jtp = _std_loadbuild_module()._get_jtp()
    xml_dir = os.path.dirname(cv.RAWPROGRAM_XML)
    search_paths = f"{xml_dir},{_TEMP_CDT_DIR}"

    jtp._dispatch("LOAD", auto_command=True,
                  arg_line=f"xml={cv.RAWPROGRAM_XML} searchpaths={search_paths}"
                           f" elfpath={cv.JTAGPROG_ELF}")

    jtp._dispatch("PATCH", auto_command=True,
                  arg_line=f"xml={cv.PATCH_XML} searchpaths={search_paths}"
                           f" elfpath={cv.JTAGPROG_ELF}")


def cdt_write(debugger, cv: CdtVars) -> str:
    """!
    Stage CDT_BINARY and program it. Mirrors cdt_program.cmm Main WRITE branch.

    @param debugger     LLDB debugger instance
    @param cv           resolved CdtVars (CDT_BINARY required)
    @return success message
    @note &storage_type is declared but never assigned in the CMM, so the
          original print always rendered with an empty value — reproduced
          here verbatim ("on  successfully!!!") rather than "fixed".
    """
    if not cv.CDT_BINARY:
        raise RuntimeError("CDT_BINARY not provided — pass cdtpath=<path to a board's .bin>")
    if not os.path.isfile(cv.CDT_BINARY):
        raise FileNotFoundError(f"CDT_BINARY not found: {cv.CDT_BINARY!r}")

    _program_cdt(cv, cv.CDT_BINARY)
    msg = f"Write CDT:{cv.CDT_BINARY} on  successfully!!!"
    print(msg)
    return msg


def cdt_erase(debugger, cv: CdtVars, zeros_cdt_path: str) -> str:
    """!
    Program an all-zeros binary over the CDT slot. Mirrors cdt_program.cmm
    Main ERASE branch.

    @param debugger         LLDB debugger instance
    @param cv               resolved CdtVars
    @param zeros_cdt_path   pre-existing all-zeros CDT-sized binary (caller-provided)
    @return success message
    @note Same &storage_type-is-always-empty quirk as cdt_write.
    """
    if not os.path.isfile(zeros_cdt_path):
        raise FileNotFoundError(
            f"zeros_cdt.bin not found: {zeros_cdt_path!r} — the CMM never "
            "generates this file, it must already exist."
        )
    _program_cdt(cv, zeros_cdt_path)
    msg = "Erase CDT on  successfully!!!"
    print(msg)
    return msg


def cdt_read(debugger, cv: CdtVars) -> str:
    """!
    DUMP the CDT sector range to C:/Temp/cdt.bin and hand off to parse_cdt.py.
    Mirrors cdt_program.cmm Read_cdt.

    @param debugger     LLDB debugger instance
    @param cv           resolved CdtVars
    @return status message
    @note parse_cdt.py is not ported into this repo — shells out to the copy
          on the fixture kit share instead, same as the CMM's os.window.
    """
    import shutil
    import subprocess

    if not os.path.isdir(_TEMP_CDT_DIR):
        os.makedirs(_TEMP_CDT_DIR)
    dump_path = os.path.join(_TEMP_CDT_DIR, _CDT_STAGED_FILENAME)
    if os.path.isfile(dump_path):
        os.remove(dump_path)

    jtp = _std_loadbuild_module()._get_jtp()
    jtp._dispatch("DUMP", auto_command=True,
                  arg_line=f"startsector={cv.CDT_SECTOR_NUM} "
                           f"physicalpartition={cv.CDT_PARTITION_NUM} "
                           f"numsectors=1 dumpfile={dump_path} elfpath={cv.JTAGPROG_ELF}")

    parse_cdt_script = _parse_cdt_script(cv.FIXTURE_ROOT)
    if os.path.isfile(parse_cdt_script):
        python_exe = shutil.which("python") or shutil.which("python3") or "python"
        logger.info(f"Launching {parse_cdt_script} {dump_path}")
        subprocess.Popen(
            ["cmd", "/k", python_exe, parse_cdt_script, dump_path],
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
    else:
        logger.warning(f"parse_cdt.py not found at {parse_cdt_script} — "
                        f"dumped raw CDT bytes to {dump_path}, not decoded.")

    msg = "Read CDT from  finished"
    print(msg)
    return msg


# ---------------------------------------------------------------------------
# LLDB integration
# ---------------------------------------------------------------------------

def __lldb_init_module(debugger, internal_dict):
    """! Entry point when imported directly via 'command script import'."""
    debugger.HandleCommand("command script add -f cdt_program.cdt_program cdt_program")
    print("cdt_program command registered.")


def cdt_program(debugger, command, exe_ctx, result):
    """!
    LLDB command to read/write/erase the CDT partition via JtagProgrammer.

    Usage:  cdt_program <OPERATION> [cdtpath=<path>] [xmlpath=<path>] [elfpath=<path>] [zerospath=<path>] [fixturepath=<path>]
        VERIFY   resolve + sanity-check paths only, no target I/O
        WRITE    stage cdtpath (or default board binary) as cdt.bin, LOAD + PATCH
        READ     DUMP the cdt sector range to C:/Temp/cdt.bin, decode via parse_cdt.py
        ERASE    same as WRITE but sourced from zerospath (default: fixture kit's zeros_cdt.bin)

    fixturepath= points at a different CDT fixture kit than the built-in
    Mavros one (see _CDT_FIXTURE_ROOT) — same layout required: rawprogram*.xml
    (with a "cdt" <program> entry) + matching patchN.xml, zeros_cdt.bin,
    parse_cdt.py, plus an emmc\\ subdirectory for eMMC targets.

    @param debugger     An instance of the SBDebugger Object that provides
                        access to SBTargets used for debugging.
    @param command      String containing all the input arguments for the
                        used command (OPERATION plus cdtpath=/xmlpath=/
                        elfpath=/zerospath=/fixturepath=).
    @param exe_ctx      Execution context for the current command invocation.
    @param result       A return object which encapsulates success/failure
                        information for the command and output text that
                        needs to be printed as a result of the command.

    Examples:
        cdt_program VERIFY
        cdt_program VERIFY cdtpath=\\\\some\\other\\board.bin
        cdt_program VERIFY xmlpath=C:/meta/common/build/ufs/rawprogram3.xml
        cdt_program WRITE
        cdt_program WRITE cdtpath=\\\\some\\other\\board.bin
        cdt_program READ
        cdt_program ERASE
        cdt_program ERASE zerospath=C:/temp/zeros_cdt.bin
        cdt_program VERIFY fixturepath=\\\\some\\other\\fixture_kit
    """
    import shlex
    import traceback

    try:
        # non-POSIX mode keeps backslashes literal so UNC paths survive shlex.split
        tokens = shlex.split(command.strip(), posix=False) if command.strip() else []
        operation = tokens[0].upper() if tokens else ""

        cdt_path      = None
        xml_path      = None
        elf_path      = None
        zeros_path    = None
        fixture_path  = None
        for tok in tokens[1:]:
            if tok.startswith("cdtpath="):
                cdt_path = tok.split("=", 1)[1]
            elif tok.startswith("xmlpath="):
                xml_path = tok.split("=", 1)[1]
            elif tok.startswith("elfpath="):
                elf_path = tok.split("=", 1)[1]
            elif tok.startswith("zerospath="):
                zeros_path = tok.split("=", 1)[1]
            elif tok.startswith("fixturepath="):
                fixture_path = tok.split("=", 1)[1]

        if not operation:
            print("Usage: cdt_program VERIFY|WRITE|READ|ERASE [cdtpath=<path>] [xmlpath=<path>] [elfpath=<path>] [zerospath=<path>] [fixturepath=<path>]")
            return

        meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo

        if operation == "VERIFY":
            cv = resolve_cdt_vars(debugger, meta, cdt_path, xml_path, elf_path, fixture_path)
            print("\n=== CdtVars ===")
            for name, value in vars(cv).items():
                print(f"  {name:<20} = {value}")
            print()
            try:
                verify_cdt_vars(cv)
                print("All required paths resolved OK.")
            except (RuntimeError, FileNotFoundError) as e:
                print(f"VERIFY FAILED: {e}")
            return

        # VERIFY is exempt from this gate; WRITE/READ/ERASE are the only valid operations
        if operation not in ("WRITE", "READ", "ERASE"):
            print(f"ERROR:operation {operation} is not correct.")
            print("Operation should be WRITE/READ/ERASE.")
            return

        cv = resolve_cdt_vars(debugger, meta, cdt_path, xml_path, elf_path, fixture_path)
        verify_cdt_vars(cv)

        if operation == "WRITE":
            cdt_write(debugger, cv)
            return

        if operation == "READ":
            cdt_read(debugger, cv)
            return

        if operation == "ERASE":
            cdt_erase(debugger, cv, zeros_path or _zeros_cdt_binary(cv.FIXTURE_ROOT))
            return

    except Exception:
        msg = traceback.format_exc()
        logger.error("cdt_program failed:\n%s", msg)
        result.SetError(msg)
