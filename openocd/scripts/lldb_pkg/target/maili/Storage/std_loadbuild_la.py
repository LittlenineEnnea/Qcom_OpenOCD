#
# Copyright (c) 2026 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file std_loadbuild_la.py
#
# @section Description
# Python port of Maili's std_loadbuild_la.cmm — LA MSM HLOS-specific build
# loading. Drives jtagprogrammer.py to ERASE / LOADCOMMON / LOADFULL / LOADIMG
# a Maili metabuild onto UFS storage.
#
# Architecture:
#   Phase 1 — variable resolution  (resolve_maili / verify_maili_vars)
#   Phase 2 — load flow            (loadbuild_maili)
#
# Relationship to std_loadbuild_la.py (Clarence) / Kaanapali's port:
#   - BuildVars extended to MailiBuildVars (new buildroots + binaries)
#   - resolve_from_contents_xml reused for the shared fields, then contents.xml
#     is re-parsed for every Maili-specific cmm_root_path_var / cmm_file_var
#   - verify_maili_vars / _maili_binary_list / _maili_search_paths /
#     _maili_loadimg_list are Maili-specific — Maili's LOADCOMMON path has no
#     META_VARIANT/"CORE" branch at all (unlike Clarence/Kaana), and several
#     binaries that are optional on Kaana (SOCCP_BINARY, TZ_QTI_CONFIG_BINARY,
#     HYP_DEVCFG_BINARY, STORESEC_BINARY, SPUSERVICE_BINARY) are unconditionally
#     required here — see the .cmm's FILEXIST FATALEXITUSER block, lines 134-217.
#
# Known quirks carried over from the source .cmm (see std_loadbuild_la.cmm):
#   - &Devcfg_Binary_Buildroot is a LOCAL var that is declared but never
#     assigned before use as TZ_DEVCFG_BINARY's buildroot in binary_list
#     construction (line 313/598). Harmless in practice — OS.FILE.NAME() only
#     extracts the basename, so the buildroot value never affects the
#     resulting binary_list entry. We just use TZ_BUILDROOT for basename
#     purposes below.
#   - The "appsboot" LOADIMG branch (lines 664-678) never appends
#     APPSBOOT_BINARY to binary_list — only ANDROID_ESP_BINARY (gbl.bin) is
#     conditionally appended, guarded by FILE.EXIST. If ANDROID_ESP_BINARY
#     does not exist, the branch produces an empty binary_list. This looks
#     like an incomplete block in the source .cmm; ported literally rather
#     than "fixed" — flag to the target team if APPSBOOT_BINARY was meant to
#     be flashed here too.
#   - KERNEL_ABL_BINARY / APPSKERNEL_BUILDROOT are referenced in the
#     LOADCOMMON SearchPaths list (line 406) but never appear in any
#     binary_list construction or FILEXIST check — vestigial, ported as-is.
#   - JtagProgrammer.elf/.cmm path resolution: the .cmm hardcodes
#     "&META_CHIPSET_DIR_N\loadbuild\JtagProgrammer\JtagProgrammer.elf", which
#     has no direct Python equivalent (META_CHIPSET_DIR_N is a T32 global, not
#     a contents.xml var). Since jtagprogrammer.py's own _resolve_elf_path()
#     already resolves this independently (explicit elfpath= > contents.xml
#     "boot" build scan > hardcoded fallback), loadbuild_maili() below just
#     reuses the already-running JtagProgrammer instance's resolved
#     jtp.elf_path instead of recomputing it here.

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Optional

_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from utils.debug_context import g_dbgContext, logger
from utils.meta_info import metaInfo
from lldb_pkg.target.clarence.Storage.std_loadbuild_la import (
    BuildVars,
    resolve_from_contents_xml as _la_resolve,
    _get_jtp,
)


# ---------------------------------------------------------------------------
# Phase 1 — Maili variable container
# ---------------------------------------------------------------------------

