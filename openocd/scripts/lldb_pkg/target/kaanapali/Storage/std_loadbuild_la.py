#
# Copyright (c) 2026 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file std_loadbuild_la.py
#
# @section Description
# Python port of loadbuild_kaana.cmm — Kaanapali-specific build loading.
# Drives JtagProgrammer to ERASE / LOADCOMMON / LOADFULL / LOADIMG onto UFS.
#
# Architecture:
#   Phase 1 — variable resolution  (resolve_kaana / verify_kaana_vars)
#   Phase 2 — load flow            (loadbuild_kaana)
#
# The Phase 2 flow reuses std_loadbuild_la.loadbuild() unchanged; only the
# binary-list helpers and variable resolution are Kaana-specific.
#
# Relationship to std_loadbuild_la.py:
#   - BuildVars extended to KaanaBuildVars (new buildroots + binaries)
#   - resolve_from_contents_xml extended to populate Kaana fields
#   - _hyp_buildroot / verify_vars / _detect_storage overridden for Kaana
#   - _build_binary_list / _build_search_paths / _build_loadimg_list replaced
#   - loadbuild() loop itself is IDENTICAL — not duplicated here

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
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
# Phase 1 — Kaana variable container
# ---------------------------------------------------------------------------

@dataclass
class KaanaBuildVars(BuildVars):
    """
    Extends BuildVars with all Kaanapali-specific buildroots and binaries.

    New buildroots (cmm_root_path_var in contents.xml):
        SSG_BUILDROOT       → TME.FW.* build
        ACP_BUILDROOT       → ACPOLICY.XF.* build
        PDP_BUILDROOT       → PDP.FW.* build
        SOCCP_BUILDROOT     → SOCCP.FW.* build
        DCP_BUILDROOT       → DCP.FW.* build
        APPSKERNEL_BUILDROOT→ KERNEL.PLATFORM.* build (alternate ABL)

    New binaries (cmm_file_var in contents.xml):
        SOC_VIEW_ELF        signed_firmware_soc_view.elf  (SSG_BUILDROOT)
        TME_SEQ_PATCH_ELF   sequencer_ram.elf             (SSG_BUILDROOT)
        HYP_AC_CONFIG_MBN   hyp_ac_config.mbn             (ACP_BUILDROOT)
        TZ_AC_CONFIG_MBN    tz_ac_config.mbn              (ACP_BUILDROOT)
        XBL_AC_CONFIG_MBN   xbl_ac_config.mbn             (ACP_BUILDROOT)
        TME_CONFIG_ELF      tme_config.elf                (ACP_BUILDROOT)
        PDP_BINARY          pdp.elf                       (PDP_BUILDROOT)
        PDP_CDB_BINARY      pdp_cdb.elf                   (PDP_BUILDROOT)
        SOCCP_BINARY        soccp.bin          optional   (SOCCP_BUILDROOT)
        SOCCP_DEBUG_BINARY  sdi.mbn                       (SOCCP_BUILDROOT)
        SOCCP_DCD_BINARY    dcd.mbn                       (SOCCP_BUILDROOT)
        DCP_BINARY          dcp.bin            optional   (DCP_BUILDROOT)
        CPUCP_DTB           cpucp_dtbs.elf                (CPUCP_BUILDROOT)
        MULTIIMGQTI_BINARY  multi_image_qti.mbn           (common)
        TZ_QTI_CONFIG       tz_qti_config.mbn  optional   (TZ_BUILDROOT)
        HYP_DEVCFG_BINARY   (dev builds only)  optional   (TZ_BUILDROOT)
        SPUSERVICE_BINARY   spu_service.mbn    optional   (TZAPPS_BUILDROOT)
        QUANTUMFV_BINARY    Quantum.fv                    (BOOT_BUILDROOT)

    Kaana also reads:
        TZ_BUILDID          from <short_build_path cmm_var="TZ_BUILDID">
        CHIPID              from <chipid cmm_var="CHIPID">

    Removed vs Clarence:
        CMNLIB_BINARY, CMNLIB64_BINARY — not present on Kaana
        CATEFV_BINARY                  — replaced by QUANTUMFV_BINARY
    """

    # --- Kaana-only buildroots ---
    SSG_BUILDROOT:          Optional[str] = None
    ACP_BUILDROOT:          Optional[str] = None
    PDP_BUILDROOT:          Optional[str] = None
    SOCCP_BUILDROOT:        Optional[str] = None
    DCP_BUILDROOT:          Optional[str] = None
    APPSKERNEL_BUILDROOT:   Optional[str] = None

    # --- Kaana-only binaries ---
    SOC_VIEW_ELF:           Optional[str] = None
    TME_SEQ_PATCH_ELF:      Optional[str] = None
    HYP_AC_CONFIG_MBN:      Optional[str] = None
    TZ_AC_CONFIG_MBN:       Optional[str] = None
    XBL_AC_CONFIG_MBN:      Optional[str] = None
    TME_CONFIG_ELF:         Optional[str] = None
    PDP_BINARY:             Optional[str] = None
    PDP_CDB_BINARY:         Optional[str] = None
    SOCCP_BINARY:           Optional[str] = None   # optional
    SOCCP_DEBUG_BINARY:     Optional[str] = None
    SOCCP_DCD_BINARY:       Optional[str] = None
    DCP_BINARY:             Optional[str] = None   # optional
    CPUCP_DTB:              Optional[str] = None
    MULTIIMGQTI_BINARY:     Optional[str] = None
    TZ_QTI_CONFIG:          Optional[str] = None   # optional
    TZ_QTI_CONFIG_BINARY:   Optional[str] = None   # optional fallback for TZ_QTI_CONFIG
    TZ_DEVCFG_BINARY:       Optional[str] = None   # optional fallback for TZDEVCFG_BINARY
    HYP_DEVCFG_BINARY:      Optional[str] = None   # optional, dev builds
    SPUSERVICE_BINARY:      Optional[str] = None   # optional
    QUANTUMFV_BINARY:       Optional[str] = None
    KERNEL_ABL_BINARY:      Optional[str] = None   # optional, alternate ABL fallback

    # --- meta identifiers ---
    TZ_BUILDID:             Optional[str] = None   # from short_build_path cmm_var="TZ_BUILDID"
    CHIPID:                 Optional[str] = None   # e.g. "KAANAPALI"


