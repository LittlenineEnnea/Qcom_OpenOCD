#
# Copyright (c) 2026 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file std_loadbuild_la.py
#
# @section Description
# Python port of loadbuild_mavros.cmm — Mavros-specific build loading.
# Drives JtagProgrammer to ERASE / LOADCOMMON / LOADFULL / LOADIMG onto UFS.
#
# Architecture:
#   Phase 1 — variable resolution  (resolve_mavros / verify_mavros_vars)
#   Phase 2 — load flow            (loadbuild_mavros)
#
# The Phase 2 flow reuses std_loadbuild_la.loadbuild() unchanged; only the
# binary-list helpers and variable resolution are Mavros-specific.
#
# Relationship to std_loadbuild_la.py:
#   - BuildVars extended to MavrosBuildVars (new buildroots + binaries)
#   - resolve_from_contents_xml extended to populate Mavros fields
#   - _hyp_buildroot / verify_vars / _detect_storage overridden for Mavros
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
# Phase 1 — Mavros variable container
# ---------------------------------------------------------------------------

@dataclass
class MavrosBuildVars(BuildVars):
    """
    Extends BuildVars with all Mavros-specific buildroots and binaries.

    New buildroots (cmm_root_path_var in contents.xml):
        SSG_BUILDROOT       → TME.FW.* build
        UTIL_FW_BUILDROOT   → UTIL.FW.* build
        SOCCP_BUILDROOT     → SOCCP.FW.* build
        CPUCP_BUILDROOT     → CPUCP.FW.* build
        ADSP_BUILDROOT      → LPAIDSP.HT.* build
        CDSP_BUILDROOT      → CDSP.HT.* build
        MPSS_BUILDROOT      → MPSS.CYSP.* build
        FASTRPC_BUILDROOT   → FASTRPC.LNX.* build
        QUANTUM_BUILDROOT   → BOOT.QUANTUM.* build
        UEFI_BUILDROOT      → BOOT.OSSUEFI.* build
        TFA_BUILDROOT       → TFA.XF.* build
        APPSKERNEL_BUILDROOT→ KERNEL.PLATFORM.* build (alternate ABL)
        VPLAN_BUILDROOT     → VPLAN.* build
        ACP_BUILDROOT       → ACPOLICY.XF.* build
        PDP_BUILDROOT       → PDP.FW.* build
        QECP_BUILDROOT      → QECP.FW.* build

    New binaries (cmm_file_var in contents.xml):
        SOC_VIEW_ELF        signed_firmware_soc_view.elf  (SSG_BUILDROOT)
        TME_SEQ_PATCH_ELF   sequencer_ram.elf             (SSG_BUILDROOT)
        QUP_BINARY          qupv3fw.elf                   (UTIL_FW_BUILDROOT)
        SHRM_BINARY         shrm.elf                      (UTIL_FW_BUILDROOT)
        SHRM_BINARY_LP6     shrm_lp6.elf                  (UTIL_FW_BUILDROOT)
        DCB_BINARY          dcb.bin                       (UTIL_FW_BUILDROOT)
        PMIC_BINARY         pmic.elf                      (BOOT_BUILDROOT)
        SOCCP_BINARY        soccp.bin                     (SOCCP_BUILDROOT)
        CPUCP_BINARY        cpucp.elf                     (CPUCP_BUILDROOT)
        CPUCP_DTB           cpucp_dtbs.elf                (CPUCP_BUILDROOT)
        ADSP_BINARY         adsp.mbn                      (ADSP_BUILDROOT)
        ADSP_DTB            adsp_dtbs.elf                 (ADSP_BUILDROOT)
        CDSP_BINARY         cdsp.mbn                      (CDSP_BUILDROOT)
        CDSP_DTB            cdsp_dtbs.elf                 (CDSP_BUILDROOT)
        MPSS_BINARY         qdsp6sw.mbn                   (MPSS_BUILDROOT)
        MPSS_DTB            qdsp6sw_dtbs.elf              (MPSS_BUILDROOT)
        QUANTUMFV_BINARY    Quantum.elf                   (QUANTUM_BUILDROOT)
        QUANTUMSDK_BINARY   quantumsdk.elf                (QUANTUM_BUILDROOT)
        UEFI                uefi.elf                      (UEFI_BUILDROOT)
        UEFI_DTB            uefi_dtbs.elf                 (UEFI_BUILDROOT)
        UEFI_IMAGEFV        imagefv.elf                   (UEFI_BUILDROOT)
        UEFI_TOOLSFV        tools.fv                      (UEFI_BUILDROOT)
        TFA_BINARY          bl31.mbn                      (TFA_BUILDROOT)
        TZ_QTI_CONFIG_BINARY tz_qti_config.mbn            (TZ_BUILDROOT)
        TZ_DEVCFG_BINARY    tz_oem_config.mbn             (TZ_BUILDROOT)
        HYP_DEVCFG_BINARY   hyp_devcfg.mbn                (TZ_BUILDROOT)
        SPUSERVICE_BINARY   spu_service.mbn               (TZAPPS_BUILDROOT)
        STORESEC_BINARY     storsec.mbn                   (TZAPPS_BUILDROOT)
        SECRETKEEPER        secretkeeper.mbn              (TZAPPS_BUILDROOT)
        ANDROID_ESP_BINARY  android_esp.bin               (APPSVENDOR_BUILDROOT)
        KERNEL_ABL_BINARY   abl.elf                       (APPSKERNEL_BUILDROOT)
        VP_BINARY           vplan binary                  (VPLAN_BUILDROOT)
        MULTIIMGOEM_BINARY  multi_image_oem.elf           (METAROOTDIR)
        MULTIIMGQTI_BINARY  multi_image_qti.mbn           (METAROOTDIR)
        NON_HLOS_BINARY     NON-HLOS.bin                  (METAROOTDIR)
        NON_HLOS_CORE_BINARY Core_NON-HLOS.bin            (METAROOTDIR)
        HYP_AC_CONFIG_MBN   hyp_ac_config.mbn             (ACP_BUILDROOT)
        TZ_AC_CONFIG_MBN    tz_ac_config.mbn              (ACP_BUILDROOT)
        XBL_AC_CONFIG_MBN   xbl_ac_config.mbn             (ACP_BUILDROOT)
        TME_CONFIG_ELF      tme_config.elf                (ACP_BUILDROOT)
        PDP_BINARY          pdp.elf                       (PDP_BUILDROOT)
        PDP_CDB_BINARY      pdp_cdb.elf                   (PDP_BUILDROOT)
        QECP_BINARY         qecp.bin                      (QECP_BUILDROOT)
        DCD_BINARY          dcd.mbn                       (QECP_BUILDROOT)
        DCP_BINARY          dcp.bin                       (APPSVENDOR_BUILDROOT)

    Mavros also reads:
        TZ_BUILDID          from <short_build_path cmm_var="TZ_BUILDID">
        CHIPID              from <chipid cmm_var="CHIPID">
    """

    # --- Mavros-specific buildroots ---
    SSG_BUILDROOT:          Optional[str] = None
    UTIL_FW_BUILDROOT:      Optional[str] = None
    SOCCP_BUILDROOT:        Optional[str] = None
    CPUCP_BUILDROOT:        Optional[str] = None
    ADSP_BUILDROOT:         Optional[str] = None
    CDSP_BUILDROOT:         Optional[str] = None
    MPSS_BUILDROOT:         Optional[str] = None
    FASTRPC_BUILDROOT:      Optional[str] = None
    QUANTUM_BUILDROOT:      Optional[str] = None
    UEFI_BUILDROOT:         Optional[str] = None
    TFA_BUILDROOT:          Optional[str] = None
    APPSKERNEL_BUILDROOT:   Optional[str] = None
    VPLAN_BUILDROOT:        Optional[str] = None
    ACP_BUILDROOT:          Optional[str] = None
    PDP_BUILDROOT:          Optional[str] = None
    QECP_BUILDROOT:         Optional[str] = None

    # --- Mavros-specific binaries ---
    SOC_VIEW_ELF:           Optional[str] = None
    TME_SEQ_PATCH_ELF:      Optional[str] = None
    QUP_BINARY:             Optional[str] = None
    SHRM_BINARY:            Optional[str] = None
    SHRM_BINARY_LP6:        Optional[str] = None
    DCB_BINARY:             Optional[str] = None
    PMIC_BINARY:            Optional[str] = None
    SOCCP_BINARY:           Optional[str] = None
    CPUCP_BINARY:           Optional[str] = None
    CPUCP_DTB:              Optional[str] = None
    ADSP_BINARY:            Optional[str] = None
    ADSP_DTB:               Optional[str] = None
    CDSP_BINARY:            Optional[str] = None
    CDSP_DTB:               Optional[str] = None
    MPSS_BINARY:            Optional[str] = None
    MPSS_DTB:               Optional[str] = None
    QUANTUMFV_BINARY:       Optional[str] = None
    QUANTUMSDK_BINARY:      Optional[str] = None
    UEFI:                   Optional[str] = None
    UEFI_DTB:               Optional[str] = None
    UEFI_IMAGEFV:           Optional[str] = None
    UEFI_TOOLSFV:           Optional[str] = None
    TFA_BINARY:             Optional[str] = None
    TZ_QTI_CONFIG_BINARY:   Optional[str] = None
    TZ_DEVCFG_BINARY:       Optional[str] = None
    HYP_DEVCFG_BINARY:      Optional[str] = None
    SPUSERVICE_BINARY:      Optional[str] = None
    STORESEC_BINARY:        Optional[str] = None
    SECRETKEEPER:           Optional[str] = None
    ANDROID_ESP_BINARY:     Optional[str] = None
    KERNEL_ABL_BINARY:      Optional[str] = None
    VP_BINARY:              Optional[str] = None
    MULTIIMGOEM_BINARY:     Optional[str] = None
    MULTIIMGQTI_BINARY:     Optional[str] = None
    NON_HLOS_BINARY:        Optional[str] = None
    NON_HLOS_CORE_BINARY:   Optional[str] = None
    HYP_AC_CONFIG_MBN:      Optional[str] = None
    TZ_AC_CONFIG_MBN:       Optional[str] = None
    XBL_AC_CONFIG_MBN:      Optional[str] = None
    TME_CONFIG_ELF:         Optional[str] = None
    PDP_BINARY:             Optional[str] = None
    PDP_CDB_BINARY:         Optional[str] = None
    QECP_BINARY:            Optional[str] = None
    DCD_BINARY:             Optional[str] = None
    DCP_BINARY:             Optional[str] = None

    # --- meta identifiers ---
    TZ_BUILDID:             Optional[str] = None
    CHIPID:                 Optional[str] = None