@dataclass
class MailiBuildVars(BuildVars):
    """
    Extends BuildVars with all Maili-specific buildroots and binaries.

    New buildroots (cmm_root_path_var in contents.xml):
        SSG_BUILDROOT        TME.FW.* build
        ACP_BUILDROOT         ACPOLICY.XF.* build
        UTIL_FW_BUILDROOT      qupv3fw.elf / shrm.elf / dcb.bin build
        PDP_BUILDROOT          PDP.FW.* build
        SOCCP_BUILDROOT        SOCCP.FW.* build
        QECP_BUILDROOT         QECP.FW.* build
        TFA_BUILDROOT          bl31.mbn build
        HYP_BUILDROOT          hypvm.mbn build (genuinely separate from TZ_BUILDROOT on Maili)
        UEFI_BUILDROOT         uefi.elf build
        QUANTUM_BUILDROOT      Quantum.fv / QuantumSDK build (optional)
        VPLAN_BUILDROOT        vplan.bin / vplan_mini.bin build
        APPSKERNEL_BUILDROOT   alternate ABL build (vestigial — never used in binary_list)

    New binaries (cmm_file_var in contents.xml):
        SOC_VIEW_ELF          signed_firmware_soc_view.elf   (SSG_BUILDROOT)
        TME_SEQ_PATCH_ELF     sequencer_ram.elf              (SSG_BUILDROOT)
        HYP_AC_CONFIG_MBN     hyp_ac_config.mbn              (ACP_BUILDROOT)
        TZ_AC_CONFIG_MBN      tz_ac_config.mbn               (ACP_BUILDROOT)
        XBL_AC_CONFIG_MBN     xbl_ac_config.mbn              (ACP_BUILDROOT)
        TME_CONFIG_ELF        tme_config.elf                 (ACP_BUILDROOT)
        SHRM_BINARY           shrm_lp5.elf                   (UTIL_FW_BUILDROOT)
        SHRM_BINARY_LP6       shrm_lp6.elf        optional   (UTIL_FW_BUILDROOT)
        DCB_BINARY            dcb.bin                        (UTIL_FW_BUILDROOT)
        PMIC_BINARY           psi.bin             optional   (BOOT_BUILDROOT)  — FILEXIST commented out, always appended
        MULTIIMGQTI_BINARY    multi_image_qti.mbn optional   (METAROOTDIR)     — FILEXIST commented out
        CPUCP_DTB             cpucp_dtbs.elf                 (CPUCP_BUILDROOT)
        PDP_BINARY            pdp.elf                        (PDP_BUILDROOT)
        PDP_CDB_BINARY        pdp_cdb.elf                    (PDP_BUILDROOT)
        SOCCP_BINARY          soccp.bin                      (SOCCP_BUILDROOT)
        DCP_BINARY            dcp.bin                        (DCP_BUILDROOT_Vendor = APPSVENDOR_BUILDROOT)
        QECP_BINARY           qecp binary                    (QECP_BUILDROOT)
        DCD_BINARY            dcd.mbn                        (QECP_BUILDROOT)
        TFA_BINARY            bl31.mbn                       (TFA_BUILDROOT)
        TZ_QTI_CONFIG_BINARY  tz_qti_config.mbn              (TZ_BUILDROOT)
        TZ_DEVCFG_BINARY      tz_oem_config.mbn              (TZ_BUILDROOT — buildroot name differs from Clarence's TZDEVCFG_BINARY)
        HYP_DEVCFG_BINARY     hyp config file                (HYP_BUILDROOT)
        UEFI                  uefi.elf                       (UEFI_BUILDROOT)
        UEFI_DTB              uefi_dtbs.elf                  (UEFI_BUILDROOT)
        UEFI_IMAGEFV          imagefv.elf                    (UEFI_BUILDROOT)
        UEFI_TOOLSFV          tools.fv                       (UEFI_BUILDROOT)
        SECRETKEEPER          secretkeeper app                (TZAPPS_BUILDROOT)
        SPUSERVICE_BINARY     spu_service.mbn                 (TZAPPS_BUILDROOT)
        QUANTUMFV_BINARY      Quantum.fv          optional   (QUANTUM_BUILDROOT)
        QUANTUMSDK_BINARY     QuantumSDK binary   optional   (QUANTUM_BUILDROOT)
        VP_BINARY             vplan.bin                      (VPLAN_BUILDROOT)
        VP_MINI_BINARY        vplan_mini.bin                 (VPLAN_BUILDROOT)
        ANDROID_ESP_BINARY    gbl.bin             optional   (APPSVENDOR_BUILDROOT)
        KERNEL_ABL_BINARY     alternate abl.elf   optional   (APPSKERNEL_BUILDROOT — vestigial, never flashed)

    Hardcoded (not contents.xml-derived — literal relative paths in the .cmm):
        NON_HLOS_BINARY       common/build/ufs/bin/asic/NON-HLOS.bin       (relative to METAROOTDIR)
        NON_HLOS_CORE_BINARY  common/build/ufs/bin/asic/Core_NON-HLOS.bin  (relative to METAROOTDIR)

    Notes:
        - DCP_BUILDROOT_Vendor in the .cmm is a literal alias for
          APPSVENDOR_BUILDROOT (no separate buildroot exists) — use
          bv.APPSVENDOR_BUILDROOT directly, no separate field.
        - &Devcfg_Binary_Buildroot is declared but never assigned in the .cmm
          (see module docstring) — TZ_DEVCFG_BINARY's basename does not
          depend on it, so no field is needed for it either.
        - Reused unchanged from Clarence's BuildVars: BOOT_BUILDROOT,
          TZ_BUILDROOT, TZAPPS_BUILDROOT, AOP_BUILDROOT, CPUCP_BUILDROOT,
          APPSVENDOR_BUILDROOT, METAROOTDIR, METASCRIPTSDIR, BOOT_BINARY,
          BOOT_CONFIG, XBLRAMDUMP_BINARY, QUP_BINARY, MULTIIMGOEM_BINARY,
          TZ_BINARY, HYP_BINARY, KEYMASTER_BINARY, UEFISEC_BINARY,
          CMNLIB_BINARY/CMNLIB64_BINARY/FMAC_BINARY (unused — stay None),
          STORESEC_BINARY, APPSBOOT_BINARY, AOP_BINARY, AOP_DEVCFG_BINARY,
          CPUCP_BINARY, PARTITION_XML_ROOT, META_VARIANT, PRODUCT_FLAVOR,
          META_CMM_LOGS.
    """

    # --- Maili-only buildroots ---
    SSG_BUILDROOT:          Optional[str] = None
    ACP_BUILDROOT:          Optional[str] = None
    UTIL_FW_BUILDROOT:      Optional[str] = None
    PDP_BUILDROOT:          Optional[str] = None
    SOCCP_BUILDROOT:        Optional[str] = None
    QECP_BUILDROOT:         Optional[str] = None
    TFA_BUILDROOT:          Optional[str] = None
    HYP_BUILDROOT:          Optional[str] = None
    UEFI_BUILDROOT:         Optional[str] = None
    QUANTUM_BUILDROOT:      Optional[str] = None
    VPLAN_BUILDROOT:        Optional[str] = None
    APPSKERNEL_BUILDROOT:   Optional[str] = None   # vestigial

    # --- Maili-only binaries ---
    SOC_VIEW_ELF:           Optional[str] = None
    TME_SEQ_PATCH_ELF:      Optional[str] = None
    HYP_AC_CONFIG_MBN:      Optional[str] = None
    TZ_AC_CONFIG_MBN:       Optional[str] = None
    XBL_AC_CONFIG_MBN:      Optional[str] = None
    TME_CONFIG_ELF:         Optional[str] = None
    SHRM_BINARY:            Optional[str] = None
    SHRM_BINARY_LP6:        Optional[str] = None   # optional
    DCB_BINARY:             Optional[str] = None
    PMIC_BINARY:            Optional[str] = None   # optional, always appended if present
    MULTIIMGQTI_BINARY:     Optional[str] = None   # optional
    CPUCP_DTB:              Optional[str] = None
    PDP_BINARY:             Optional[str] = None
    PDP_CDB_BINARY:         Optional[str] = None
    SOCCP_BINARY:           Optional[str] = None
    DCP_BINARY:             Optional[str] = None
    QECP_BINARY:            Optional[str] = None
    DCD_BINARY:             Optional[str] = None
    TFA_BINARY:             Optional[str] = None
    TZ_QTI_CONFIG_BINARY:   Optional[str] = None
    TZ_DEVCFG_BINARY:       Optional[str] = None
    HYP_DEVCFG_BINARY:      Optional[str] = None
    UEFI:                   Optional[str] = None
    UEFI_DTB:               Optional[str] = None
    UEFI_IMAGEFV:           Optional[str] = None
    UEFI_TOOLSFV:           Optional[str] = None
    SECRETKEEPER:           Optional[str] = None
    SPUSERVICE_BINARY:      Optional[str] = None
    QUANTUMFV_BINARY:       Optional[str] = None   # optional
    QUANTUMSDK_BINARY:      Optional[str] = None   # optional
    VP_BINARY:              Optional[str] = None
    VP_MINI_BINARY:         Optional[str] = None
    ANDROID_ESP_BINARY:     Optional[str] = None   # optional
    KERNEL_ABL_BINARY:      Optional[str] = None   # optional, vestigial

    # --- hardcoded relative paths (not contents.xml-derived) ---
    NON_HLOS_BINARY:        Optional[str] = None
    NON_HLOS_CORE_BINARY:   Optional[str] = None


# ---------------------------------------------------------------------------
# Phase 1 — Resolution
# ---------------------------------------------------------------------------