# ---------------------------------------------------------------------------
# Phase 1 — Resolution
# ---------------------------------------------------------------------------

def resolve_kaana(meta: metaInfo) -> KaanaBuildVars:
    """
    Resolve all KaanaBuildVars from contents.xml, then apply engineering
    build path overrides via metaInfo.getImageBasePath (same logic as LA).
    """
    import re
    import platform
    from xml.etree import ElementTree as ET

    contents_xml = str(g_dbgContext.contentsXmlPath)
    meta_root    = meta.getMetaPath()

    # Start from the base LA resolver to populate shared fields
    base: BuildVars = _la_resolve(contents_xml, meta_root)

    # Copy into KaanaBuildVars
    bv = KaanaBuildVars(**{k: v for k, v in vars(base).items()
                           if k in BuildVars.__dataclass_fields__})

    # ----------------------------------------------------------------
    # Re-parse contents.xml for Kaana-specific fields
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

    # CHIPID from <product_info>
    product_info = root.find("product_info")
    if product_info is not None:
        chipid_elem = product_info.find("chipid")
        if chipid_elem is not None:
            bv.CHIPID = (chipid_elem.text or "").strip()

    builds_flat = root.find("builds_flat")
    if builds_flat is None:
        raise RuntimeError("contents.xml has no <builds_flat> element")

    kaana_root_vars = {}
    kaana_file_vars = {}
    kaana_cmm_vars  = {}   # short_build_path cmm_var

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
            kaana_root_vars[var] = build_root

        # short_build_path cmm_var (TZ_BUILDID etc.)
        for sbp in build.findall("short_build_path"):
            cvar = sbp.get("cmm_var")
            if cvar:
                kaana_cmm_vars[cvar] = _strip_tokens(sbp.text or "").strip()

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
            kaana_file_vars[fvar] = _abs(full)

    # --- Kaana-only buildroots ---
    bv.SSG_BUILDROOT         = kaana_root_vars.get("SSG_BUILDROOT")
    bv.ACP_BUILDROOT         = kaana_root_vars.get("ACP_BUILDROOT")
    bv.PDP_BUILDROOT         = kaana_root_vars.get("PDP_BUILDROOT")
    bv.SOCCP_BUILDROOT       = kaana_root_vars.get("SOCCP_BUILDROOT")
    bv.DCP_BUILDROOT         = kaana_root_vars.get("DCP_BUILDROOT")
    bv.APPSKERNEL_BUILDROOT  = kaana_root_vars.get("APPSKERNEL_BUILDROOT")

    # --- Kaana-only binaries ---
    bv.SOC_VIEW_ELF          = kaana_file_vars.get("SOC_VIEW_ELF")
    bv.TME_SEQ_PATCH_ELF     = kaana_file_vars.get("TME_SEQ_PATCH_ELF")
    bv.HYP_AC_CONFIG_MBN     = kaana_file_vars.get("HYP_AC_CONFIG_MBN")
    bv.TZ_AC_CONFIG_MBN      = kaana_file_vars.get("TZ_AC_CONFIG_MBN")
    bv.XBL_AC_CONFIG_MBN     = kaana_file_vars.get("XBL_AC_CONFIG_MBN")
    bv.TME_CONFIG_ELF        = kaana_file_vars.get("TME_CONFIG_ELF")
    bv.PDP_BINARY            = kaana_file_vars.get("PDP_BINARY")
    bv.PDP_CDB_BINARY        = kaana_file_vars.get("PDP_CDB_BINARY")
    bv.SOCCP_BINARY          = kaana_file_vars.get("SOCCP_BINARY")
    bv.SOCCP_DEBUG_BINARY    = kaana_file_vars.get("SOCCP_DEBUG_BINARY")
    bv.SOCCP_DCD_BINARY      = kaana_file_vars.get("SOCCP_DCD_BINARY")
    bv.DCP_BINARY            = kaana_file_vars.get("DCP_BINARY")
    bv.CPUCP_DTB             = kaana_file_vars.get("CPUCP_DTB")
    bv.MULTIIMGQTI_BINARY    = kaana_file_vars.get("MULTIIMGQTI_BINARY")
    bv.TZ_QTI_CONFIG         = kaana_file_vars.get("TZ_QTI_CONFIG")
    bv.TZ_QTI_CONFIG_BINARY  = kaana_file_vars.get("TZ_QTI_CONFIG_BINARY")
    bv.TZ_DEVCFG_BINARY      = kaana_file_vars.get("TZ_DEVCFG_BINARY")
    bv.SPUSERVICE_BINARY     = kaana_file_vars.get("SPUSERVICE_BINARY")
    bv.QUANTUMFV_BINARY      = kaana_file_vars.get("QUANTUMFV_BINARY")

    # HYP_DEVCFG has no cmm_file_var in this contents.xml — dev-build only,
    # populated via engineering override if present
    bv.HYP_DEVCFG_BINARY     = kaana_file_vars.get("HYP_DEVCFG_BINARY")

    # TZ_BUILDID from short_build_path
    bv.TZ_BUILDID            = kaana_cmm_vars.get("TZ_BUILDID")

    # KERNEL_ABL_BINARY: no cmm_file_var in contents.xml; derived conventionally
    # as APPSKERNEL_BUILDROOT/out/target/product/<product>/abl.elf — left None,
    # engineering override via metaInfo.getImageBasePath("appskernel") if needed.

    # ----------------------------------------------------------------
    # Engineering build path overrides (same pattern as LA)
    # ----------------------------------------------------------------
    _kaana_buildroot_map = {
        "boot":       ("BOOT_BUILDROOT",  [
            "BOOT_BINARY", "BOOT_CONFIG", "BOOT_UEFI", "DDRFW_BINARY",
            "XBLRAMDUMP_BINARY", "QUANTUMFV_BINARY", "IMAGEFV_BINARY", "TOOLSFV_BINARY",
        ]),
        "tz":         ("TZ_BUILDROOT",    [
            "TZ_BINARY", "HYP_BINARY", "TZDEVCFG_BINARY", "TZ_QTI_CONFIG",
            "HYP_DEVCFG_BINARY", "KEYMASTER_BINARY", "UEFISEC_BINARY",
            "STORESEC_BINARY", "SPUSERVICE_BINARY",
        ]),
        "aop":        ("AOP_BUILDROOT",   ["AOP_BINARY", "AOP_DEVCFG_BINARY"]),
        "tme":        ("SSG_BUILDROOT",   ["SOC_VIEW_ELF", "TME_SEQ_PATCH_ELF"]),
        "acp":        ("ACP_BUILDROOT",   [
            "HYP_AC_CONFIG_MBN", "TZ_AC_CONFIG_MBN", "XBL_AC_CONFIG_MBN", "TME_CONFIG_ELF",
        ]),
        "pdp":        ("PDP_BUILDROOT",   ["PDP_BINARY", "PDP_CDB_BINARY"]),
        "soccp":      ("SOCCP_BUILDROOT", ["SOCCP_BINARY", "SOCCP_DEBUG_BINARY", "SOCCP_DCD_BINARY"]),
        "dcp":        ("DCP_BUILDROOT",   ["DCP_BINARY"]),
        "cpucp":      ("CPUCP_BUILDROOT", ["CPUCP_BINARY", "CPUCP_DTB"]),
    }

    for image_name, (buildroot_attr, binary_attrs) in _kaana_buildroot_map.items():
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
        logger.info("Kaana engineering override: %s → %s", image_name, eng_path_norm)
        setattr(bv, buildroot_attr, eng_path_norm)
        for attr in binary_attrs:
            old_val = getattr(bv, attr, None)
            if old_val and old_val.replace("\\", "/").startswith(xml_buildroot_norm):
                new_val = eng_path_norm + old_val.replace("\\", "/")[len(xml_buildroot_norm):]
                setattr(bv, attr, new_val)

    return bv