# ---------------------------------------------------------------------------
# Phase 1 — Resolution
# ---------------------------------------------------------------------------

def resolve_mavros(meta: metaInfo) -> MavrosBuildVars:
    """
    Resolve all MavrosBuildVars from contents.xml, then apply engineering
    build path overrides via metaInfo.getImageBasePath (same logic as LA).
    """
    import re
    import platform
    from xml.etree import ElementTree as ET

    contents_xml = str(g_dbgContext.contentsXmlPath)
    meta_root    = meta.getMetaPath()

    # Start from the base LA resolver to populate shared fields
    base: BuildVars = _la_resolve(contents_xml, meta_root)

    # Copy into MavrosBuildVars
    bv = MavrosBuildVars(**{k: v for k, v in vars(base).items()
                            if k in BuildVars.__dataclass_fields__})

    # ----------------------------------------------------------------
    # Re-parse contents.xml for Mavros-specific fields
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
        """Join path parts with "/" separator, skipping empty components."""
        result = ""
        for p in parts:
            p = p.strip("/") if result else p.rstrip("/")  # preserve leading // (UNC) on first part
            if not p:
                continue  # Skip empty components
            if not result:
                result = p
            else:
                result = result + "/" + p
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

    mavros_root_vars = {}
    mavros_file_vars = {}
    mavros_cmm_vars  = {}   # short_build_path cmm_var

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
            mavros_root_vars[var] = build_root

        # short_build_path cmm_var (TZ_BUILDID etc.)
        for sbp in build.findall("short_build_path"):
            cvar = sbp.get("cmm_var")
            if cvar:
                mavros_cmm_vars[cvar] = _strip_tokens(sbp.text or "").strip()

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
            mavros_file_vars[fvar] = _abs(full)

    # --- Mavros-specific buildroots ---
    bv.SSG_BUILDROOT         = mavros_root_vars.get("SSG_BUILDROOT")
    bv.UTIL_FW_BUILDROOT     = mavros_root_vars.get("UTIL_FW_BUILDROOT")
    bv.SOCCP_BUILDROOT       = mavros_root_vars.get("SOCCP_BUILDROOT")
    bv.CPUCP_BUILDROOT       = mavros_root_vars.get("CPUCP_BUILDROOT")
    bv.ADSP_BUILDROOT        = mavros_root_vars.get("ADSP_BUILDROOT")
    bv.CDSP_BUILDROOT        = mavros_root_vars.get("CDSP_BUILDROOT")
    bv.MPSS_BUILDROOT        = mavros_root_vars.get("MPSS_BUILDROOT")
    bv.FASTRPC_BUILDROOT     = mavros_root_vars.get("FASTRPC_BUILDROOT")
    bv.QUANTUM_BUILDROOT     = mavros_root_vars.get("QUANTUM_BUILDROOT")
    bv.UEFI_BUILDROOT        = mavros_root_vars.get("UEFI_BUILDROOT")
    bv.TFA_BUILDROOT         = mavros_root_vars.get("TFA_BUILDROOT")
    bv.APPSKERNEL_BUILDROOT  = mavros_root_vars.get("APPSKERNEL_BUILDROOT")
    bv.VPLAN_BUILDROOT       = mavros_root_vars.get("VPLAN_BUILDROOT")
    bv.ACP_BUILDROOT         = mavros_root_vars.get("ACP_BUILDROOT")
    bv.PDP_BUILDROOT         = mavros_root_vars.get("PDP_BUILDROOT")
    bv.QECP_BUILDROOT        = mavros_root_vars.get("QECP_BUILDROOT")

    # --- Mavros-specific binaries ---
    bv.SOC_VIEW_ELF          = mavros_file_vars.get("SOC_VIEW_ELF")
    bv.TME_SEQ_PATCH_ELF     = mavros_file_vars.get("TME_SEQ_PATCH_ELF")
    bv.QUP_BINARY            = mavros_file_vars.get("QUP_BINARY")
    bv.SHRM_BINARY           = mavros_file_vars.get("SHRM_BINARY")
    bv.SHRM_BINARY_LP6       = mavros_file_vars.get("SHRM_BINARY_LP6")
    bv.DCB_BINARY            = mavros_file_vars.get("DCB_BINARY")
    bv.PMIC_BINARY           = mavros_file_vars.get("PMIC_BINARY")
    bv.SOCCP_BINARY          = mavros_file_vars.get("SOCCP_BINARY")
    bv.CPUCP_BINARY          = mavros_file_vars.get("CPUCP_BINARY")
    bv.CPUCP_DTB             = mavros_file_vars.get("CPUCP_DTB")
    bv.ADSP_BINARY           = mavros_file_vars.get("ADSP_BINARY")
    bv.ADSP_DTB              = mavros_file_vars.get("ADSP_DTB")
    bv.CDSP_BINARY           = mavros_file_vars.get("CDSP_BINARY")
    bv.CDSP_DTB              = mavros_file_vars.get("CDSP_DTB")
    bv.MPSS_BINARY           = mavros_file_vars.get("MPSS_BINARY")
    bv.MPSS_DTB              = mavros_file_vars.get("MPSS_DTB")
    bv.QUANTUMFV_BINARY      = mavros_file_vars.get("QUANTUMFV_BINARY")
    bv.QUANTUMSDK_BINARY     = mavros_file_vars.get("QUANTUMSDK_BINARY")
    bv.UEFI                  = mavros_file_vars.get("UEFI")
    bv.UEFI_DTB              = mavros_file_vars.get("UEFI_DTB")
    bv.UEFI_IMAGEFV          = mavros_file_vars.get("UEFI_IMAGEFV")
    bv.UEFI_TOOLSFV          = mavros_file_vars.get("UEFI_TOOLSFV")
    bv.TFA_BINARY            = mavros_file_vars.get("TFA_BINARY")
    bv.TZ_QTI_CONFIG_BINARY  = mavros_file_vars.get("TZ_QTI_CONFIG_BINARY")
    bv.TZ_DEVCFG_BINARY      = mavros_file_vars.get("TZ_DEVCFG_BINARY")
    bv.HYP_DEVCFG_BINARY     = mavros_file_vars.get("HYP_DEVCFG_BINARY")
    bv.SPUSERVICE_BINARY     = mavros_file_vars.get("SPUSERVICE_BINARY")
    bv.STORESEC_BINARY       = mavros_file_vars.get("STORESEC_BINARY")
    bv.SECRETKEEPER          = mavros_file_vars.get("SECRETKEEPER")
    bv.ANDROID_ESP_BINARY    = mavros_file_vars.get("ANDROID_ESP_BINARY")
    bv.VP_BINARY             = mavros_file_vars.get("VP_BINARY")
    bv.MULTIIMGOEM_BINARY    = mavros_file_vars.get("MULTIIMGOEM_BINARY")
    bv.MULTIIMGQTI_BINARY    = mavros_file_vars.get("MULTIIMGQTI_BINARY")
    bv.NON_HLOS_BINARY       = mavros_file_vars.get("NON_HLOS_BINARY")
    bv.NON_HLOS_CORE_BINARY  = mavros_file_vars.get("NON_HLOS_CORE_BINARY")
    bv.HYP_AC_CONFIG_MBN     = mavros_file_vars.get("HYP_AC_CONFIG_MBN")
    bv.TZ_AC_CONFIG_MBN      = mavros_file_vars.get("TZ_AC_CONFIG_MBN")
    bv.XBL_AC_CONFIG_MBN     = mavros_file_vars.get("XBL_AC_CONFIG_MBN")
    bv.TME_CONFIG_ELF        = mavros_file_vars.get("TME_CONFIG_ELF")
    bv.PDP_BINARY            = mavros_file_vars.get("PDP_BINARY")
    bv.PDP_CDB_BINARY        = mavros_file_vars.get("PDP_CDB_BINARY")
    bv.QECP_BINARY           = mavros_file_vars.get("QECP_BINARY")
    bv.DCD_BINARY            = mavros_file_vars.get("DCD_BINARY")
    bv.DCP_BINARY            = mavros_file_vars.get("DCP_BINARY")

    # TZ_BUILDID from short_build_path
    bv.TZ_BUILDID            = mavros_cmm_vars.get("TZ_BUILDID")

    # KERNEL_ABL_BINARY: derive conventionally from APPSKERNEL_BUILDROOT
    # if not found in contents.xml
    if bv.APPSKERNEL_BUILDROOT and not bv.KERNEL_ABL_BINARY:
        conventional_path = os.path.join(bv.APPSKERNEL_BUILDROOT,
                                        "out/target/product",
                                        bv.PRODUCT_FLAVOR or "generic",
                                        "abl.elf")
        if os.path.exists(conventional_path):
            bv.KERNEL_ABL_BINARY = conventional_path
            logger.info(f"KERNEL_ABL_BINARY derived: {conventional_path}")

    # ----------------------------------------------------------------
    # Engineering build path overrides (same pattern as LA)
    # ----------------------------------------------------------------
    _mavros_buildroot_map = {
        "boot":       ("BOOT_BUILDROOT",  [
            "BOOT_BINARY", "BOOT_CONFIG", "XBLRAMDUMP_BINARY", "PMIC_BINARY",
        ]),
        "tz":         ("TZ_BUILDROOT",    [
            "TZ_BINARY", "HYP_BINARY", "TZ_QTI_CONFIG_BINARY", "TZ_DEVCFG_BINARY",
            "HYP_DEVCFG_BINARY", "KEYMASTER_BINARY", "UEFISEC_BINARY",
            "STORESEC_BINARY", "SPUSERVICE_BINARY",
        ]),
        "aop":        ("AOP_BUILDROOT",   ["AOP_BINARY", "AOP_DEVCFG_BINARY"]),
        "tme":        ("SSG_BUILDROOT",   ["SOC_VIEW_ELF", "TME_SEQ_PATCH_ELF"]),
        "util_fw":    ("UTIL_FW_BUILDROOT", ["QUP_BINARY", "SHRM_BINARY", "SHRM_BINARY_LP6", "DCB_BINARY"]),
        "soccp":      ("SOCCP_BUILDROOT", ["SOCCP_BINARY"]),
        "cpucp":      ("CPUCP_BUILDROOT", ["CPUCP_BINARY", "CPUCP_DTB"]),
        "adsp":       ("ADSP_BUILDROOT",  ["ADSP_BINARY", "ADSP_DTB"]),
        "cdsp":       ("CDSP_BUILDROOT",  ["CDSP_BINARY", "CDSP_DTB"]),
        "mpss":       ("MPSS_BUILDROOT",  ["MPSS_BINARY", "MPSS_DTB"]),
        "quantum":    ("QUANTUM_BUILDROOT", ["QUANTUMFV_BINARY", "QUANTUMSDK_BINARY"]),
        "uefi":       ("UEFI_BUILDROOT",  ["UEFI", "UEFI_DTB", "UEFI_IMAGEFV", "UEFI_TOOLSFV"]),
        "tfa":        ("TFA_BUILDROOT",   ["TFA_BINARY"]),
        "acp":        ("ACP_BUILDROOT",   ["HYP_AC_CONFIG_MBN", "TZ_AC_CONFIG_MBN", "XBL_AC_CONFIG_MBN", "TME_CONFIG_ELF"]),
        "pdp":        ("PDP_BUILDROOT",   ["PDP_BINARY", "PDP_CDB_BINARY"]),
        "qecp":       ("QECP_BUILDROOT",  ["QECP_BINARY", "DCD_BINARY"]),
        "vplan":      ("VPLAN_BUILDROOT", ["VP_BINARY"]),
    }

    for image_name, (buildroot_attr, binary_attrs) in _mavros_buildroot_map.items():
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
        logger.info("Mavros engineering override: %s → %s", image_name, eng_path_norm)
        setattr(bv, buildroot_attr, eng_path_norm)
        for attr in binary_attrs:
            old_val = getattr(bv, attr, None)
            if old_val and old_val.replace("\\", "/").startswith(xml_buildroot_norm):
                new_val = eng_path_norm + old_val.replace("\\", "/")[len(xml_buildroot_norm):]
                setattr(bv, attr, new_val)

    return bv