def resolve_maili(meta: metaInfo) -> MailiBuildVars:
    """
    Resolve all MailiBuildVars from contents.xml, then apply engineering
    build path overrides via metaInfo.getImageBasePath (same pattern as
    Clarence/Kaana).
    """
    import re
    import platform
    from xml.etree import ElementTree as ET

    contents_xml = str(g_dbgContext.contentsXmlPath)
    meta_root    = meta.getMetaPath()

    # Start from the base LA resolver to populate shared fields
    base: BuildVars = _la_resolve(contents_xml, meta_root)

    bv = MailiBuildVars(**{k: v for k, v in vars(base).items()
                           if k in BuildVars.__dataclass_fields__})

    # ----------------------------------------------------------------
    # Re-parse contents.xml for Maili-specific fields
    # ----------------------------------------------------------------
    is_windows = platform.system() == "Windows"

    def _strip_tokens(s: str) -> str:
        s = re.sub(r'\$\{[^:}]+:([^}]+)\}', r'\1', s)
        s = re.sub(r'\$\{[^}]+\}', '', s)
        return s

    def _clean(s: str) -> str:
        s = _strip_tokens(s or "").replace("\\", "/")
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

    def _abs(path: str) -> str:
        if path.startswith("./") or path == ".":
            return _join(bv.METAROOTDIR, path[2:] if path.startswith("./") else "")
        return path

    tree = ET.parse(contents_xml)
    root = tree.getroot()

    builds_flat = root.find("builds_flat")
    if builds_flat is None:
        raise RuntimeError("contents.xml has no <builds_flat> element")

    maili_root_vars: dict = {}
    maili_file_vars: dict = {}

    for build in builds_flat:
        if is_windows:
            root_path_elem = build.find("windows_root_path")
        else:
            root_path_elem = build.find("linux_root_path")

        if root_path_elem is None:
            continue

        build_root = _abs(_clean(root_path_elem.text or ""))
        var = root_path_elem.get("cmm_root_path_var")
        if var:
            maili_root_vars[var] = build_root

        for child in build:
            fvar = child.get("cmm_file_var")
            if not fvar:
                continue
            file_name_elem = child.find("file_name")
            file_path_elem = child.find("file_path")
            if file_name_elem is None:
                continue
            file_name = _clean(file_name_elem.text or "")
            file_path = _clean(file_path_elem.text or "") if file_path_elem is not None else ""
            if file_name == ".":
                full = _join(build_root, file_path)
            else:
                full = _join(build_root, file_path, file_name)
            maili_file_vars[fvar] = _abs(full)

    # --- Maili-only buildroots ---
    bv.SSG_BUILDROOT         = maili_root_vars.get("SSG_BUILDROOT")
    bv.ACP_BUILDROOT         = maili_root_vars.get("ACP_BUILDROOT")
    bv.UTIL_FW_BUILDROOT     = maili_root_vars.get("UTIL_FW_BUILDROOT")
    bv.PDP_BUILDROOT         = maili_root_vars.get("PDP_BUILDROOT")
    bv.SOCCP_BUILDROOT       = maili_root_vars.get("SOCCP_BUILDROOT")
    bv.QECP_BUILDROOT        = maili_root_vars.get("QECP_BUILDROOT")
    bv.TFA_BUILDROOT         = maili_root_vars.get("TFA_BUILDROOT")
    bv.HYP_BUILDROOT         = maili_root_vars.get("HYP_BUILDROOT")
    bv.UEFI_BUILDROOT        = maili_root_vars.get("UEFI_BUILDROOT")
    bv.QUANTUM_BUILDROOT     = maili_root_vars.get("QUANTUM_BUILDROOT")
    bv.VPLAN_BUILDROOT       = maili_root_vars.get("VPLAN_BUILDROOT")
    bv.APPSKERNEL_BUILDROOT  = maili_root_vars.get("APPSKERNEL_BUILDROOT")

    # --- Maili-only binaries ---
    bv.SOC_VIEW_ELF          = maili_file_vars.get("SOC_VIEW_ELF")
    bv.TME_SEQ_PATCH_ELF     = maili_file_vars.get("TME_SEQ_PATCH_ELF")
    bv.HYP_AC_CONFIG_MBN     = maili_file_vars.get("HYP_AC_CONFIG_MBN")
    bv.TZ_AC_CONFIG_MBN      = maili_file_vars.get("TZ_AC_CONFIG_MBN")
    bv.XBL_AC_CONFIG_MBN     = maili_file_vars.get("XBL_AC_CONFIG_MBN")
    bv.TME_CONFIG_ELF        = maili_file_vars.get("TME_CONFIG_ELF")
    bv.SHRM_BINARY           = maili_file_vars.get("SHRM_BINARY")
    bv.SHRM_BINARY_LP6       = maili_file_vars.get("SHRM_BINARY_LP6")
    bv.DCB_BINARY            = maili_file_vars.get("DCB_BINARY")
    bv.PMIC_BINARY           = maili_file_vars.get("PMIC_BINARY")
    bv.MULTIIMGQTI_BINARY    = maili_file_vars.get("MULTIIMGQTI_BINARY")
    bv.CPUCP_DTB             = maili_file_vars.get("CPUCP_DTB")
    bv.PDP_BINARY            = maili_file_vars.get("PDP_BINARY")
    bv.PDP_CDB_BINARY        = maili_file_vars.get("PDP_CDB_BINARY")
    bv.SOCCP_BINARY          = maili_file_vars.get("SOCCP_BINARY")
    bv.DCP_BINARY            = maili_file_vars.get("DCP_BINARY")
    bv.QECP_BINARY           = maili_file_vars.get("QECP_BINARY")
    bv.DCD_BINARY            = maili_file_vars.get("DCD_BINARY")
    bv.TFA_BINARY            = maili_file_vars.get("TFA_BINARY")
    bv.TZ_QTI_CONFIG_BINARY  = maili_file_vars.get("TZ_QTI_CONFIG_BINARY")
    bv.TZ_DEVCFG_BINARY      = maili_file_vars.get("TZ_DEVCFG_BINARY")
    bv.HYP_DEVCFG_BINARY     = maili_file_vars.get("HYP_DEVCFG_BINARY")
    bv.UEFI                  = maili_file_vars.get("UEFI")
    bv.UEFI_DTB              = maili_file_vars.get("UEFI_DTB")
    bv.UEFI_IMAGEFV          = maili_file_vars.get("UEFI_IMAGEFV")
    bv.UEFI_TOOLSFV          = maili_file_vars.get("UEFI_TOOLSFV")
    bv.SECRETKEEPER          = maili_file_vars.get("SECRETKEEPER")
    bv.SPUSERVICE_BINARY     = maili_file_vars.get("SPUSERVICE_BINARY")
    bv.QUANTUMFV_BINARY      = maili_file_vars.get("QUANTUMFV_BINARY")
    bv.QUANTUMSDK_BINARY     = maili_file_vars.get("QUANTUMSDK_BINARY")
    bv.VP_BINARY             = maili_file_vars.get("VP_BINARY")
    bv.VP_MINI_BINARY        = maili_file_vars.get("VP_MINI_BINARY")
    bv.ANDROID_ESP_BINARY    = maili_file_vars.get("ANDROID_ESP_BINARY")
    bv.KERNEL_ABL_BINARY     = maili_file_vars.get("KERNEL_ABL_BINARY")

    # --- hardcoded relative paths (mirror .cmm lines 66-67) ---
    if bv.METAROOTDIR:
        bv.NON_HLOS_BINARY      = _join(bv.METAROOTDIR, "common/build/ufs/bin/asic/NON-HLOS.bin")
        bv.NON_HLOS_CORE_BINARY = _join(bv.METAROOTDIR, "common/build/ufs/bin/asic/Core_NON-HLOS.bin")

    # ----------------------------------------------------------------
    # Engineering build path overrides (same pattern as Clarence/Kaana)
    # ----------------------------------------------------------------
    _maili_buildroot_map = {
        "boot":   ("BOOT_BUILDROOT",  ["BOOT_BINARY", "BOOT_CONFIG", "XBLRAMDUMP_BINARY", "PMIC_BINARY"]),
        "tz":     ("TZ_BUILDROOT",    ["TZ_BINARY", "TZ_QTI_CONFIG_BINARY", "TZ_DEVCFG_BINARY"]),
        "aop":    ("AOP_BUILDROOT",   ["AOP_BINARY", "AOP_DEVCFG_BINARY"]),
        "tme":    ("SSG_BUILDROOT",   ["SOC_VIEW_ELF", "TME_SEQ_PATCH_ELF"]),
        "acp":    ("ACP_BUILDROOT",   ["HYP_AC_CONFIG_MBN", "TZ_AC_CONFIG_MBN", "XBL_AC_CONFIG_MBN", "TME_CONFIG_ELF"]),
        "util_fw":("UTIL_FW_BUILDROOT", ["QUP_BINARY", "SHRM_BINARY", "SHRM_BINARY_LP6", "DCB_BINARY"]),
        "pdp":    ("PDP_BUILDROOT",   ["PDP_BINARY", "PDP_CDB_BINARY"]),
        "soccp":  ("SOCCP_BUILDROOT", ["SOCCP_BINARY"]),
        "qecp":   ("QECP_BUILDROOT",  ["QECP_BINARY", "DCD_BINARY"]),
        "tfa":    ("TFA_BUILDROOT",   ["TFA_BINARY"]),
        "hyp":    ("HYP_BUILDROOT",   ["HYP_BINARY", "HYP_DEVCFG_BINARY"]),
        "uefi":   ("UEFI_BUILDROOT",  ["UEFI", "UEFI_DTB", "UEFI_IMAGEFV", "UEFI_TOOLSFV"]),
        "cpucp":  ("CPUCP_BUILDROOT", ["CPUCP_BINARY", "CPUCP_DTB"]),
        "vplan":  ("VPLAN_BUILDROOT", ["VP_BINARY", "VP_MINI_BINARY"]),
        "quantum":("QUANTUM_BUILDROOT", ["QUANTUMFV_BINARY", "QUANTUMSDK_BINARY"]),
    }

    for image_name, (buildroot_attr, binary_attrs) in _maili_buildroot_map.items():
        eng_path = meta.getImageBasePath(image_name)
        if not eng_path or eng_path == "ERROR":
            continue
        xml_buildroot = getattr(bv, buildroot_attr, None)
        if not xml_buildroot:
            continue
        eng_path_norm = eng_path.replace("\\", "/").rstrip("/")
        xml_buildroot_norm = xml_buildroot.replace("\\", "/").rstrip("/")
        if eng_path_norm == xml_buildroot_norm:
            continue
        logger.info("Maili engineering override: %s -> %s", image_name, eng_path_norm)
        setattr(bv, buildroot_attr, eng_path_norm)
        for attr in binary_attrs:
            old_val = getattr(bv, attr, None)
            if old_val and old_val.replace("\\", "/").startswith(xml_buildroot_norm):
                new_val = eng_path_norm + old_val.replace("\\", "/")[len(xml_buildroot_norm):]
                setattr(bv, attr, new_val)

    return bv