def resolve_from_meta(meta: metaInfo) -> KaanaBuildVars:
    """Generic-name alias for resolve_kaana() — cdt_program.py's _std_loadbuild_module()
    dynamically imports this Storage/<Chip>/std_loadbuild_la module and calls
    resolve_from_meta() by that fixed name regardless of chipset."""
    return resolve_kaana(meta)


# ---------------------------------------------------------------------------
# Phase 1 — Verification
# ---------------------------------------------------------------------------

def verify_kaana_vars(bv: KaanaBuildVars, loadoption: str) -> None:
    """
    Mirror CMM FILEXIST FATALEXITUSER block for LOADCOMMON.
    Raises FileNotFoundError on the first missing required path.
    """
    def _require(path: Optional[str], label: str):
        if not path or not os.path.exists(path):
            raise FileNotFoundError(
                f"Required binary not found: {label} -> {path!r}"
            )

    _require(bv.BOOT_BINARY, "BOOT_BINARY")

    if loadoption in ("LOADCOMMON", "LOADFULL"):
        _require(bv.QUP_BINARY,           "QUP_BINARY")
        _require(bv.MULTIIMGOEM_BINARY,   "MULTIIMGOEM_BINARY")
        _require(bv.MULTIIMGQTI_BINARY,   "MULTIIMGQTI_BINARY")
        _require(bv.SOC_VIEW_ELF,         "SOC_VIEW_ELF")
        _require(bv.TME_SEQ_PATCH_ELF,    "TME_SEQ_PATCH_ELF")
        _require(bv.HYP_AC_CONFIG_MBN,    "HYP_AC_CONFIG_MBN")
        _require(bv.TZ_AC_CONFIG_MBN,     "TZ_AC_CONFIG_MBN")
        _require(bv.XBL_AC_CONFIG_MBN,    "XBL_AC_CONFIG_MBN")
        _require(bv.TME_CONFIG_ELF,       "TME_CONFIG_ELF")
        _require(bv.BOOT_BINARY,          "BOOT_BINARY")
        _require(bv.BOOT_CONFIG,          "BOOT_CONFIG")
        _require(bv.BOOT_UEFI,            "BOOT_UEFI")
        _require(bv.DDRFW_BINARY,         "DDRFW_BINARY")
        _require(bv.XBLRAMDUMP_BINARY,    "XBLRAMDUMP_BINARY")
        _require(bv.CPUCP_BINARY,         "CPUCP_BINARY")
        _require(bv.CPUCP_DTB,            "CPUCP_DTB")
        _require(bv.PDP_BINARY,           "PDP_BINARY")
        _require(bv.PDP_CDB_BINARY,       "PDP_CDB_BINARY")
        _require(bv.SOCCP_DEBUG_BINARY,   "SOCCP_DEBUG_BINARY")
        _require(bv.SOCCP_DCD_BINARY,     "SOCCP_DCD_BINARY")
        _require(bv.AOP_BINARY,           "AOP_BINARY")
        _require(bv.AOP_DEVCFG_BINARY,    "AOP_DEVCFG_BINARY")
        _require(bv.TZ_BINARY,            "TZ_BINARY")
        _require(bv.HYP_BINARY,           "HYP_BINARY")
        if bv.TZ_BUILDID == "KAANAPALIDEV":
            _require(bv.HYP_DEVCFG_BINARY, "HYP_DEVCFG_BINARY")
        _require(bv.KEYMASTER_BINARY,     "KEYMASTER_BINARY")
        _require(bv.UEFISEC_BINARY,       "UEFISEC_BINARY")

        if bv.META_VARIANT and "CORE" not in bv.META_VARIANT:
            if not (bv.APPSBOOT_BINARY and os.path.exists(bv.APPSBOOT_BINARY)) and \
               not (bv.KERNEL_ABL_BINARY and os.path.exists(bv.KERNEL_ABL_BINARY)):
                raise FileNotFoundError("No APPS ABL file found (APPSBOOT_BINARY or KERNEL_ABL_BINARY)")
        else:
            _require(bv.QUANTUMFV_BINARY, "QUANTUMFV_BINARY")
            _require(bv.TOOLSFV_BINARY,   "TOOLSFV_BINARY")

    logger.info("KaanaBuildVars verification passed for loadoption=%s", loadoption)