def resolve_from_meta(meta: metaInfo) -> MavrosBuildVars:
    """Generic-name alias for resolve_mavros() — cdt_program.py's _std_loadbuild_module()
    dynamically imports this Storage/<Chip>/std_loadbuild_la module and calls
    resolve_from_meta() by that fixed name regardless of chipset."""
    return resolve_mavros(meta)


# ---------------------------------------------------------------------------
# Phase 1 — Verification
# ---------------------------------------------------------------------------

def verify_mavros_vars(bv: MavrosBuildVars, loadoption: str) -> None:
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
        # Meta binaries
        # MULTIIMGOEM_BINARY not required: absent from contents.xml on QTI-only/no-OEM meta variants
        _require(bv.MULTIIMGQTI_BINARY,   "MULTIIMGQTI_BINARY")

        # TME (SSG)
        _require(bv.SOC_VIEW_ELF,         "SOC_VIEW_ELF")
        _require(bv.TME_SEQ_PATCH_ELF,    "TME_SEQ_PATCH_ELF")

        # ACP (ACPolicy)
        _require(bv.HYP_AC_CONFIG_MBN,    "HYP_AC_CONFIG_MBN")
        _require(bv.TZ_AC_CONFIG_MBN,     "TZ_AC_CONFIG_MBN")
        _require(bv.XBL_AC_CONFIG_MBN,    "XBL_AC_CONFIG_MBN")
        _require(bv.TME_CONFIG_ELF,       "TME_CONFIG_ELF")

        # UTIL_FW
        _require(bv.QUP_BINARY,           "QUP_BINARY")
        _require(bv.SHRM_BINARY,          "SHRM_BINARY")
        _require(bv.DCB_BINARY,           "DCB_BINARY")

        # XBL (BOOT)
        _require(bv.BOOT_BINARY,          "BOOT_BINARY")
        _require(bv.BOOT_CONFIG,          "BOOT_CONFIG")
        _require(bv.XBLRAMDUMP_BINARY,    "XBLRAMDUMP_BINARY")

        # AOP
        _require(bv.AOP_BINARY,           "AOP_BINARY")
        _require(bv.AOP_DEVCFG_BINARY,    "AOP_DEVCFG_BINARY")

        # CPUCP
        _require(bv.CPUCP_BINARY,         "CPUCP_BINARY")
        _require(bv.CPUCP_DTB,            "CPUCP_DTB")

        # PDP not required: absent from contents.xml on meta variants without a PDP image

        # SOCCP
        _require(bv.SOCCP_BINARY,         "SOCCP_BINARY")

        # DCP
        _require(bv.DCP_BINARY,           "DCP_BINARY")

        # QECP not required: absent from contents.xml on meta variants without a QECP image

        # TZ
        _require(bv.TZ_BINARY,            "TZ_BINARY")
        _require(bv.TZ_QTI_CONFIG_BINARY, "TZ_QTI_CONFIG_BINARY")
        _require(bv.TZ_DEVCFG_BINARY,     "TZ_DEVCFG_BINARY")

        # TFA
        _require(bv.TFA_BINARY,           "TFA_BINARY")

        # HYP
        _require(bv.HYP_BINARY,           "HYP_BINARY")
        _require(bv.HYP_DEVCFG_BINARY,    "HYP_DEVCFG_BINARY")

        # UEFI
        _require(bv.UEFI,                 "UEFI")
        _require(bv.UEFI_DTB,             "UEFI_DTB")
        _require(bv.UEFI_IMAGEFV,         "UEFI_IMAGEFV")
        _require(bv.UEFI_TOOLSFV,         "UEFI_TOOLSFV")

        # TZ-apps
        _require(bv.UEFISEC_BINARY,       "UEFISEC_BINARY")
        _require(bv.STORESEC_BINARY,      "STORESEC_BINARY")
        _require(bv.KEYMASTER_BINARY,     "KEYMASTER_BINARY")
        _require(bv.SPUSERVICE_BINARY,    "SPUSERVICE_BINARY")

        # Quantum
        _require(bv.QUANTUMFV_BINARY,     "QUANTUMFV_BINARY")
        _require(bv.QUANTUMSDK_BINARY,    "QUANTUMSDK_BINARY")

        # VPLAN not required: absent from contents.xml on meta variants without a vplan image

        if bv.META_VARIANT and "CORE" not in bv.META_VARIANT:
            if not (bv.ANDROID_ESP_BINARY and os.path.exists(bv.ANDROID_ESP_BINARY)) and \
               not (bv.APPSBOOT_BINARY and os.path.exists(bv.APPSBOOT_BINARY)) and \
               not (bv.KERNEL_ABL_BINARY and os.path.exists(bv.KERNEL_ABL_BINARY)):
                raise FileNotFoundError("No APPS ABL file found (APPSBOOT_BINARY, KERNEL_ABL_BINARY, or ANDROID_ESP_BINARY)")

    logger.info("MavrosBuildVars verification passed for loadoption=%s", loadoption)