def resolve_from_meta(meta: metaInfo) -> MailiBuildVars:
    """Generic-name alias for resolve_maili() — mirrors Kaana's convention so
    any caller that dynamically imports Storage/<Chip>/std_loadbuild_la can
    call resolve_from_meta() by a fixed name regardless of chipset."""
    return resolve_maili(meta)


# ---------------------------------------------------------------------------
# Phase 1 — Verification
# ---------------------------------------------------------------------------

def verify_maili_vars(bv: MailiBuildVars, loadoption: str) -> None:
    """
    Mirror the .cmm's FILEXIST FATALEXITUSER block inside LOADCOMMON
    (std_loadbuild_la.cmm lines 134-217). Raises FileNotFoundError on the
    first missing required path.

    Unlike Clarence/Kaana, Maili's LOADCOMMON path has no META_VARIANT/"CORE"
    branch — APPSBOOT_BINARY / CATEFV_BINARY / TOOLSFV_BINARY are never
    required (and never flashed) here.
    """
    def _require(path: Optional[str], label: str):
        if not path or not os.path.exists(path):
            raise FileNotFoundError(
                f"Required binary not found: {label} -> {path!r}"
            )

    _require(bv.BOOT_BINARY, "BOOT_BINARY")

    if loadoption in ("LOADCOMMON", "LOADFULL"):
        # TME (SSG)
        _require(bv.SOC_VIEW_ELF,        "SOC_VIEW_ELF")
        _require(bv.TME_SEQ_PATCH_ELF,   "TME_SEQ_PATCH_ELF")
        # ACPolicy
        _require(bv.HYP_AC_CONFIG_MBN,   "HYP_AC_CONFIG_MBN")
        _require(bv.TZ_AC_CONFIG_MBN,    "TZ_AC_CONFIG_MBN")
        _require(bv.XBL_AC_CONFIG_MBN,   "XBL_AC_CONFIG_MBN")
        _require(bv.TME_CONFIG_ELF,      "TME_CONFIG_ELF")
        # UTIL_FW
        _require(bv.QUP_BINARY,          "QUP_BINARY")
        _require(bv.SHRM_BINARY,         "SHRM_BINARY")
        _require(bv.DCB_BINARY,          "DCB_BINARY")
        # XBL
        _require(bv.BOOT_BINARY,         "BOOT_BINARY")
        _require(bv.BOOT_CONFIG,         "BOOT_CONFIG")
        _require(bv.XBLRAMDUMP_BINARY,   "XBLRAMDUMP_BINARY")
        # AOP
        _require(bv.AOP_BINARY,          "AOP_BINARY")
        _require(bv.AOP_DEVCFG_BINARY,   "AOP_DEVCFG_BINARY")
        # CPUCP
        _require(bv.CPUCP_BINARY,        "CPUCP_BINARY")
        _require(bv.CPUCP_DTB,           "CPUCP_DTB")
        # PDP
        _require(bv.PDP_BINARY,          "PDP_BINARY")
        _require(bv.PDP_CDB_BINARY,      "PDP_CDB_BINARY")
        # SOCCP
        _require(bv.SOCCP_BINARY,        "SOCCP_BINARY")
        # DCP (buildroot = APPSVENDOR_BUILDROOT, aka DCP_BUILDROOT_Vendor)
        _require(bv.DCP_BINARY,          "DCP_BINARY")
        # QECP
        _require(bv.QECP_BINARY,         "QECP_BINARY")
        _require(bv.DCD_BINARY,          "DCD_BINARY")
        # TZ
        _require(bv.TZ_BINARY,           "TZ_BINARY")
        _require(bv.TZ_QTI_CONFIG_BINARY,"TZ_QTI_CONFIG_BINARY")
        _require(bv.TZ_DEVCFG_BINARY,    "TZ_DEVCFG_BINARY")
        # TFA
        _require(bv.TFA_BINARY,          "TFA_BINARY")
        # HYP
        _require(bv.HYP_BINARY,          "HYP_BINARY")
        _require(bv.HYP_DEVCFG_BINARY,   "HYP_DEVCFG_BINARY")
        # UEFI
        _require(bv.UEFI,                "UEFI")
        _require(bv.UEFI_DTB,            "UEFI_DTB")
        _require(bv.UEFI_IMAGEFV,        "UEFI_IMAGEFV")
        _require(bv.UEFI_TOOLSFV,        "UEFI_TOOLSFV")
        # TZ-apps
        _require(bv.UEFISEC_BINARY,      "UEFISEC_BINARY")
        _require(bv.STORESEC_BINARY,     "STORESEC_BINARY")
        _require(bv.KEYMASTER_BINARY,    "KEYMASTER_BINARY")
        _require(bv.SECRETKEEPER,        "SECRETKEEPER")
        _require(bv.SPUSERVICE_BINARY,   "SPUSERVICE_BINARY")
        # QUANTUM is optional (commented out in the .cmm) — no check here
        # VPLAN
        _require(bv.VP_BINARY,           "VP_BINARY")
        _require(bv.VP_MINI_BINARY,      "VP_MINI_BINARY")

    logger.info("MailiBuildVars verification passed for loadoption=%s", loadoption)