# ---------------------------------------------------------------------------
# Phase 2 helpers — mirror CMM binary list construction
# ---------------------------------------------------------------------------

def _kaana_hyp_buildroot(bv: KaanaBuildVars) -> str:
    """
    Mirror CMM:
        IF TZ_BUILDID == "KAANAPALIDEV" → HYP_BUILDROOT
        ELSE                            → TZ_BUILDROOT
    This contents.xml has no separate HYP_BUILDROOT; both paths resolve to
    TZ_BUILDROOT.  For a KAANAPALIDEV build the engineering override would
    have set a different root, so we just return TZ_BUILDROOT in all cases.
    """
    return bv.TZ_BUILDROOT


def _kaana_detect_storage(bv: KaanaBuildVars):
    """
    Kaana is hardcoded UFS — no runtime BOOT_CONFIG detection needed.
    Returns (storage_type, xml_location, xml_provision, max_partitions).
    """
    xml_location  = bv.PARTITION_XML_ROOT
    xml_provision = os.path.join(bv.METAROOTDIR, "config", "ufs", "provision") \
                    if bv.METAROOTDIR else ""
    return "ufs", xml_location, xml_provision, 6


def _detect_storage(debugger, bv: KaanaBuildVars):
    """Generic-name alias for _kaana_detect_storage() — cdt_program.py's
    _std_loadbuild_module() calls _detect_storage(debugger, bv) by that fixed
    name regardless of chipset, and unpacks a 5-tuple with a trailing
    programmer path (unused by cdt_program, Kaana has no equivalent)."""
    storage_type, xml_location, xml_provision, max_partitions = _kaana_detect_storage(bv)
    return storage_type, xml_location, xml_provision, max_partitions, None