# ---------------------------------------------------------------------------
# Phase 2 helpers — mirror CMM binary list construction
# ---------------------------------------------------------------------------

def _mavros_hyp_buildroot(bv: MavrosBuildVars) -> str:
    """
    Mirror CMM: return TZ_BUILDROOT (HYP lives under TZ on Mavros).
    """
    return bv.TZ_BUILDROOT


def _mavros_detect_storage(bv: MavrosBuildVars):
    """
    Mavros is hardcoded UFS — no runtime BOOT_CONFIG detection needed.
    Returns (storage_type, xml_location, xml_provision, max_partitions).
    """
    xml_location  = bv.PARTITION_XML_ROOT
    xml_provision = os.path.join(bv.METAROOTDIR, "config", "ufs", "provision") \
                    if bv.METAROOTDIR else ""
    return "ufs", xml_location, xml_provision, 6


def _detect_storage(debugger, bv: MavrosBuildVars):
    """Generic-name alias for _mavros_detect_storage() — cdt_program.py's
    _std_loadbuild_module() calls _detect_storage(debugger, bv) by that fixed
    name regardless of chipset, and unpacks a 5-tuple with a trailing
    programmer path (unused by cdt_program, Mavros has no equivalent)."""
    storage_type, xml_location, xml_provision, max_partitions = _mavros_detect_storage(bv)
    return storage_type, xml_location, xml_provision, max_partitions, None