# ---------------------------------------------------------------------------
# Phase 2 helpers — mirror .cmm binary list / search path construction
# ---------------------------------------------------------------------------

def _maili_detect_storage(bv: MailiBuildVars):
    """
    Maili is hardcoded UFS (.cmm sets &StorageType="ufs" &MaxPartitions=6
    unconditionally — no runtime BOOT_CONFIG HWIO detection like Clarence).
    Returns (storage_type, xml_location, xml_provision, max_partitions).
    """
    xml_location  = bv.PARTITION_XML_ROOT
    xml_provision = os.path.join(bv.METAROOTDIR, "config", "ufs", "provision") \
                    if bv.METAROOTDIR else ""
    return "ufs", xml_location, xml_provision, 6


def _detect_storage(debugger, bv: MailiBuildVars):
    """Generic-name alias for _maili_detect_storage(), matching Kaana's
    5-tuple convention (trailing None for an unused programmer path)."""
    storage_type, xml_location, xml_provision, max_partitions = _maili_detect_storage(bv)
    return storage_type, xml_location, xml_provision, max_partitions, None


def _maili_binary_list(bv: MailiBuildVars) -> str:
    """
    Mirror .cmm LOADCOMMON binary_list construction (lines 223-367).
    Returns comma-separated bare filenames, in the exact .cmm order.
    """
    names = []

    def _add(path: Optional[str]):
        if path:
            names.append(os.path.basename(path))

    # TME / ACP
    _add(bv.SOC_VIEW_ELF)
    _add(bv.TME_SEQ_PATCH_ELF)
    _add(bv.HYP_AC_CONFIG_MBN)
    _add(bv.TZ_AC_CONFIG_MBN)
    _add(bv.XBL_AC_CONFIG_MBN)
    _add(bv.TME_CONFIG_ELF)

    # UTIL_FW — SHRM_BINARY_LP6 appended unconditionally despite its
    # FILEXIST check being commented out in the .cmm (line 150/496)
    _add(bv.QUP_BINARY)
    _add(bv.SHRM_BINARY)
    _add(bv.SHRM_BINARY_LP6)
    _add(bv.DCB_BINARY)

    # XBL — PMIC_BINARY appended unconditionally despite its FILEXIST check
    # being commented out in the .cmm (line 157/261)
    _add(bv.BOOT_BINARY)
    _add(bv.BOOT_CONFIG)
    _add(bv.XBLRAMDUMP_BINARY)
    _add(bv.PMIC_BINARY)

    # AOP
    _add(bv.AOP_BINARY)
    _add(bv.AOP_DEVCFG_BINARY)

    # CPUCP
    _add(bv.CPUCP_BINARY)
    _add(bv.CPUCP_DTB)

    # PDP
    _add(bv.PDP_BINARY)
    _add(bv.PDP_CDB_BINARY)

    # SOCCP
    _add(bv.SOCCP_BINARY)

    # DCP (buildroot = APPSVENDOR_BUILDROOT, aka DCP_BUILDROOT_Vendor)
    _add(bv.DCP_BINARY)

    # QECP
    _add(bv.QECP_BINARY)
    _add(bv.DCD_BINARY)

    # TFA
    _add(bv.TFA_BINARY)

    # TZ — TZ_DEVCFG_BINARY's buildroot is the unassigned &Devcfg_Binary_Buildroot
    # in the .cmm; harmless since only the basename is used (see module docstring)
    _add(bv.TZ_BINARY)
    _add(bv.TZ_QTI_CONFIG_BINARY)
    _add(bv.TZ_DEVCFG_BINARY)

    # HYP
    _add(bv.HYP_BINARY)
    _add(bv.HYP_DEVCFG_BINARY)

    # UEFI
    _add(bv.UEFI)
    _add(bv.UEFI_DTB)
    _add(bv.UEFI_IMAGEFV)
    _add(bv.UEFI_TOOLSFV)

    # TZ apps
    _add(bv.UEFISEC_BINARY)
    _add(bv.STORESEC_BINARY)
    _add(bv.KEYMASTER_BINARY)
    _add(bv.SECRETKEEPER)
    _add(bv.SPUSERVICE_BINARY)

    # QUANTUM — optional, FILE.EXIST guarded in the .cmm
    if bv.QUANTUMFV_BINARY and os.path.exists(bv.QUANTUMFV_BINARY):
        _add(bv.QUANTUMFV_BINARY)
    if bv.QUANTUMSDK_BINARY and os.path.exists(bv.QUANTUMSDK_BINARY):
        _add(bv.QUANTUMSDK_BINARY)

    # VPLAN
    _add(bv.VP_BINARY)
    _add(bv.VP_MINI_BINARY)

    # gbl.bin — optional, FILE.EXIST guarded in the .cmm
    if bv.ANDROID_ESP_BINARY and os.path.exists(bv.ANDROID_ESP_BINARY):
        _add(bv.ANDROID_ESP_BINARY)

    return ",".join(names)