def _kaana_binary_list(bv: KaanaBuildVars, hyp_buildroot: str) -> str:
    """
    Mirror CMM LOADCOMMON binary_list construction (loadbuild_kaana.cmm lines 191-339).
    Returns comma-separated bare filenames.
    """
    names = []

    def _add(path: Optional[str]):
        if path:
            names.append(os.path.basename(path))

    def _add_if_exists(path: Optional[str]):
        if path and os.path.exists(path):
            names.append(os.path.basename(path))

    # meta binaries
    _add(bv.QUP_BINARY)
    _add(bv.MULTIIMGOEM_BINARY)
    _add(bv.MULTIIMGQTI_BINARY)

    # TME / ACP binaries
    _add(bv.SOC_VIEW_ELF)
    _add(bv.TME_SEQ_PATCH_ELF)
    _add(bv.HYP_AC_CONFIG_MBN)
    _add(bv.TZ_AC_CONFIG_MBN)
    _add(bv.XBL_AC_CONFIG_MBN)
    _add(bv.TME_CONFIG_ELF)

    # boot binaries
    _add(bv.BOOT_BINARY)
    _add(bv.BOOT_CONFIG)
    _add(bv.BOOT_UEFI)
    _add(bv.DDRFW_BINARY)
    _add(bv.XBLRAMDUMP_BINARY)

    # AOP
    _add(bv.AOP_BINARY)
    _add(bv.AOP_DEVCFG_BINARY)

    # CPUCP
    _add(bv.CPUCP_BINARY)
    _add(bv.CPUCP_DTB)

    # PDP
    _add(bv.PDP_BINARY)
    _add(bv.PDP_CDB_BINARY)

    # SOCCP — SOCCP_BINARY is optional (FILE.EXIST guarded in CMM)
    _add_if_exists(bv.SOCCP_BINARY)
    _add(bv.SOCCP_DEBUG_BINARY)
    _add(bv.SOCCP_DCD_BINARY)

    # DCP — optional
    _add_if_exists(bv.DCP_BINARY)

    # TZ
    _add(bv.TZ_BINARY)
    # TZ_QTI_CONFIG — try mbn then binary fallback
    if bv.TZ_QTI_CONFIG and os.path.exists(bv.TZ_QTI_CONFIG):
        _add(bv.TZ_QTI_CONFIG)
    elif bv.TZ_QTI_CONFIG_BINARY and os.path.exists(bv.TZ_QTI_CONFIG_BINARY):
        _add(bv.TZ_QTI_CONFIG_BINARY)

    # devcfg — optional fallback
    if bv.TZDEVCFG_BINARY and os.path.exists(bv.TZDEVCFG_BINARY):
        _add(bv.TZDEVCFG_BINARY)
    elif bv.TZ_DEVCFG_BINARY and os.path.exists(bv.TZ_DEVCFG_BINARY):
        _add(bv.TZ_DEVCFG_BINARY)

    # HYP
    if bv.HYP_BINARY and os.path.exists(bv.HYP_BINARY):
        _add(bv.HYP_BINARY)
    _add_if_exists(bv.HYP_DEVCFG_BINARY)

    # TZ apps
    _add(bv.KEYMASTER_BINARY)
    _add(bv.UEFISEC_BINARY)
    _add_if_exists(bv.STORESEC_BINARY)
    _add_if_exists(bv.SPUSERVICE_BINARY)

    # appsboot — prefer APPSBOOT_BINARY, fallback to KERNEL_ABL_BINARY
    if bv.META_VARIANT and "CORE" not in bv.META_VARIANT:
        if bv.APPSBOOT_BINARY and os.path.exists(bv.APPSBOOT_BINARY):
            _add(bv.APPSBOOT_BINARY)
        elif bv.KERNEL_ABL_BINARY and os.path.exists(bv.KERNEL_ABL_BINARY):
            _add(bv.KERNEL_ABL_BINARY)
    else:
        _add(bv.QUANTUMFV_BINARY)
        _add(bv.TOOLSFV_BINARY)

    return ",".join(names)


def _kaana_search_paths(bv: KaanaBuildVars, xml_location: str) -> str:
    """
    Mirror CMM SearchPaths construction for binary flash loop
    (loadbuild_kaana.cmm lines 357-376).
    Returns comma-separated directory paths.
    """
    dirs = [
        xml_location,
        os.path.dirname(bv.MULTIIMGOEM_BINARY)   if bv.MULTIIMGOEM_BINARY   else "",
        os.path.dirname(bv.MULTIIMGQTI_BINARY)   if bv.MULTIIMGQTI_BINARY   else "",
        os.path.dirname(bv.QUP_BINARY)           if bv.QUP_BINARY           else "",
        os.path.dirname(bv.SOC_VIEW_ELF)         if bv.SOC_VIEW_ELF         else "",
        os.path.dirname(bv.HYP_AC_CONFIG_MBN)   if bv.HYP_AC_CONFIG_MBN    else "",
        os.path.dirname(bv.BOOT_BINARY)          if bv.BOOT_BINARY          else "",
        os.path.dirname(bv.CPUCP_BINARY)         if bv.CPUCP_BINARY         else "",
        os.path.dirname(bv.CPUCP_DTB)            if bv.CPUCP_DTB            else "",
        os.path.dirname(bv.PDP_BINARY)           if bv.PDP_BINARY           else "",
        os.path.dirname(bv.PDP_CDB_BINARY)       if bv.PDP_CDB_BINARY       else "",
        os.path.dirname(bv.SOCCP_BINARY)         if bv.SOCCP_BINARY         else "",
        os.path.dirname(bv.SOCCP_DEBUG_BINARY)   if bv.SOCCP_DEBUG_BINARY   else "",
        os.path.dirname(bv.DCP_BINARY)           if bv.DCP_BINARY           else "",
        os.path.dirname(bv.AOP_BINARY)           if bv.AOP_BINARY           else "",
        os.path.dirname(bv.AOP_DEVCFG_BINARY)   if bv.AOP_DEVCFG_BINARY    else "",
        os.path.dirname(bv.TZ_BINARY)            if bv.TZ_BINARY            else "",
        os.path.dirname(bv.HYP_BINARY)           if bv.HYP_BINARY           else "",
        os.path.dirname(bv.KEYMASTER_BINARY)     if bv.KEYMASTER_BINARY     else "",
        os.path.dirname(bv.APPSBOOT_BINARY)      if bv.APPSBOOT_BINARY      else "",
        os.path.dirname(bv.KERNEL_ABL_BINARY)    if bv.KERNEL_ABL_BINARY    else "",
    ]
    # deduplicate while preserving order
    seen = set()
    result = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            result.append(d)
    return ",".join(result)