def _mavros_binary_list(bv: MavrosBuildVars, hyp_buildroot: str) -> str:
    """
    Mirror CMM LOADCOMMON binary_list construction (loadbuild_mavros.cmm).
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
    _add(bv.MULTIIMGOEM_BINARY)
    _add(bv.MULTIIMGQTI_BINARY)

    # TME / SSG binaries
    _add(bv.SOC_VIEW_ELF)
    _add(bv.TME_SEQ_PATCH_ELF)

    # ACP binaries
    _add(bv.HYP_AC_CONFIG_MBN)
    _add(bv.TZ_AC_CONFIG_MBN)
    _add(bv.XBL_AC_CONFIG_MBN)
    _add(bv.TME_CONFIG_ELF)

    # UTIL_FW binaries
    _add(bv.QUP_BINARY)
    _add(bv.SHRM_BINARY)
    _add_if_exists(bv.SHRM_BINARY_LP6)
    _add(bv.DCB_BINARY)

    # boot binaries
    _add(bv.BOOT_BINARY)
    _add(bv.BOOT_CONFIG)
    _add(bv.XBLRAMDUMP_BINARY)
    _add_if_exists(bv.PMIC_BINARY)

    # AOP
    _add(bv.AOP_BINARY)
    _add(bv.AOP_DEVCFG_BINARY)

    # CPUCP
    _add(bv.CPUCP_BINARY)
    _add(bv.CPUCP_DTB)

    # PDP
    _add(bv.PDP_BINARY)
    _add(bv.PDP_CDB_BINARY)

    # SOCCP — optional
    _add_if_exists(bv.SOCCP_BINARY)

    # DCP
    _add(bv.DCP_BINARY)

    # QECP
    _add(bv.QECP_BINARY)
    _add(bv.DCD_BINARY)

    # TZ
    _add(bv.TZ_BINARY)
    _add(bv.TZ_QTI_CONFIG_BINARY)
    _add(bv.TZ_DEVCFG_BINARY)

    # TFA
    _add(bv.TFA_BINARY)

    # HYP
    _add(bv.HYP_BINARY)
    _add_if_exists(bv.HYP_DEVCFG_BINARY)

    # TZ apps
    _add(bv.KEYMASTER_BINARY)
    _add(bv.UEFISEC_BINARY)
    _add_if_exists(bv.STORESEC_BINARY)
    _add_if_exists(bv.SECRETKEEPER)
    _add_if_exists(bv.SPUSERVICE_BINARY)

    # UEFI
    _add(bv.UEFI)
    _add(bv.UEFI_DTB)
    _add(bv.UEFI_IMAGEFV)
    _add(bv.UEFI_TOOLSFV)

    # Quantum
    _add_if_exists(bv.QUANTUMFV_BINARY)
    _add_if_exists(bv.QUANTUMSDK_BINARY)

    # VPLAN
    _add_if_exists(bv.VP_BINARY)

    # Android ESP
    _add_if_exists(bv.ANDROID_ESP_BINARY)

    return ",".join(names)


def _mavros_search_paths(bv: MavrosBuildVars, xml_location: str) -> str:
    """
    Mirror CMM SearchPaths construction for binary flash loop
    (loadbuild_mavros.cmm).
    Returns comma-separated directory paths.
    """
    dirs = [
        xml_location,
        os.path.dirname(bv.MULTIIMGOEM_BINARY)   if bv.MULTIIMGOEM_BINARY   else "",
        os.path.dirname(bv.MULTIIMGQTI_BINARY)   if bv.MULTIIMGQTI_BINARY   else "",
        os.path.dirname(bv.SOC_VIEW_ELF)         if bv.SOC_VIEW_ELF         else "",
        os.path.dirname(bv.HYP_AC_CONFIG_MBN)    if bv.HYP_AC_CONFIG_MBN    else "",
        os.path.dirname(bv.QUP_BINARY)           if bv.QUP_BINARY           else "",
        os.path.dirname(bv.SHRM_BINARY)          if bv.SHRM_BINARY          else "",
        os.path.dirname(bv.BOOT_BINARY)          if bv.BOOT_BINARY          else "",
        os.path.dirname(bv.AOP_BINARY)           if bv.AOP_BINARY           else "",
        os.path.dirname(bv.CPUCP_BINARY)         if bv.CPUCP_BINARY         else "",
        os.path.dirname(bv.CPUCP_DTB)            if bv.CPUCP_DTB            else "",
        os.path.dirname(bv.PDP_BINARY)           if bv.PDP_BINARY           else "",
        os.path.dirname(bv.SOCCP_BINARY)         if bv.SOCCP_BINARY         else "",
        os.path.dirname(bv.DCP_BINARY)           if bv.DCP_BINARY           else "",
        os.path.dirname(bv.QECP_BINARY)          if bv.QECP_BINARY          else "",
        os.path.dirname(bv.TZ_BINARY)            if bv.TZ_BINARY            else "",
        os.path.dirname(bv.HYP_BINARY)           if bv.HYP_BINARY           else "",
        os.path.dirname(bv.TFA_BINARY)           if bv.TFA_BINARY           else "",
        os.path.dirname(bv.UEFI)                 if bv.UEFI                 else "",
        os.path.dirname(bv.UEFI_IMAGEFV)         if bv.UEFI_IMAGEFV         else "",
        os.path.dirname(bv.UEFI_TOOLSFV)         if bv.UEFI_TOOLSFV         else "",
        os.path.dirname(bv.KEYMASTER_BINARY)     if bv.KEYMASTER_BINARY     else "",
        os.path.dirname(bv.UEFISEC_BINARY)       if bv.UEFISEC_BINARY       else "",
        os.path.dirname(bv.QUANTUMFV_BINARY)     if bv.QUANTUMFV_BINARY     else "",
        os.path.dirname(bv.QUANTUMSDK_BINARY)    if bv.QUANTUMSDK_BINARY    else "",
        os.path.dirname(bv.VP_BINARY)            if bv.VP_BINARY            else "",
        os.path.dirname(bv.APPSVENDOR_BUILDROOT) if bv.APPSVENDOR_BUILDROOT else "",
        os.path.dirname(bv.APPSKERNEL_BUILDROOT) if bv.APPSKERNEL_BUILDROOT else "",
        os.path.dirname(bv.ANDROID_ESP_BINARY)   if bv.ANDROID_ESP_BINARY   else "",
    ]
    # deduplicate while preserving order
    seen = set()
    result = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            result.append(d)
    return ",".join(result)


def _mavros_loadimg_list(bv: MavrosBuildVars, loadimages: str):
    """
    Mirror CMM LOADIMG branch (loadbuild_mavros.cmm).
    Returns (binary_list_str, binary_path_str).

    Groups: ssg | acpolicy | util_fw | boot | cpucp | pdp | soccp | dcp | qecp |
            aop | tz | tfa | hyp | uefi | quantum | vplan | appsboot | core_non-hlos
    """
    names = []
    dirs  = []

    def _n(path): return os.path.basename(path) if path else None
    def _d(path): return os.path.dirname(path)  if path else None

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
        dirs = [_d(bv.QUP_BINARY)]

    elif loadimages == "boot":
        for p in [bv.BOOT_BINARY, bv.BOOT_CONFIG, bv.XBLRAMDUMP_BINARY, bv.PMIC_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.BOOT_BINARY)]

    elif loadimages == "cpucp":
        for p in [bv.CPUCP_BINARY, bv.CPUCP_DTB]:
            if p: names.append(_n(p))
        dirs = [_d(bv.CPUCP_BINARY), _d(bv.CPUCP_DTB)]

    elif loadimages == "pdp":
        for p in [bv.PDP_BINARY, bv.PDP_CDB_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.PDP_BINARY), _d(bv.PDP_CDB_BINARY)]

    elif loadimages == "soccp":
        if bv.SOCCP_BINARY and os.path.exists(bv.SOCCP_BINARY):
            names.append(_n(bv.SOCCP_BINARY))
        dirs = [_d(bv.SOCCP_BINARY)]

    elif loadimages == "dcp":
        if bv.DCP_BINARY: names.append(_n(bv.DCP_BINARY))
        dirs = [_d(bv.DCP_BINARY)]

    elif loadimages == "qecp":
        for p in [bv.QECP_BINARY, bv.DCD_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.QECP_BINARY)]

    elif loadimages in ("rpm", "aop"):
        for p in [bv.AOP_BINARY, bv.AOP_DEVCFG_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.AOP_BINARY)]

    elif loadimages == "tz":
        names.append(_n(bv.TZ_BINARY))
        names.append(_n(bv.TZ_QTI_CONFIG_BINARY))
        names.append(_n(bv.TZ_DEVCFG_BINARY))
        dirs = [
            _d(bv.TZ_BINARY),
            _d(bv.TZ_QTI_CONFIG_BINARY),
        ]

    elif loadimages == "tfa":
        if bv.TFA_BINARY: names.append(_n(bv.TFA_BINARY))
        dirs = [_d(bv.TFA_BINARY)]

    elif loadimages == "hyp":
        names.append(_n(bv.HYP_BINARY))
        if bv.HYP_DEVCFG_BINARY and os.path.exists(bv.HYP_DEVCFG_BINARY):
            names.append(_n(bv.HYP_DEVCFG_BINARY))
        dirs = [_d(bv.HYP_BINARY)]

    elif loadimages == "uefi":
        for p in [bv.UEFI, bv.UEFI_DTB, bv.UEFI_IMAGEFV, bv.UEFI_TOOLSFV]:
            if p: names.append(_n(p))
        dirs = [_d(bv.UEFI), _d(bv.UEFI_IMAGEFV), _d(bv.UEFI_TOOLSFV)]

    elif loadimages == "quantum":
        for p in [bv.QUANTUMFV_BINARY, bv.QUANTUMSDK_BINARY]:
            if p: names.append(_n(p))
        dirs = [_d(bv.QUANTUMFV_BINARY), _d(bv.QUANTUMSDK_BINARY)]

    elif loadimages == "vplan":
        if bv.VP_BINARY: names.append(_n(bv.VP_BINARY))
        dirs = [_d(bv.VP_BINARY)]

    elif loadimages == "appsboot":
        if bv.META_VARIANT and "CORE" in bv.META_VARIANT:
            raise RuntimeError("Error - apps boot loading not available for CORE only metabuild")
        if bv.ANDROID_ESP_BINARY and os.path.exists(bv.ANDROID_ESP_BINARY):
            names.append(_n(bv.ANDROID_ESP_BINARY))
        if bv.APPSBOOT_BINARY and os.path.exists(bv.APPSBOOT_BINARY):
            names.append(_n(bv.APPSBOOT_BINARY))
            dirs = [_d(bv.APPSBOOT_BINARY)]
        elif bv.KERNEL_ABL_BINARY and os.path.exists(bv.KERNEL_ABL_BINARY):
            names.append(_n(bv.KERNEL_ABL_BINARY))
            dirs = [_d(bv.KERNEL_ABL_BINARY)]
        else:
            raise FileNotFoundError("No APPS ABL file!")

    elif loadimages == "core_non-hlos":
        if bv.NON_HLOS_CORE_BINARY and os.path.exists(bv.NON_HLOS_CORE_BINARY):
            names.append(_n(bv.NON_HLOS_CORE_BINARY))
            dirs = [_d(bv.NON_HLOS_CORE_BINARY)]
        else:
            raise FileNotFoundError("Core_NON-HLOS.bin not found")

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

def loadbuild_mavros(debugger, loadoption: str, loadimages: str = "",
                     extraoptions: str = "") -> str:
    """
    Phase 2 — Mavros load flow.
    Structural mirror of loadbuild_mavros.cmm MAIN subroutine.
    Returns "SUCCESS" or raises on fatal error.
    """
    import subprocess
    import time
    from datetime import datetime

    meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo
    bv   = resolve_mavros(meta)
    verify_mavros_vars(bv, loadoption)

    jtp = _get_jtp()
    # jtagprog_elf = _jtagprog_elf_path(bv)
    jtagprog_elf = jtp.elf_path
    hyp_buildroot = _mavros_hyp_buildroot(bv)

    storage_type, xml_location, xml_provision, max_partitions = _mavros_detect_storage(bv)

    _load_start_time = time.time()
    _log_file = os.path.join(os.path.expanduser("~"), ".claude", "loadbuild_logs",
                             f"loadbuild_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    os.makedirs(os.path.dirname(_log_file), exist_ok=True)

    logger.info("Mavros StorageType:   %s (hardcoded UFS)", storage_type)
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

        binary_list  = _mavros_binary_list(bv, hyp_buildroot)
        search_paths = _mavros_search_paths(bv, xml_location)

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

        # set active boot (always UFS=1 on Mavros)
        logger.info("*** SET ACTIVE BOOT PARTITION → 1 ***")
        jtp._dispatch("9", auto_command=True, arg_line=f"activeboot=1 elfpath={jtagprog_elf} _load_start_time={_load_start_time} _log_file={_log_file}")

    # ------------------------------------------------------------------
    # LOADIMG
    # ------------------------------------------------------------------
    if loadoption == "LOADIMG":
        binary_list, binary_path = _mavros_loadimg_list(bv, loadimages)
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
        std_loadbuild_la LOADIMG loadimages=ssg
        std_loadbuild_la LOADIMG loadimages=acpolicy
        std_loadbuild_la LOADIMG loadimages=util_fw
        std_loadbuild_la LOADIMG loadimages=boot
        std_loadbuild_la LOADIMG loadimages=cpucp
        std_loadbuild_la LOADIMG loadimages=pdp
        std_loadbuild_la LOADIMG loadimages=soccp
        std_loadbuild_la LOADIMG loadimages=dcp
        std_loadbuild_la LOADIMG loadimages=qecp
        std_loadbuild_la LOADIMG loadimages=aop
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
            print("    ssg           — signed_firmware_soc_view.elf, sequencer_ram.elf")
            print("    acpolicy      — hyp_ac_config.mbn, tz_ac_config.mbn, xbl_ac_config.mbn, tme_config.elf")
            print("    util_fw       — qupv3fw.elf, shrm.elf, shrm_lp6.elf, dcb.bin")
            print("    boot          — xbl.elf, xbl_config.elf, xbl_ramdump.elf, pmic.elf")
            print("    cpucp         — cpucp.elf, cpucp_dtbs.elf")
            print("    pdp           — pdp.elf, pdp_cdb.elf")
            print("    soccp         — soccp.bin")
            print("    dcp           — dcp.bin")
            print("    qecp          — qecp.bin, dcd.mbn")
            print("    aop           — aop.mbn, aop_devcfg.mbn  (alias: rpm)")
            print("    tz            — tz.mbn, tz_qti_config.mbn, tz_oem_config.mbn")
            print("    tfa           — bl31.mbn")
            print("    hyp           — hyp.mbn [hyp_devcfg.mbn]")
            print("    uefi          — uefi.elf, uefi_dtbs.elf, imagefv.elf, tools.fv")
            print("    quantum       — Quantum.elf, quantumsdk.elf")
            print("    vplan         — vplan binary")
            print("    appsboot      — android_esp.bin, abl.elf (or kernel abl fallback)")
            print("    core_non-hlos — Core_NON-HLOS.bin")
            print()
            print("  Tip: run 'std_loadbuild_la VERIFY' first to confirm all paths resolve.")
            return

        if loadoption == "PING":
            import sys, shutil
            print(f"Python executable: {sys.executable}")
            print(f"Python version:    {sys.version}")
            print(f"python on PATH:    {shutil.which('python')}")
            print(f"python3 on PATH:   {shutil.which('python3')}")
            return

        if loadoption == "FASTBOOT":
            import shutil, subprocess
            meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo
            bv   = resolve_mavros(meta)
            fastboot_script = os.path.normpath(
                os.path.join(bv.METAROOTDIR, "common", "build", "fastboot_all.py"))
            python_exe = shutil.which("python") or shutil.which("python3") or "python"
            logger.info("*** FASTBOOT: launching fastboot_all.py in new window ***")
            logger.info("*** fastboot_all.py --ap=%s --pf=%s ***",
                        bv.APPSVENDOR_BUILDROOT, bv.PRODUCT_FLAVOR)
            subprocess.Popen(
                ["cmd", "/k", python_exe, fastboot_script,
                 f"--ap={bv.APPSVENDOR_BUILDROOT}",
                 f"--pf={bv.PRODUCT_FLAVOR}",
                 "--st=ufs"],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            return

        if loadoption == "VERIFY":
            meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo
            bv   = resolve_mavros(meta)
            print("\n=== MavrosBuildVars ===")
            for name, value in vars(bv).items():
                print(f"  {name:<30} = {value}")
            print()
            return

        res = loadbuild_mavros(debugger, loadoption, loadimages, extraoptions)
        print(res)

    except Exception:
        msg = traceback.format_exc()
        logger.error("std_loadbuild_la failed:\n%s", msg)
        result.SetError(msg)