def _maili_search_paths(bv: MailiBuildVars, xml_location: str) -> str:
    """
    Mirror .cmm SearchPaths construction for the binary flash loop
    (lines 384-417). Returns comma-separated directory paths, in .cmm order.

    KERNEL_ABL_BINARY/APPSKERNEL_BUILDROOT is included here even though it is
    never used in binary_list — matches the .cmm's vestigial reference
    (line 406) exactly.
    """
    def _d(path):
        return os.path.dirname(path) if path else ""

    dirs = [
        xml_location,
        _d(bv.MULTIIMGOEM_BINARY),
        _d(bv.MULTIIMGQTI_BINARY),
        _d(bv.SOC_VIEW_ELF),
        _d(bv.HYP_AC_CONFIG_MBN),
        _d(bv.QUP_BINARY),
        _d(bv.SHRM_BINARY),
        _d(bv.SHRM_BINARY_LP6),
        _d(bv.BOOT_BINARY),
        _d(bv.BOOT_CONFIG),
        _d(bv.AOP_BINARY),
        _d(bv.CPUCP_BINARY),
        _d(bv.PDP_BINARY),
        _d(bv.SOCCP_BINARY),
        _d(bv.DCP_BINARY),
        _d(bv.QECP_BINARY),
        _d(bv.TFA_BINARY),
        _d(bv.TZ_BINARY),
        _d(bv.HYP_BINARY),
        _d(bv.UEFI),
        _d(bv.UEFI_IMAGEFV),
        _d(bv.UEFI_TOOLSFV),
        _d(bv.APPSBOOT_BINARY),
        _d(bv.KERNEL_ABL_BINARY),
        _d(bv.ANDROID_ESP_BINARY),
        _d(bv.VP_BINARY),
        _d(bv.UEFISEC_BINARY),
    ]
    if bv.QUANTUMFV_BINARY and os.path.exists(bv.QUANTUMFV_BINARY):
        dirs.append(_d(bv.QUANTUMFV_BINARY))
    if bv.QUANTUMSDK_BINARY and os.path.exists(bv.QUANTUMSDK_BINARY):
        dirs.append(_d(bv.QUANTUMSDK_BINARY))

    # deduplicate while preserving order
    seen = set()
    result = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            result.append(d)
    return ",".join(result)


def _maili_loadimg_list(bv: MailiBuildVars, loadimages: str):
    """
    Mirror .cmm LOADIMG branch (lines 454-702).
    Returns (binary_list_str, binary_path_str) for the requested image group.

    Groups: ssg | acpolicy | util_fw | boot | cpucp | pdp | soccp | dcp |
            qecp | rpm | aop | tz | tfa | hyp | uefi | quantum | vplan |
            appsboot | core_non-hlos
    """
    names = []
    dirs  = []

    def _n(path): return os.path.basename(path) if path else None
    def _d(path): return os.path.dirname(path) if path else None

    if loadimages == "ssg":
        for p in [bv.SOC_VIEW_ELF, bv.TME_SEQ_PATCH_ELF]:
            if p: names.append(_n(p))
        dirs = [_d(bv.SOC_VIEW_ELF), _d(bv.TME_SEQ_PATCH_ELF)]

    elif loadimages == "acpolicy":
        for p in [bv.HYP_AC_CONFIG_MBN, bv.TZ_AC_CONFIG_MBN,
                  bv.XBL_AC_CONFIG_MBN, bv.TME_CONFIG_ELF]:
            if p: names.append(_n(p))
        dirs = [_d(bv.HYP_AC_CONFIG_MBN)]

    elif loadimages == "util_fw":
        for p in [bv.QUP_BINARY, bv.SHRM_BINARY, bv.SHRM_BINARY_LP6, bv.DCB_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.SHRM_BINARY), _d(bv.QUP_BINARY)]

    elif loadimages == "boot":
        for p in [bv.BOOT_BINARY, bv.BOOT_CONFIG, bv.XBLRAMDUMP_BINARY, bv.PMIC_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.BOOT_BINARY), _d(bv.BOOT_CONFIG)]

    elif loadimages == "cpucp":
        for p in [bv.CPUCP_BINARY, bv.CPUCP_DTB]:
            if p: names.append(_n(p))
        dirs = [_d(bv.CPUCP_BINARY), _d(bv.CPUCP_DTB)]

    elif loadimages == "pdp":
        for p in [bv.PDP_BINARY, bv.PDP_CDB_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.PDP_BINARY), _d(bv.PDP_CDB_BINARY)]

    elif loadimages == "soccp":
        if bv.SOCCP_BINARY: names.append(_n(bv.SOCCP_BINARY))
        dirs = [_d(bv.SOCCP_BINARY)]

    elif loadimages == "dcp":
        if bv.DCP_BINARY: names.append(_n(bv.DCP_BINARY))
        dirs = [_d(bv.DCP_BINARY)]

    elif loadimages == "qecp":
        for p in [bv.QECP_BINARY, bv.DCD_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.QECP_BINARY), _d(bv.DCD_BINARY)]

    elif loadimages in ("rpm", "aop"):
        for p in [bv.AOP_BINARY, bv.AOP_DEVCFG_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.AOP_BINARY)]

    elif loadimages == "tz":
        for p in [bv.TZ_BINARY, bv.TZ_QTI_CONFIG_BINARY, bv.TZ_DEVCFG_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.TZ_BINARY)]

    elif loadimages == "tfa":
        if bv.TFA_BINARY: names.append(_n(bv.TFA_BINARY))
        dirs = [_d(bv.TFA_BINARY)]

    elif loadimages == "hyp":
        for p in [bv.HYP_BINARY, bv.HYP_DEVCFG_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.HYP_BINARY)]

    elif loadimages == "uefi":
        for p in [bv.UEFI, bv.UEFI_DTB, bv.UEFI_IMAGEFV, bv.UEFI_TOOLSFV]:
            if p: names.append(_n(p))
        dirs = [_d(bv.UEFI), _d(bv.UEFI_TOOLSFV)]

    elif loadimages == "quantum":
        for p in [bv.QUANTUMFV_BINARY, bv.QUANTUMSDK_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.QUANTUMFV_BINARY), _d(bv.QUANTUMSDK_BINARY)]

    elif loadimages == "vplan":
        for p in [bv.VP_BINARY, bv.VP_MINI_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.VP_BINARY)]

    elif loadimages == "appsboot":
        # Ported literally from the .cmm (lines 664-678), including its
        # apparent bug: APPSBOOT_BINARY itself is never appended to
        # binary_list — only ANDROID_ESP_BINARY (gbl.bin), and only if it
        # exists. If ANDROID_ESP_BINARY is absent this produces an empty
        # binary_list/BinaryPath, matching the source .cmm's own behavior.
        if bv.META_VARIANT and "CORE" in bv.META_VARIANT:
            raise RuntimeError("Error - apps boot loading not available for CORE only metabuild")
        if bv.ANDROID_ESP_BINARY and os.path.exists(bv.ANDROID_ESP_BINARY):
            names.append(_n(bv.ANDROID_ESP_BINARY))
            dirs = [_d(bv.APPSBOOT_BINARY), _d(bv.ANDROID_ESP_BINARY)]

    elif loadimages == "core_non-hlos":
        if not bv.NON_HLOS_CORE_BINARY or not os.path.exists(bv.NON_HLOS_CORE_BINARY):
            raise FileNotFoundError(f"Core_NON-HLOS.bin not found: {bv.NON_HLOS_CORE_BINARY!r}")
        names.append(os.path.basename(bv.NON_HLOS_CORE_BINARY))
        dirs = [os.path.dirname(bv.NON_HLOS_CORE_BINARY)]

    else:
        raise ValueError(f"Unknown loadimages value: {loadimages!r}")

    seen = set()
    clean_dirs = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            clean_dirs.append(d)

    return ",".join(n for n in names if n), ",".join(clean_dirs)