def _kaana_loadimg_list(bv: KaanaBuildVars, loadimages: str):
    """
    Mirror CMM LOADIMG branch (loadbuild_kaana.cmm lines 405-648).
    Returns (binary_list_str, binary_path_str).

    Groups: boot | ssg | acpolicy | cpucp | pdp | soccp | dcp |
            rpm | aop | tz | appsboot | qup/multioem | core_non-hlos | uefifv
    """
    names = []
    dirs  = []

    def _n(path): return os.path.basename(path) if path else None
    def _d(path): return os.path.dirname(path)  if path else None

    if loadimages == "boot":
        for p in [bv.BOOT_BINARY, bv.BOOT_CONFIG, bv.BOOT_UEFI,
                  bv.DDRFW_BINARY, bv.XBLRAMDUMP_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.BOOT_BINARY)]

    elif loadimages == "ssg":
        for p in [bv.SOC_VIEW_ELF, bv.TME_SEQ_PATCH_ELF]:
            if p: names.append(_n(p))
        dirs = [_d(bv.SOC_VIEW_ELF), _d(bv.TME_SEQ_PATCH_ELF)]

    elif loadimages == "acpolicy":
        for p in [bv.HYP_AC_CONFIG_MBN, bv.TZ_AC_CONFIG_MBN,
                  bv.XBL_AC_CONFIG_MBN, bv.TME_CONFIG_ELF]:
            if p: names.append(_n(p))
        dirs = [_d(bv.HYP_AC_CONFIG_MBN)]

    elif loadimages == "cpucp":
        for p in [bv.CPUCP_BINARY, bv.CPUCP_DTB]:
            if p: names.append(_n(p))
        dirs = [_d(bv.CPUCP_BINARY), _d(bv.CPUCP_DTB)]

    elif loadimages == "pdp":
        for p in [bv.PDP_BINARY, bv.PDP_CDB_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.PDP_BINARY), _d(bv.PDP_CDB_BINARY)]

    elif loadimages == "soccp":
        for p in [bv.SOCCP_DEBUG_BINARY, bv.SOCCP_DCD_BINARY]:
            if p: names.append(_n(p))
        if bv.SOCCP_BINARY and os.path.exists(bv.SOCCP_BINARY):
            names.append(_n(bv.SOCCP_BINARY))
        dirs = [_d(bv.SOCCP_DEBUG_BINARY)]

    elif loadimages == "dcp":
        if bv.DCP_BINARY: names.append(_n(bv.DCP_BINARY))
        dirs = [_d(bv.DCP_BINARY)]

    elif loadimages in ("rpm", "aop"):
        for p in [bv.AOP_BINARY, bv.AOP_DEVCFG_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.AOP_BINARY)]

    elif loadimages == "tz":
        names.append(_n(bv.TZ_BINARY))
        if bv.TZ_QTI_CONFIG and os.path.exists(bv.TZ_QTI_CONFIG):
            names.append(_n(bv.TZ_QTI_CONFIG))
        elif bv.TZ_QTI_CONFIG_BINARY and os.path.exists(bv.TZ_QTI_CONFIG_BINARY):
            names.append(_n(bv.TZ_QTI_CONFIG_BINARY))
        if bv.TZDEVCFG_BINARY and os.path.exists(bv.TZDEVCFG_BINARY):
            names.append(_n(bv.TZDEVCFG_BINARY))
        elif bv.TZ_DEVCFG_BINARY and os.path.exists(bv.TZ_DEVCFG_BINARY):
            names.append(_n(bv.TZ_DEVCFG_BINARY))
        if bv.HYP_BINARY and os.path.exists(bv.HYP_BINARY):
            names.append(_n(bv.HYP_BINARY))
        if bv.HYP_DEVCFG_BINARY and os.path.exists(bv.HYP_DEVCFG_BINARY):
            names.append(_n(bv.HYP_DEVCFG_BINARY))
        names.append(_n(bv.UEFISEC_BINARY))
        names.append(_n(bv.KEYMASTER_BINARY))
        if bv.STORESEC_BINARY and os.path.exists(bv.STORESEC_BINARY):
            names.append(_n(bv.STORESEC_BINARY))
        if bv.SPUSERVICE_BINARY and os.path.exists(bv.SPUSERVICE_BINARY):
            names.append(_n(bv.SPUSERVICE_BINARY))
        dirs = [
            _d(bv.HYP_BINARY),
            _d(bv.TZ_BINARY),
            _d(bv.KEYMASTER_BINARY),
        ]

    elif loadimages == "appsboot":
        if bv.META_VARIANT and "CORE" in bv.META_VARIANT:
            raise RuntimeError("Error - apps boot loading not available for CORE only metabuild")
        if bv.APPSBOOT_BINARY and os.path.exists(bv.APPSBOOT_BINARY):
            names.append(_n(bv.APPSBOOT_BINARY))
            dirs = [_d(bv.APPSBOOT_BINARY)]
        elif bv.KERNEL_ABL_BINARY and os.path.exists(bv.KERNEL_ABL_BINARY):
            names.append(_n(bv.KERNEL_ABL_BINARY))
            dirs = [_d(bv.KERNEL_ABL_BINARY)]
        else:
            raise FileNotFoundError("No APPS ABL file!")

    elif loadimages == "qup/multioem":
        for p in [bv.QUP_BINARY, bv.MULTIIMGOEM_BINARY, bv.MULTIIMGQTI_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.QUP_BINARY), _d(bv.MULTIIMGOEM_BINARY), _d(bv.MULTIIMGQTI_BINARY)]

    elif loadimages == "core_non-hlos":
        # NON_HLOS_CORE_BINARY lives under METAROOTDIR/common/build/ufs/bin/asic/
        core_nhlos = os.path.join(bv.METAROOTDIR, "common", "build", "ufs",
                                  "bin", "asic", "Core_NON-HLOS.bin")
        if not os.path.exists(core_nhlos):
            raise FileNotFoundError(f"Core_NON-HLOS.bin not found: {core_nhlos}")
        names.append(os.path.basename(core_nhlos))
        dirs = [os.path.dirname(core_nhlos)]

    elif loadimages == "uefifv":
        for p in [bv.QUANTUMFV_BINARY, bv.IMAGEFV_BINARY, bv.TOOLSFV_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.QUANTUMFV_BINARY), _d(bv.IMAGEFV_BINARY), _d(bv.TOOLSFV_BINARY)]

    else:
        raise ValueError(f"Unknown loadimages value: {loadimages!r}")

    # deduplicate dirs, drop None/empty
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