# ---------------------------------------------------------------------------
# Phase 2 — Main load flow
# ---------------------------------------------------------------------------

def _xml_filenames(xml_path: str) -> set:
    """
    Return the set of non-empty filename="..." attributes in a rawprogram
    XML. Used to filter binary_list per-partition before calling LOAD, so
    jtagprogrammer.py's LOAD is never asked to find a file in a partition
    XML that doesn't list it — avoids the expected-but-noisy "file not found"
    print for every partition a given binary doesn't happen to live on.
    """
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(xml_path)
    except Exception:
        return set()
    return {p.get("filename", "") for p in tree.getroot().iter("program")
            if p.get("filename")}


def loadbuild_maili(debugger, loadoption: str, loadimages: str = "",
                    extraoptions: str = "") -> str:
    """
    Phase 2 — Maili load flow.
    Structural mirror of std_loadbuild_la.cmm's MAIN subroutine.
    Returns "SUCCESS" or raises on fatal error.
    """
    import subprocess

    meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo
    bv   = resolve_maili(meta)
    verify_maili_vars(bv, loadoption)

    jtp = _get_jtp()
    # JtagProgrammer.elf path resolution is delegated to the already-running
    # jtp instance (jtp.elf_path) rather than recomputed here — the .cmm's
    # own path is META_CHIPSET_DIR_N-relative, which has no direct contents.xml
    # equivalent, and jtagprogrammer.py's _resolve_elf_path() already handles
    # explicit elfpath= / contents.xml scan / fallback search paths.
    jtagprog_elf = jtp.elf_path

    storage_type, xml_location, xml_provision, max_partitions, _ = _detect_storage(debugger, bv)

    logger.info("Maili StorageType:  %s (hardcoded UFS)", storage_type)
    logger.info("jtagprogrammer_elf: %s", jtagprog_elf)
    logger.info("xml_location:       %s", xml_location)

    # ------------------------------------------------------------------
    # PROVISION
    # ------------------------------------------------------------------
    if "provision" in extraoptions:
        logger.info("*** PROVISION ***")
        prov_xml = os.path.join(xml_provision, "provision_ufs40.xml")
        if not os.path.exists(prov_xml):
            raise FileNotFoundError(f"Provision XML not found: {prov_xml}")
        jtp._dispatch("PROVISION",
                     arg_line=f"elfpath={jtagprog_elf} xml=provision_ufs40.xml searchpaths={xml_provision}")

    # ------------------------------------------------------------------
    # ERASE — Maili's .cmm calls ERASE with no "YES" token, but
    # jtagprogrammer.py's ERASE handler requires it (silently no-ops
    # otherwise) — add it explicitly, matching Kaana's precedent.
    # ------------------------------------------------------------------
    if loadoption in ("ERASEONLY", "LOADCOMMON", "LOADFULL"):
        logger.info("*** ERASE ***")
        jtp._dispatch("ERASE", arg_line=f"YES elfpath={jtagprog_elf}")

    # ------------------------------------------------------------------
    # LOADCOMMON / LOADFULL
    # ------------------------------------------------------------------
    if loadoption in ("LOADCOMMON", "LOADFULL"):

        binary_list  = _maili_binary_list(bv)
        search_paths = _maili_search_paths(bv, xml_location)

        # flash GPT partition table
        for partition in range(max_partitions):
            xml   = f"{xml_location}/rawprogram{partition}.xml"
            files = f"gpt_main{partition}.bin,gpt_backup{partition}.bin"
            logger.info("*** LOAD GPT %s ***", files)
            jtp._dispatch("LOAD",
                         arg_line=f"searchpaths={xml_location} xml={xml} files={files} elfpath={jtagprog_elf}")

        # flash binary images — filter binary_list per partition so LOAD is
        # only asked to find files that actually live in that partition's XML
        all_binaries = [f.strip() for f in binary_list.split(",") if f.strip()]
        seen_anywhere = set()
        for partition in range(max_partitions):
            xml = f"{xml_location}/rawprogram{partition}.xml"
            if not os.path.exists(xml):
                raise FileNotFoundError(f"rawprogram XML not found: {xml}")
            available = _xml_filenames(xml)
            files_here = [f for f in all_binaries if f in available]
            seen_anywhere.update(files_here)
            if not files_here:
                continue
            logger.info("*** LOAD binaries partition %d ***", partition)
            jtp._dispatch("LOAD",
                         arg_line=f"searchpaths={search_paths} xml={xml} files={','.join(files_here)} elfpath={jtagprog_elf}")

        for missing in all_binaries:
            if missing not in seen_anywhere:
                logger.error("Binary never found in any rawprogram*.xml: %s", missing)

        # apply disk patches
        for partition in range(max_partitions):
            xml = f"{xml_location}/patch{partition}.xml"
            if not os.path.exists(xml):
                raise FileNotFoundError(f"patch XML not found: {xml}")
            logger.info("*** PATCH partition %d ***", partition)
            jtp._dispatch("PATCH", arg_line=f"searchpaths={xml_location} xml={xml} elfpath={jtagprog_elf}")

        # set active boot partition (always UFS=1 on Maili)
        logger.info("*** SET ACTIVE BOOT PARTITION -> 1 ***")
        jtp._dispatch("9", arg_line=f"activeboot=1 elfpath={jtagprog_elf}")

    # ------------------------------------------------------------------
    # LOADIMG
    # ------------------------------------------------------------------
    if loadoption == "LOADIMG":
        binary_list, binary_path = _maili_loadimg_list(bv, loadimages)
        search_paths = f"{xml_location},{binary_path}"

        all_binaries = [f.strip() for f in binary_list.split(",") if f.strip()]
        seen_anywhere = set()
        for partition in range(max_partitions):
            xml = f"{xml_location}/rawprogram{partition}.xml"
            available = _xml_filenames(xml)
            files_here = [f for f in all_binaries if f in available]
            seen_anywhere.update(files_here)
            if not files_here:
                continue
            logger.info("*** LOAD %s partition %d ***", loadimages, partition)
            jtp._dispatch("LOAD",
                         arg_line=f"searchpaths={search_paths} xml={xml} files={','.join(files_here)} elfpath={jtagprog_elf}")

        for missing in all_binaries:
            if missing not in seen_anywhere:
                logger.error("Binary never found in any rawprogram*.xml: %s", missing)

        logger.info("*** SET ACTIVE BOOT PARTITION -> 1 ***")
        jtp._dispatch("9", arg_line=f"activeboot=1 elfpath={jtagprog_elf}")
        debugger.HandleCommand("breakpoint delete --force")

    # ------------------------------------------------------------------
    # LOADFULL — fastboot HLOS
    # ------------------------------------------------------------------
    if loadoption == "LOADFULL":
        logger.info("*** SET ACTIVE BOOT PARTITION -> 1 (pre-fastboot) ***")
        jtp._dispatch("9", arg_line=f"activeboot=1 elfpath={jtagprog_elf}")
        debugger.HandleCommand("breakpoint delete --force")

        fastboot_script = os.path.normpath(
            os.path.join(bv.METAROOTDIR, "common", "build", "fastboot_all.py"))
        python_exe = shutil.which("python") or shutil.which("python3") or "python"
        logger.info("*** LOAD HLOS via fastboot_all.py --ap=%s --pf=%s --st=ufs ***",
                    bv.APPSVENDOR_BUILDROOT, bv.PRODUCT_FLAVOR)
        subprocess.Popen(
            ["cmd", "/k", python_exe, fastboot_script,
             f"--ap={bv.APPSVENDOR_BUILDROOT}",
             f"--pf={bv.PRODUCT_FLAVOR}",
             "--st=ufs"],
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )

    logger.info("*** LOAD SUCCESS ***")
    return "SUCCESS"


# ---------------------------------------------------------------------------
# LLDB integration
# ---------------------------------------------------------------------------

def std_loadbuild_la(debugger, command, exe_ctx, result):
    """
    LLDB command:  std_loadbuild_la <LOADOPTION> [loadimages=<img>] [extraoptions=<opts>]

    Examples:
        std_loadbuild_la ERASEONLY
        std_loadbuild_la LOADCOMMON
        std_loadbuild_la LOADFULL
        std_loadbuild_la LOADCOMMON extraoptions=provision
        std_loadbuild_la LOADIMG loadimages=boot
        std_loadbuild_la LOADIMG loadimages=ssg
        std_loadbuild_la LOADIMG loadimages=acpolicy
        std_loadbuild_la LOADIMG loadimages=util_fw
        std_loadbuild_la LOADIMG loadimages=cpucp
        std_loadbuild_la LOADIMG loadimages=pdp
        std_loadbuild_la LOADIMG loadimages=soccp
        std_loadbuild_la LOADIMG loadimages=dcp
        std_loadbuild_la LOADIMG loadimages=qecp
        std_loadbuild_la LOADIMG loadimages=aop        (alias: rpm)
        std_loadbuild_la LOADIMG loadimages=tz
        std_loadbuild_la LOADIMG loadimages=tfa
        std_loadbuild_la LOADIMG loadimages=hyp
        std_loadbuild_la LOADIMG loadimages=uefi
        std_loadbuild_la LOADIMG loadimages=quantum
        std_loadbuild_la LOADIMG loadimages=vplan
        std_loadbuild_la LOADIMG loadimages=appsboot
        std_loadbuild_la LOADIMG loadimages=core_non-hlos
        std_loadbuild_la VERIFY
        std_loadbuild_la FASTBOOT
    """
    import shlex
    import traceback

    try:
        tokens = shlex.split(command.strip()) if command.strip() else []
        loadoption   = tokens[0].upper() if tokens else ""
        loadimages   = ""
        extraoptions = ""

        for tok in tokens[1:]:
            if tok.startswith("loadimages="):
                loadimages = tok.split("=", 1)[1]
            elif tok.startswith("extraoptions="):
                extraoptions = tok.split("=", 1)[1]

        if not loadoption:
            print("Usage: std_loadbuild_la ERASEONLY|LOADCOMMON|LOADFULL|LOADIMG|FASTBOOT|VERIFY")
            return

        if loadoption == "LOADIMG" and not loadimages:
            print("Usage: std_loadbuild_la LOADIMG loadimages=<group>")
            print()
            print("  Image groups:")
            print("    ssg           - signed_firmware_soc_view.elf, sequencer_ram.elf")
            print("    acpolicy      - hyp_ac_config.mbn, tz_ac_config.mbn, xbl_ac_config.mbn, tme_config.elf")
            print("    util_fw       - qupv3fw.elf, shrm_lp5.elf, [shrm_lp6.elf], dcb.bin")
            print("    boot          - xbl_s.melf, xbl_config.elf, XblRamdump.elf, [psi.bin]")
            print("    cpucp         - cpucp.elf, cpucp_dtbs.elf")
            print("    pdp           - pdp.elf, pdp_cdb.elf")
            print("    soccp         - soccp.bin")
            print("    dcp           - dcp.bin")
            print("    qecp          - qecp binary, dcd.mbn")
            print("    aop           - aop.mbn, aop_devcfg.mbn  (alias: rpm)")
            print("    tz            - tz.mbn, tz_qti_config.mbn, tz_oem_config.mbn")
            print("    tfa           - bl31.mbn")
            print("    hyp           - hypvm.mbn, hyp config file")
            print("    uefi          - uefi.elf, uefi_dtbs.elf, imagefv.elf, tools.fv")
            print("    quantum       - Quantum.fv, QuantumSDK binary")
            print("    vplan         - vplan.bin, vplan_mini.bin")
            print("    appsboot      - gbl.bin only (see module docstring re: .cmm bug)")
            print("    core_non-hlos - Core_NON-HLOS.bin")
            print()
            print("  Tip: run 'std_loadbuild_la VERIFY' first to confirm all paths resolve.")
            return

        if loadoption == "VERIFY":
            meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo
            bv   = resolve_maili(meta)
            print("\n=== MailiBuildVars ===")
            for name, value in vars(bv).items():
                print(f"  {name:<30} = {value}")
            print()
            return

        if loadoption == "FASTBOOT":
            meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo
            bv   = resolve_maili(meta)
            fastboot_script = os.path.normpath(
                os.path.join(bv.METAROOTDIR, "common", "build", "fastboot_all.py"))
            python_exe = shutil.which("python") or shutil.which("python3") or "python"
            logger.info("*** FASTBOOT: launching fastboot_all.py ***")
            import subprocess
            subprocess.Popen(
                ["cmd", "/k", python_exe, fastboot_script,
                 f"--ap={bv.APPSVENDOR_BUILDROOT}",
                 f"--pf={bv.PRODUCT_FLAVOR}",
                 "--st=ufs"],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            return

        res = loadbuild_maili(debugger, loadoption, loadimages, extraoptions)
        print(res)

    except Exception:
        msg = traceback.format_exc()
        logger.error("std_loadbuild_la failed:\n%s", msg)
        result.SetError(msg)