def loadbuild_kaana(debugger, loadoption: str, loadimages: str = "",
                    extraoptions: str = "") -> str:
    """
    Phase 2 — Kaanapali load flow.
    Structural mirror of loadbuild_kaana.cmm MAIN subroutine.
    Returns "SUCCESS" or raises on fatal error.
    """
    import subprocess
    import time
    from datetime import datetime

    meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo
    bv   = resolve_kaana(meta)
    verify_kaana_vars(bv, loadoption)

    jtp = _get_jtp()
    jtagprog_elf = _jtagprog_elf_path(bv)
    hyp_buildroot = _kaana_hyp_buildroot(bv)

    storage_type, xml_location, xml_provision, max_partitions = _kaana_detect_storage(bv)

    _load_start_time = time.time()
    _log_file = os.path.join(os.path.expanduser("~"), ".claude", "loadbuild_logs",
                             f"loadbuild_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    os.makedirs(os.path.dirname(_log_file), exist_ok=True)

    logger.info("Kaana StorageType:   %s (hardcoded UFS)", storage_type)
    logger.info("jtagprogrammer_elf:  %s", jtagprog_elf)
    logger.info("xml_location:        %s", xml_location)
    logger.info("CHIPID:              %s", bv.CHIPID)
    logger.info("TZ_BUILDID:          %s", bv.TZ_BUILDID)
    logger.info("Log file:            %s", _log_file)

    # ------------------------------------------------------------------
    # PROVISION
    # ------------------------------------------------------------------
    if "provision" in extraoptions:
        logger.info("*** PROVISION ***")
        prov_xml = os.path.join(xml_provision, "provision_ufs40.xml")
        if not os.path.exists(prov_xml):
            raise FileNotFoundError(f"Provision XML not found: {prov_xml}")
        jtp._dispatch("PROVISION", auto_command=True,
                      arg_line=f"elfpath={jtagprog_elf} xml=provision_ufs40.xml searchpaths={xml_provision}")

    # ------------------------------------------------------------------
    # ERASE
    # ------------------------------------------------------------------
    if loadoption in ("ERASEONLY", "LOADCOMMON", "LOADFULL"):
        logger.info("*** ERASE ***")
        jtp._dispatch("ERASE", auto_command=True, arg_line=f"elfpath={jtagprog_elf}")

    # ------------------------------------------------------------------
    # LOADCOMMON / LOADFULL
    # ------------------------------------------------------------------
    if loadoption in ("LOADCOMMON", "LOADFULL"):

        binary_list  = _kaana_binary_list(bv, hyp_buildroot)
        search_paths = _kaana_search_paths(bv, xml_location)

        # flash GPT partition table
        for partition in range(max_partitions):
            xml   = f"{xml_location}/rawprogram{partition}.xml"
            files = f"gpt_main{partition}.bin,gpt_backup{partition}.bin"
            logger.info("*** LOAD GPT %s ***", files)
            jtp._dispatch("LOAD", auto_command=True,
                          arg_line=f"searchpaths={xml_location} xml={xml} files={files} elfpath={jtagprog_elf} _load_start_time={_load_start_time} _log_file={_log_file}")

        # flash binary images
        for partition in range(max_partitions):
            xml = f"{xml_location}/rawprogram{partition}.xml"
            if not os.path.exists(xml):
                raise FileNotFoundError(f"rawprogram XML not found: {xml}")
            logger.info("*** LOAD binaries partition %d ***", partition)
            jtp._dispatch("LOAD", auto_command=True,
                          arg_line=f"searchpaths={search_paths} xml={xml} files={binary_list} elfpath={jtagprog_elf} _load_start_time={_load_start_time} _log_file={_log_file}")

        # apply disk patches
        for partition in range(max_partitions):
            xml = f"{xml_location}/patch{partition}.xml"
            if not os.path.exists(xml):
                raise FileNotFoundError(f"patch XML not found: {xml}")
            logger.info("*** PATCH partition %d ***", partition)
            jtp._dispatch("PATCH", auto_command=True,
                          arg_line=f"searchpaths={xml_location} xml={xml} elfpath={jtagprog_elf} _load_start_time={_load_start_time} _log_file={_log_file}")

        # set active boot (always UFS=1 on Kaana)
        logger.info("*** SET ACTIVE BOOT PARTITION → 1 ***")
        jtp._dispatch("9", auto_command=True, arg_line=f"activeboot=1 elfpath={jtagprog_elf} _load_start_time={_load_start_time} _log_file={_log_file}")

    # ------------------------------------------------------------------
    # LOADIMG
    # ------------------------------------------------------------------
    if loadoption == "LOADIMG":
        binary_list, binary_path = _kaana_loadimg_list(bv, loadimages)
        search_paths = f"{xml_location},{binary_path}"

        for partition in range(max_partitions):
            xml = f"{xml_location}/rawprogram{partition}.xml"
            logger.info("*** LOAD %s partition %d ***", loadimages, partition)
            jtp._dispatch("LOAD", auto_command=True,
                          arg_line=f"searchpaths={search_paths} xml={xml} files={binary_list} elfpath={jtagprog_elf}")

        logger.info("*** SET ACTIVE BOOT PARTITION → 1 ***")
        jtp._dispatch("9", auto_command=True, arg_line=f"activeboot=1 elfpath={jtagprog_elf}")
        debugger.HandleCommand("breakpoint delete --force")

    # ------------------------------------------------------------------
    # LOADFULL — fastboot HLOS
    # ------------------------------------------------------------------
    if loadoption == "LOADFULL":
        logger.info("*** SET ACTIVE BOOT PARTITION → 1 (pre-fastboot) ***")
        jtp._dispatch("9", auto_command=True, arg_line=f"activeboot=1 elfpath={jtagprog_elf}")
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
        std_loadbuild_la LOADIMG loadimages=tz
        std_loadbuild_la LOADIMG loadimages=ssg
        std_loadbuild_la LOADIMG loadimages=acpolicy
        std_loadbuild_la LOADIMG loadimages=pdp
        std_loadbuild_la LOADIMG loadimages=soccp
        std_loadbuild_la LOADIMG loadimages=dcp
        std_loadbuild_la LOADIMG loadimages=cpucp
        std_loadbuild_la LOADIMG loadimages=aop
        std_loadbuild_la LOADIMG loadimages=tz
        std_loadbuild_la LOADIMG loadimages=appsboot
        std_loadbuild_la LOADIMG loadimages=qup/multioem
        std_loadbuild_la LOADIMG loadimages=uefifv
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
            print("    boot          — xbl_sc.elf, xbl_config.elf, uefi.elf, shrm.elf, XblRamdump.elf")
            print("    ssg           — signed_firmware_soc_view.elf, sequencer_ram.elf")
            print("    acpolicy      — hyp_ac_config.mbn, tz_ac_config.mbn, xbl_ac_config.mbn, tme_config.elf")
            print("    cpucp         — cpucp.elf, cpucp_dtbs.elf")
            print("    pdp           — pdp.elf, pdp_cdb.elf")
            print("    soccp         — sdi.mbn, dcd.mbn [soccp.bin]")
            print("    dcp           — dcp.bin")
            print("    aop           — aop.mbn, aop_devcfg.mbn  (alias: rpm)")
            print("    tz            — tz.mbn, hyp*.mbn, keymint.mbn, uefi_sec.mbn, ...")
            print("    appsboot      — abl.elf (or kernel abl fallback)")
            print("    qup/multioem  — qupv3fw.elf, multi_image.mbn, multi_image_qti.mbn")
            print("    uefifv        — Quantum.fv, imagefv.elf, tools.fv")
            print("    core_non-hlos — Core_NON-HLOS.bin")
            return

        if loadoption == "VERIFY":
            meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo
            bv   = resolve_kaana(meta)
            print("\n=== KaanaBuildVars ===")
            for name, value in vars(bv).items():
                print(f"  {name:<30} = {value}")
            print()
            return

        if loadoption == "FASTBOOT":
            meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo
            bv   = resolve_kaana(meta)
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

        res = loadbuild_kaana(debugger, loadoption, loadimages, extraoptions)
        print(res)

    except Exception:
        msg = traceback.format_exc()
        logger.error("std_loadbuild_la failed:\n%s", msg)
        result.SetError(msg)
