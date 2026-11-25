#
# Copyright (c) 2026 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file std_loadbuild_la.py
#
# @section Description
# Python port of std_loadbuild_la.cmm — LA MSM HLOS-specific build loading.
# Drives JtagProgrammer to ERASE / LOADCOMMON / LOADFULL / LOADIMG a metabuild
# onto UFS/eMMC storage.
#
# === Two-phase design ===
#
# Phase 1 — Variable resolution (resolve_from_meta / verify_vars):
#   All &BOOT_BUILDROOT, &BOOT_BINARY, &TZ_BUILDROOT … variables from the CMM
#   are resolved here via metaInfo before any flash operation touches the target.
#   Once verify_vars() returns without error you have the same guarantee the CMM
#   gets from its FILEXIST+FATALEXITUSER calls at the top of LOADCOMMON.
#
# Phase 2 — Exact CMM flow (loadbuild):
#   Branch structure mirrors the CMM MAIN subroutine exactly — same conditionals,
#   same loop topology, same order.  Inner operations are TODO stubs; fill them
#   in by calling into jtagprogrammer.py dispatch helpers.

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


# ---------------------------------------------------------------------------
# Phase 1 — Build variable container
# ---------------------------------------------------------------------------

@dataclass
class BuildVars:
    """
    All path variables the CMM resolves from contents.xml / metabuild layout.

    Naming mirrors the CMM &XXX_BUILDROOT / &XXX_BINARY convention verbatim so
    the Phase 2 code is a line-for-line parallel of the CMM.

    Buildroots — absolute directory paths (from metaInfo.getImageBasePath):
        BOOT_BUILDROOT      → "boot" image buildroot
        TZ_BUILDROOT        → "tz" image buildroot  (also used for HYP, Devcfg)
        TZAPPS_BUILDROOT    → "tz_apps" / "tzapps" image buildroot
        AOP_BUILDROOT       → "aop" image buildroot
        CPUCP_BUILDROOT     → "cpucp" image buildroot
        APPSVENDOR_BUILDROOT→ "apps_vendor" image buildroot
        METAROOTDIR         → meta root (metaInfo.getMetaPath())
        METASCRIPTSDIR      → meta scripts dir (METAROOTDIR/common/build/app/meta)

    Binary paths — full absolute paths to individual binaries
    (from metaInfo.getBinaryPath or composed as BUILDROOT/relative):
        BOOT_BINARY         XBL elf (xbl.elf / xbl_s.elf)
        BOOT_CONFIG         xbl_config.elf
        BOOT_UEFI           uefi.elf
        DDRFW_BINARY        ddr_fw.elf
        XBLRAMDUMP_BINARY   xbl_ramdump.elf
        QUP_BINARY          qupv3fw.elf   (under METAROOTDIR)
        MULTIIMGOEM_BINARY  multi_image_oem.elf (under METAROOTDIR)
        TZ_BINARY           tz.mbn
        HYP_BINARY          hyp.mbn
        KEYMASTER_BINARY    km4.mbn
        UEFISEC_BINARY      uefi_sec.mbn
        TZDEVCFG_BINARY     devcfg.mbn
        CMNLIB_BINARY       cmnlib.mbn
        CMNLIB64_BINARY     cmnlib64.mbn
        STORESEC_BINARY     storsec.mbn   (optional — may not exist)
        FMAC_BINARY         fmac.mbn      (optional — may not exist)
        APPSBOOT_BINARY     abl.elf
        AOP_BINARY          aop.mbn
        AOP_DEVCFG_BINARY   aop_devcfg.mbn
        CPUCP_BINARY        cpucp.elf
        CATEFV_BINARY       CatEFIFV.fd
        TOOLSFV_BINARY      ToolsFV.fd
        IMAGEFV_BINARY      ImageFV.fd

    Meta-level:
        PARTITION_XML_ROOT  directory containing rawprogram*.xml / patch*.xml
        META_VARIANT        build variant string (e.g. "LA", "CORE")
        PRODUCT_FLAVOR      product flavor string for fastboot_all.py
        META_CMM_LOGS       log output directory (mirrors CMM AREA.OPEN target)
    """

    # --- buildroots ---
    BOOT_BUILDROOT:         Optional[str] = None
    TZ_BUILDROOT:           Optional[str] = None
    TZAPPS_BUILDROOT:       Optional[str] = None
    AOP_BUILDROOT:          Optional[str] = None
    CPUCP_BUILDROOT:        Optional[str] = None
    APPSVENDOR_BUILDROOT:   Optional[str] = None
    METAROOTDIR:            Optional[str] = None
    METASCRIPTSDIR:         Optional[str] = None

    # --- binary full paths ---
    BOOT_BINARY:            Optional[str] = None
    BOOT_CONFIG:            Optional[str] = None
    BOOT_UEFI:              Optional[str] = None
    DDRFW_BINARY:           Optional[str] = None
    XBLRAMDUMP_BINARY:      Optional[str] = None
    QUP_BINARY:             Optional[str] = None
    MULTIIMGOEM_BINARY:     Optional[str] = None
    TZ_BINARY:              Optional[str] = None
    HYP_BINARY:             Optional[str] = None
    KEYMASTER_BINARY:       Optional[str] = None
    UEFISEC_BINARY:         Optional[str] = None
    TZDEVCFG_BINARY:        Optional[str] = None
    CMNLIB_BINARY:          Optional[str] = None
    CMNLIB64_BINARY:        Optional[str] = None
    STORESEC_BINARY:        Optional[str] = None   # optional
    FMAC_BINARY:            Optional[str] = None   # optional
    APPSBOOT_BINARY:        Optional[str] = None
    AOP_BINARY:             Optional[str] = None
    AOP_DEVCFG_BINARY:      Optional[str] = None
    CPUCP_BINARY:           Optional[str] = None
    CATEFV_BINARY:          Optional[str] = None
    TOOLSFV_BINARY:         Optional[str] = None
    IMAGEFV_BINARY:         Optional[str] = None

    # --- meta-level ---
    PARTITION_XML_ROOT:     Optional[str] = None
    META_VARIANT:           Optional[str] = None
    PRODUCT_FLAVOR:         Optional[str] = None
    META_CMM_LOGS:          Optional[str] = None


def resolve_from_contents_xml(contents_xml_path: str, meta_root: str) -> BuildVars:
    """
    Phase 1, step A — populate BuildVars by parsing contents.xml directly.

    Mirrors gen_buildflavor.cmm exactly:
      - cmm_root_path_var  attributes → buildroot variables (&BOOT_BUILDROOT etc.)
      - cmm_file_var       attributes → binary variables   (&BOOT_BINARY etc.)
      - cmm_var            on <meta_type> → META_VARIANT
      - cmm_file_var="PARTITION_XML_ROOT" → PARTITION_XML_ROOT directory

    Binary full paths are constructed as:
        windows_root_path (of the parent <build>) + file_path + file_name

    The ${xxx:yyy} template tokens in file_path are already resolved by the
    meta build system before the contents.xml we receive is written — the
    actual path on disk uses the resolved sub-directory, so we read it as-is.
    Any remaining ${...} tokens (not resolved at meta-generation time) are
    stripped so the path stays usable.

    Only <download_file> and <file_ref> children are scanned for cmm_file_var
    because that is where the CMM variable binaries live.  <file_ref> entries
    with cmm_root_path_var are the buildroot rows; those live on <windows_root_path>.
    """
    import re
    from xml.etree import ElementTree as ET
    import platform

    bv = BuildVars()

    # --- meta root & scripts dir ---
    bv.METAROOTDIR    = meta_root.replace("\\", "/")
    bv.METASCRIPTSDIR = "/".join([bv.METAROOTDIR, "common/build/app/meta"])
    bv.META_CMM_LOGS  = os.path.join(os.environ.get("TEMP", "/tmp"),
                                     "meta_cmm_logs")

    tree = ET.parse(contents_xml_path)
    root = tree.getroot()

    is_windows = platform.system() == "Windows"

    def _strip_tokens(s: str) -> str:
        """Replace ${var:default} tokens with their default value; strip bare ${var} tokens."""
        s = re.sub(r'\$\{[^:}]+:([^}]+)\}', r'\1', s)   # ${var:default} → default
        s = re.sub(r'\$\{[^}]+\}', '', s)                 # ${var} with no default → remove
        return s

    def _clean(s: str) -> str:
        # normalise separators, collapse multiple slashes, preserve leading //
        s = _strip_tokens(s or "").replace("\\", "/")
        # collapse runs of slashes that are NOT the UNC-style leading //
        s = re.sub(r'(?<!^)/{2,}', '/', s)
        return s.rstrip("/")

    def _join(*parts: str) -> str:
        """Join path segments, skip empty ones, preserve UNC // on first part."""
        result = ""
        for p in parts:
            p = p.strip("/") if result else p.rstrip("/")
            if not p:
                continue
            result = p if not result else result + "/" + p
        return result

    def _abs(path: str) -> str:
        """Make a path absolute relative to METAROOTDIR if it starts with '.'."""
        if path.startswith("./") or path == ".":
            return _join(bv.METAROOTDIR, path[2:] if path.startswith("./") else "")
        return path

    # --- META_VARIANT from <product_info><meta_type cmm_var="META_VARIANT"> ---
    # --- PRODUCT_FLAVOR from <product_info><default flavor="..."> ---
    product_info = root.find("product_info")
    if product_info is not None:
        meta_type = product_info.find("meta_type")
        if meta_type is not None:
            bv.META_VARIANT = (meta_type.text or "").strip()
        default_elem = product_info.find("default")
        if default_elem is not None:
            bv.PRODUCT_FLAVOR = default_elem.get("flavor")

    # --- walk every <build> in <builds_flat> ---
    builds_flat = root.find("builds_flat")
    if builds_flat is None:
        raise RuntimeError("contents.xml has no <builds_flat> element")

    # maps cmm_file_var name → full resolved path
    file_vars: dict = {}
    # maps cmm_root_path_var name → buildroot path
    root_vars: dict = {}

    for build in builds_flat:
        # pick the right root path for this OS
        if is_windows:
            root_path_elem = build.find("windows_root_path")
        else:
            root_path_elem = build.find("linux_root_path")

        if root_path_elem is None:
            continue

        build_root = _abs(_clean(root_path_elem.text or ""))
        # --- cmm_root_path_var on the root path element itself ---
        var = root_path_elem.get("cmm_root_path_var")
        if var:
            root_vars[var] = build_root

        # --- scan children for cmm_file_var ---
        for child in build:
            var = child.get("cmm_file_var")
            if not var:
                continue

            file_name_elem = child.find("file_name")
            file_path_elem = child.find("file_path")

            if file_name_elem is None:
                continue

            file_name = _clean(file_name_elem.text or "")
            file_path = _clean(file_path_elem.text or "") if file_path_elem is not None else ""

            # PARTITION_XML_ROOT has file_name="." — keep as directory only
            if file_name == ".":
                full = _join(build_root, file_path)
            else:
                full = _join(build_root, file_path, file_name)

            file_vars[var] = _abs(full)

    # --- populate BuildVars from the two maps ---

    # buildroots
    bv.BOOT_BUILDROOT       = root_vars.get("BOOT_BUILDROOT")
    bv.TZ_BUILDROOT         = root_vars.get("TZ_BUILDROOT")
    bv.TZAPPS_BUILDROOT     = root_vars.get("TZAPPS_BUILDROOT")
    bv.AOP_BUILDROOT        = root_vars.get("AOP_BUILDROOT")
    bv.CPUCP_BUILDROOT      = root_vars.get("CPUCP_BUILDROOT")
    bv.APPSVENDOR_BUILDROOT = root_vars.get("APPSVENDOR_BUILDROOT")

    # binaries
    bv.BOOT_BINARY          = file_vars.get("BOOT_BINARY")
    bv.BOOT_CONFIG          = file_vars.get("BOOT_CONFIG")
    bv.BOOT_UEFI            = file_vars.get("BOOT_UEFI")
    bv.DDRFW_BINARY         = file_vars.get("DDRFW_BINARY")
    bv.XBLRAMDUMP_BINARY    = file_vars.get("XBLRAMDUMP_BINARY")
    bv.QUP_BINARY           = file_vars.get("QUP_BINARY")
    bv.MULTIIMGOEM_BINARY   = file_vars.get("MULTIIMGOEM_BINARY")
    bv.TZ_BINARY            = file_vars.get("TZ_BINARY")
    bv.HYP_BINARY           = file_vars.get("HYP_BINARY")
    bv.KEYMASTER_BINARY     = file_vars.get("KEYMASTER_BINARY")
    bv.UEFISEC_BINARY       = file_vars.get("UEFISEC_BINARY")
    bv.TZDEVCFG_BINARY      = file_vars.get("TZDEVCFG_BINARY")
    bv.CMNLIB_BINARY        = file_vars.get("CMNLIB_BINARY")
    bv.CMNLIB64_BINARY      = file_vars.get("CMNLIB64_BINARY")
    bv.STORESEC_BINARY      = file_vars.get("STORESEC_BINARY")
    bv.FMAC_BINARY          = file_vars.get("FMAC_BINARY")
    bv.APPSBOOT_BINARY      = file_vars.get("APPSBOOT_BINARY")
    bv.AOP_BINARY           = file_vars.get("AOP_BINARY")
    bv.AOP_DEVCFG_BINARY    = file_vars.get("AOP_DEVCFG_BINARY")
    bv.CPUCP_BINARY         = file_vars.get("CPUCP_BINARY")
    bv.CATEFV_BINARY        = file_vars.get("CATEFV_BINARY")
    bv.TOOLSFV_BINARY       = file_vars.get("TOOLSFV_BINARY")
    bv.IMAGEFV_BINARY       = file_vars.get("IMAGEFV_BINARY")
    bv.PARTITION_XML_ROOT   = file_vars.get("PARTITION_XML_ROOT")

    return bv


def resolve_from_meta(meta: metaInfo) -> BuildVars:
    """Thin wrapper — reads contentsXmlPath from g_dbgContext, delegates to resolve_from_contents_xml.

    After XML resolution, checks metaInfo for engineering build path overrides set via
    image_path -i <image> -s <path> (launch.json BOOT_BUILD_PATH / TZ_BUILD_PATH / AOSS_BUILD_PATH).
    For any image whose base path was overridden, rebases all binary paths that fall under
    the original XML buildroot to the engineering path instead.
    """
    contents_xml = str(g_dbgContext.contentsXmlPath)
    meta_root    = meta.getMetaPath()
    bv = resolve_from_contents_xml(contents_xml, meta_root)

    # Map image name → (BuildVars buildroot attr, list of binary attrs that live under it)
    _image_buildroot_map = {
        "boot": ("BOOT_BUILDROOT", [
            "BOOT_BINARY", "BOOT_CONFIG", "BOOT_UEFI", "DDRFW_BINARY",
            "XBLRAMDUMP_BINARY", "QUP_BINARY", "MULTIIMGOEM_BINARY", "APPSBOOT_BINARY",
            "CATEFV_BINARY", "IMAGEFV_BINARY", "TOOLSFV_BINARY",
        ]),
        "tz": ("TZ_BUILDROOT", [
            "TZ_BINARY", "HYP_BINARY", "KEYMASTER_BINARY", "UEFISEC_BINARY",
            "TZDEVCFG_BINARY", "CMNLIB_BINARY", "CMNLIB64_BINARY",
            "STORESEC_BINARY", "FMAC_BINARY",
        ]),
        "aop": ("AOP_BUILDROOT", [
            "AOP_BINARY", "AOP_DEVCFG_BINARY",
        ]),
    }

    for image_name, (buildroot_attr, binary_attrs) in _image_buildroot_map.items():
        eng_path = meta.getImageBasePath(image_name)
        if not eng_path or eng_path == "ERROR":
            continue
        xml_buildroot = getattr(bv, buildroot_attr, None)
        if not xml_buildroot:
            continue
        eng_path = eng_path.replace("\\", "/").rstrip("/")
        xml_buildroot_norm = xml_buildroot.replace("\\", "/").rstrip("/")
        if eng_path == xml_buildroot_norm:
            continue
        logger.info("Engineering build override: %s → %s (was %s)",
                    image_name, eng_path, xml_buildroot_norm)
        setattr(bv, buildroot_attr, eng_path)
        for attr in binary_attrs:
            old_val = getattr(bv, attr, None)
            if old_val and old_val.replace("\\", "/").startswith(xml_buildroot_norm):
                new_val = eng_path + old_val.replace("\\", "/")[len(xml_buildroot_norm):]
                setattr(bv, attr, new_val)

    return bv


def verify_vars(bv: BuildVars, loadoption: str) -> None:
    """
    Phase 1, step B — assert all required paths exist for the requested loadoption.

    Mirrors the CMM do std_utils FILEXIST FATALEXITUSER block inside LOADCOMMON.
    Raises FileNotFoundError on the first missing required path.
    Optional binaries (STORESEC, FMAC) are checked with os.path.exists() only
    at call-sites that need them.

    loadoption — one of: ERASEONLY, LOADCOMMON, LOADFULL, LOADIMG
    """

    def _require(path: Optional[str], label: str):
        if not path or not os.path.exists(path):
            raise FileNotFoundError(
                f"Required binary not found: {label} -> {path!r}\n"
                "Check image names and binary filenames in resolve_from_meta()."
            )

    # Always required (JtagProgrammer.elf path derivation depends on this)
    _require(bv.BOOT_BINARY, "BOOT_BINARY")

    if loadoption in ("LOADCOMMON", "LOADFULL"):
        _require(bv.BOOT_BINARY,        "BOOT_BINARY")
        _require(bv.BOOT_CONFIG,        "BOOT_CONFIG")
        _require(bv.BOOT_UEFI,          "BOOT_UEFI")
        _require(bv.DDRFW_BINARY,       "DDRFW_BINARY")
        _require(bv.XBLRAMDUMP_BINARY,  "XBLRAMDUMP_BINARY")
        _require(bv.QUP_BINARY,         "QUP_BINARY")
        _require(bv.CPUCP_BINARY,       "CPUCP_BINARY")
        _require(bv.TZ_BINARY,          "TZ_BINARY")
        _require(bv.HYP_BINARY,         "HYP_BINARY")
        _require(bv.KEYMASTER_BINARY,   "KEYMASTER_BINARY")
        _require(bv.UEFISEC_BINARY,     "UEFISEC_BINARY")
        _require(bv.TZDEVCFG_BINARY,    "TZDEVCFG_BINARY")
        _require(bv.AOP_BINARY,         "AOP_BINARY")
        _require(bv.AOP_DEVCFG_BINARY,  "AOP_DEVCFG_BINARY")

        # CMM: if META_VARIANT does NOT contain "CORE" → check APPSBOOT_BINARY
        #      else                                      → check CATEFV + TOOLSFV
        if bv.META_VARIANT and "CORE" not in bv.META_VARIANT:
            _require(bv.APPSBOOT_BINARY, "APPSBOOT_BINARY")
        else:
            _require(bv.CATEFV_BINARY,  "CATEFV_BINARY")
            _require(bv.TOOLSFV_BINARY, "TOOLSFV_BINARY")

    logger.info("BuildVars verification passed for loadoption=%s", loadoption)


# ---------------------------------------------------------------------------
# Phase 2 — Exact CMM flow scaffold
# ---------------------------------------------------------------------------

def _detect_storage(debugger, bv: BuildVars):
    """
    Mirror CMM: do hwio → do std_utils HWIO_INF BOOT_CONFIG FAST_BOOT → ENTRY &StorageOption.
    Returns (storage_type, xml_location, xml_provision, max_partitions, programmer_path).

    TODO: read BOOT_CONFIG FAST_BOOT HWIO via openocd/LLDB and return real values.
    Placeholder returns UFS defaults so the scaffold compiles and runs to the first TODO.
    """
    # TODO: call hwio.py equivalent to read FAST_BOOT register
    storage_option = 0x0   # 0x0 = UFS, else eMMC

    if storage_option == 0x0:
        programmer = os.path.join(
            bv.BOOT_BUILDROOT,
            "boot_images/boot/QcomPkg/SocPkg/Clarence/Tools/storage/UFS/JtagProgrammer.cmm"
        )
        xml_location  = bv.PARTITION_XML_ROOT
        xml_provision = os.path.join(bv.METAROOTDIR, "config", "ufs", "provision")
        storage_type  = "ufs"
        max_partitions = 6
    else:
        programmer = os.path.join(
            bv.BOOT_BUILDROOT,
            "boot_images/boot/QcomPkg/SocPkg/Clarence/Tools/storage/eMMC/JtagProgrammer.cmm"
        )
        xml_location  = os.path.join(bv.METAROOTDIR, "common", "build", "emmc")
        xml_provision = os.path.join(bv.METAROOTDIR, "config", "ufs", "provision")
        storage_type  = "emmc"
        max_partitions = 1

    return storage_type, xml_location, xml_provision, max_partitions, programmer


def _jtagprog_elf_path(bv: BuildVars) -> str:
    """
    Mirror CMM:
        &jtagprogrammer_elf_location = OS.FILE.PATH(&BOOT_BUILDROOT/&BOOT_BINARY)
        &jtagprogrammer_elf_location = &jtagprogrammer_elf_location + "/JtagProgrammer.elf"
    JtagProgrammer.elf lives in the same directory as xbl.elf.
    """
    xbl_dir = os.path.dirname(bv.BOOT_BINARY)
    return os.path.join(xbl_dir, "JtagProgrammer.elf")


def _hyp_buildroot(bv: BuildVars) -> str:
    """
    Mirror CMM:
        &HYP_Binary_Buildroot = &TZ_BUILDROOT
        IF STRING.SCAN("&HYP_BINARY","common",0) != -1:
            &HYP_Binary_Buildroot = &METAROOTDIR
    """
    if bv.HYP_BINARY and "common" in bv.HYP_BINARY:
        return bv.METAROOTDIR
    return bv.TZ_BUILDROOT


def _devcfg_buildroot(bv: BuildVars) -> str:
    """Mirror CMM: &Devcfg_Binary_Buildroot = &TZ_BUILDROOT (no override logic here)."""
    return bv.TZ_BUILDROOT


def _get_jtp():
    """
    Retrieve the live JtagProgrammer instance created by processCreateCommands.
    jtagprogrammer.py stores it as _jtp_instance at module level.
    """
    import sys
    jtp_mod = sys.modules.get("jtagprogrammer") or sys.modules.get("utils.jtagprogrammer")
    if jtp_mod is None:
        raise RuntimeError("jtagprogrammer module not loaded — run 'jtagprogrammer elfpath=...' first")
    jtp = getattr(jtp_mod, "_jtp_instance", None)
    if jtp is None:
        raise RuntimeError("JtagProgrammer not set up — run 'jtagprogrammer elfpath=...' first")
    return jtp


def loadbuild(debugger, loadoption: str, loadimages: str = "",
              extraoptions: str = "") -> str:
    """
    Phase 2 — main load flow.  Exact structural mirror of CMM MAIN subroutine.

    Returns "SUCCESS" or raises on fatal error.

    loadoption  : ERASEONLY | LOADCOMMON | LOADFULL | LOADIMG
    loadimages  : image name (used only with LOADIMG)
    extraoptions: comma-separated flags, e.g. "provision"
    """
    import subprocess

    # ------------------------------------------------------------------
    # Phase 1 — resolve and verify all variables
    # ------------------------------------------------------------------

    meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo
    bv   = resolve_from_meta(meta)
    verify_vars(bv, loadoption)

    jtp = _get_jtp()

    # Derived paths used throughout
    jtagprog_elf  = _jtagprog_elf_path(bv)
    hyp_buildroot = _hyp_buildroot(bv)
    devcfg_buildroot = _devcfg_buildroot(bv)

    (storage_type, xml_location, xml_provision,
     max_partitions, programmer) = _detect_storage(debugger, bv)

    storesec_present = (bv.STORESEC_BINARY is not None and
                        os.path.exists(bv.STORESEC_BINARY))

    logger.info("StorageType:        %s", storage_type)
    logger.info("jtagprogrammer_elf: %s", jtagprog_elf)
    logger.info("xml_location:       %s", xml_location)
    logger.info("max_partitions:     %d", max_partitions)

    # ------------------------------------------------------------------
    # PROVISION
    # ------------------------------------------------------------------

    if "provision" in extraoptions:
        logger.info("*** PROVISION ***")
        jtp._dispatch("PROVISION", auto_command=True,
                      arg_line=f"elfpath={jtagprog_elf} xml=provision_ufs31.xml searchpaths={xml_provision}")

    # ------------------------------------------------------------------
    # ERASE
    # ------------------------------------------------------------------

    if loadoption in ("ERASEONLY", "LOADCOMMON", "LOADFULL"):
        if storage_type in ("ufs", "emmc"):
            logger.info("*** ERASE ***")
            jtp._dispatch("ERASE", auto_command=True, arg_line=f"elfpath={jtagprog_elf}")

    # ------------------------------------------------------------------
    # LOADCOMMON / LOADFULL — flash common images
    # ------------------------------------------------------------------

    if loadoption in ("LOADCOMMON", "LOADFULL"):

        binary_list  = _build_binary_list(bv, hyp_buildroot, devcfg_buildroot,
                                          storesec_present)
        search_paths = _build_search_paths(bv, hyp_buildroot, xml_location)

        # --- flash GPT partition table ---
        for partition in range(max_partitions):
            xml   = f"{xml_location}/rawprogram{partition}.xml"
            files = f"gpt_main{partition}.bin,gpt_backup{partition}.bin"
            logger.info("*** LOAD GPT %s ***", files)
            jtp._dispatch("LOAD", auto_command=True,
                          arg_line=f"searchpaths={xml_location} xml={xml} files={files} elfpath={jtagprog_elf}")

        # --- flash binary images ---
        for partition in range(max_partitions):
            xml = f"{xml_location}/rawprogram{partition}.xml"
            logger.info("*** LOAD binaries partition %d ***", partition)
            jtp._dispatch("LOAD", auto_command=True,
                          arg_line=f"searchpaths={search_paths} xml={xml} files={binary_list} elfpath={jtagprog_elf}")

        # --- apply disk patches ---
        for partition in range(max_partitions):
            xml = f"{xml_location}/patch{partition}.xml"
            logger.info("*** PATCH partition %d ***", partition)
            jtp._dispatch("PATCH", auto_command=True, arg_line=f"xml={xml} elfpath={jtagprog_elf}")

        # --- set active boot partition ---
        activeboot = 0 if storage_type == "emmc" else 1
        logger.info("*** SET ACTIVE BOOT PARTITION → %d ***", activeboot)
        jtp._dispatch("9", auto_command=True, arg_line=f"activeboot={activeboot} elfpath={jtagprog_elf}")

    # ------------------------------------------------------------------
    # LOADIMG — flash a single named image group
    # ------------------------------------------------------------------

    if loadoption == "LOADIMG":
        binary_list, binary_path = _build_loadimg_list(
            bv, loadimages, hyp_buildroot, devcfg_buildroot, storesec_present
        )
        search_paths = f"{xml_location},{binary_path}"

        for partition in range(max_partitions):
            xml = f"{xml_location}/rawprogram{partition}.xml"
            logger.info("*** LOAD %s partition %d ***", loadimages, partition)
            jtp._dispatch("LOAD", auto_command=True,
                          arg_line=f"searchpaths={search_paths} xml={xml} files={binary_list} elfpath={jtagprog_elf}")

        activeboot = 0 if storage_type == "emmc" else 1
        logger.info("*** SET ACTIVE BOOT PARTITION → %d ***", activeboot)
        jtp._dispatch("9", auto_command=True, arg_line=f"activeboot={activeboot} elfpath={jtagprog_elf}")

        debugger.HandleCommand("breakpoint delete --force")

    # ------------------------------------------------------------------
    # LOADFULL — flash HLOS images via fastboot
    # ------------------------------------------------------------------

    if loadoption == "LOADFULL":
        activeboot = 0 if storage_type == "emmc" else 1
        logger.info("*** SET ACTIVE BOOT PARTITION → %d (pre-fastboot) ***", activeboot)
        jtp._dispatch("9", auto_command=True, arg_line=f"activeboot={activeboot} elfpath={jtagprog_elf}")

        # Mirror CMM: B.DELETE /ALL before spawning fastboot
        debugger.HandleCommand("breakpoint delete --force")

        # CMM: &METASCRIPTSDIR/../../../build/fastboot_all.py
        # METASCRIPTSDIR = METAROOTDIR/common/build/app/meta → 3 levels up = METAROOTDIR/common/build
        fastboot_script = os.path.normpath(
            os.path.join(bv.METAROOTDIR, "common", "build", "fastboot_all.py"))
        python_exe = shutil.which("python") or shutil.which("python3") or "python"
        logger.info("*** RESET BOARD NOW — fastboot_all.py is waiting for device ***")
        logger.info("*** LOAD HLOS via fastboot_all.py --ap=%s --pf=%s --st=%s ***",
                    bv.APPSVENDOR_BUILDROOT, bv.PRODUCT_FLAVOR, storage_type)
        subprocess.Popen(
            ["cmd", "/k", python_exe, fastboot_script,
             f"--ap={bv.APPSVENDOR_BUILDROOT}",
             f"--pf={bv.PRODUCT_FLAVOR}",
             f"--st={storage_type}"],
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )

    # ------------------------------------------------------------------
    # Done
    # ------------------------------------------------------------------

    logger.info("*** LOAD SUCCESS ***")
    return "SUCCESS"


# ---------------------------------------------------------------------------
# Binary list helpers  (mirror the CMM &binary_list construction blocks)
# ---------------------------------------------------------------------------

def _build_binary_list(bv: BuildVars, hyp_buildroot: str,
                       devcfg_buildroot: str, storesec_present: bool) -> str:
    """
    Mirror CMM binary_list construction in the LOADCOMMON block (lines 193-255).
    Returns a comma-separated string of bare filenames (os.path.basename).
    """
    names = []

    # boot binaries
    names.append(os.path.basename(bv.BOOT_BINARY))
    names.append(os.path.basename(bv.BOOT_CONFIG))
    names.append(os.path.basename(bv.BOOT_UEFI))
    names.append(os.path.basename(bv.DDRFW_BINARY))
    names.append(os.path.basename(bv.XBLRAMDUMP_BINARY))

    # meta binaries
    names.append(os.path.basename(bv.QUP_BINARY))
    names.append(os.path.basename(bv.MULTIIMGOEM_BINARY))

    # AOP binaries
    names.append(os.path.basename(bv.AOP_BINARY))
    names.append(os.path.basename(bv.AOP_DEVCFG_BINARY))

    # CPUCP
    names.append(os.path.basename(bv.CPUCP_BINARY))

    # TZ / HYP / devcfg
    names.append(os.path.basename(bv.TZ_BINARY))
    names.append(os.path.basename(bv.HYP_BINARY))
    names.append(os.path.basename(bv.KEYMASTER_BINARY))
    if bv.CMNLIB_BINARY:
        names.append(os.path.basename(bv.CMNLIB_BINARY))
    if bv.CMNLIB64_BINARY:
        names.append(os.path.basename(bv.CMNLIB64_BINARY))
    names.append(os.path.basename(bv.TZDEVCFG_BINARY))
    names.append(os.path.basename(bv.UEFISEC_BINARY))

    # optional
    if storesec_present:
        names.append(os.path.basename(bv.STORESEC_BINARY))
    if bv.FMAC_BINARY and os.path.exists(bv.FMAC_BINARY):
        names.append(os.path.basename(bv.FMAC_BINARY))

    # appsboot — mirror CMM META_VARIANT check
    if bv.META_VARIANT and "CORE" not in bv.META_VARIANT:
        names.append(os.path.basename(bv.APPSBOOT_BINARY))
    else:
        names.append(os.path.basename(bv.CATEFV_BINARY))
        names.append(os.path.basename(bv.TOOLSFV_BINARY))

    return ",".join(names)


def _build_search_paths(bv: BuildVars, hyp_buildroot: str,
                        xml_location: str) -> str:
    """
    Mirror CMM &SearchPaths construction for the binary flash loop (line 276).
    Returns a comma-separated string of directory paths.
    """
    dirs = [
        xml_location,
        os.path.dirname(bv.BOOT_BINARY),
        os.path.dirname(bv.QUP_BINARY),
        os.path.dirname(bv.AOP_BINARY),
        os.path.dirname(bv.AOP_DEVCFG_BINARY),
        os.path.dirname(bv.MULTIIMGOEM_BINARY),
        os.path.dirname(bv.APPSBOOT_BINARY) if bv.APPSBOOT_BINARY else "",
        os.path.dirname(bv.TZ_BINARY),
        os.path.dirname(bv.CPUCP_BINARY),
        os.path.dirname(bv.KEYMASTER_BINARY),
        os.path.dirname(bv.UEFISEC_BINARY),
    ]
    return ",".join(d for d in dirs if d)


def _build_loadimg_list(bv: BuildVars, loadimages: str,
                        hyp_buildroot: str, devcfg_buildroot: str,
                        storesec_present: bool):
    """
    Mirror CMM LOADIMG branch (lines 322-438).
    Returns (binary_list_str, binary_path_str) for the requested image group.

    loadimages: "xbl" | "tz" | "rpm" | "aop" | "appsboot" | "qup/multioem"
                | "uefifv" | "cpucp"
    """
    names = []
    dirs  = []

    if loadimages == "xbl":
        names = [
            os.path.basename(bv.BOOT_BINARY),
            os.path.basename(bv.BOOT_CONFIG),
            os.path.basename(bv.BOOT_UEFI),
            os.path.basename(bv.DDRFW_BINARY),
            os.path.basename(bv.XBLRAMDUMP_BINARY),
            os.path.basename(bv.QUP_BINARY),
        ]
        dirs = [os.path.dirname(bv.BOOT_BINARY), os.path.dirname(bv.QUP_BINARY)]

    elif loadimages == "tz":
        names = [
            os.path.basename(bv.TZ_BINARY),
            os.path.basename(bv.HYP_BINARY),
            os.path.basename(bv.KEYMASTER_BINARY),
            os.path.basename(bv.CMNLIB_BINARY),
            os.path.basename(bv.CMNLIB64_BINARY),
            os.path.basename(bv.TZDEVCFG_BINARY),
            os.path.basename(bv.UEFISEC_BINARY),
        ]
        if storesec_present:
            names.append(os.path.basename(bv.STORESEC_BINARY))
        if bv.FMAC_BINARY and os.path.exists(bv.FMAC_BINARY):
            names.append(os.path.basename(bv.FMAC_BINARY))
        dirs = [
            os.path.dirname(bv.HYP_BINARY),
            os.path.dirname(bv.TZ_BINARY),
            os.path.dirname(bv.KEYMASTER_BINARY),
        ]

    elif loadimages in ("rpm", "aop"):
        names = [
            os.path.basename(bv.AOP_BINARY),
            os.path.basename(bv.AOP_DEVCFG_BINARY),
        ]
        dirs = [os.path.dirname(bv.AOP_BINARY)]

    elif loadimages == "appsboot":
        if bv.META_VARIANT and "CORE" in bv.META_VARIANT:
            raise RuntimeError("Error - apps boot loading not available for CORE only metabuild")
        names = [os.path.basename(bv.APPSBOOT_BINARY)]
        dirs  = [os.path.dirname(bv.APPSBOOT_BINARY)]

    elif loadimages == "qup/multioem":
        names = [
            os.path.basename(bv.QUP_BINARY),
            os.path.basename(bv.MULTIIMGOEM_BINARY),
        ]
        dirs = [os.path.dirname(bv.QUP_BINARY)]

    elif loadimages == "uefifv":
        names = [
            os.path.basename(bv.CATEFV_BINARY),
            os.path.basename(bv.IMAGEFV_BINARY),
            os.path.basename(bv.TOOLSFV_BINARY),
        ]
        dirs = [
            os.path.dirname(bv.CATEFV_BINARY),
            os.path.dirname(bv.IMAGEFV_BINARY),
            os.path.dirname(bv.TOOLSFV_BINARY),
        ]

    elif loadimages == "cpucp":
        names = [os.path.basename(bv.CPUCP_BINARY)]
        dirs  = [os.path.dirname(bv.CPUCP_BINARY)]

    else:
        raise ValueError(f"Unknown loadimages value: {loadimages!r}")

    return ",".join(names), ",".join(dict.fromkeys(d for d in dirs if d))


# ---------------------------------------------------------------------------
# LLDB integration — mirrors jtagprogrammer.py registration pattern exactly
# ---------------------------------------------------------------------------

def std_loadbuild_la(debugger, command, exe_ctx, result):
    """
    LLDB command:  std_loadbuild_la <LOADOPTION> [loadimages=<img>] [extraoptions=<opts>]

    Examples:
        std_loadbuild_la ERASEONLY
        std_loadbuild_la LOADCOMMON
        std_loadbuild_la LOADFULL
        std_loadbuild_la LOADIMG loadimages=xbl
        std_loadbuild_la LOADIMG loadimages=tz
        std_loadbuild_la LOADCOMMON extraoptions=provision
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
            print("Usage: std_loadbuild_la ERASEONLY|LOADCOMMON|LOADFULL|LOADIMG|FASTBOOT [loadimages=<img>] [extraoptions=<opts>]")
            print("       std_loadbuild_la VERIFY")
            return

        if loadoption == "LOADIMG" and not loadimages:
            print("Usage: std_loadbuild_la LOADIMG loadimages=<image_group>")
            print()
            print("  Image groups:")
            print("    xbl          — xbl.elf, xbl_config.elf, uefi.elf, ddr_fw.elf,")
            print("                   xbl_ramdump.elf, qupv3fw.elf")
            print("    tz           — tz.mbn, hyp.mbn, km4.mbn, cmnlib*.mbn,")
            print("                   devcfg.mbn, uefi_sec.mbn [storsec.mbn] [fmac.mbn]")
            print("    aop          — aop.mbn, aop_devcfg.mbn  (alias: rpm)")
            print("    appsboot     — abl.elf  (LA metabuilds only)")
            print("    qup/multioem — qupv3fw.elf, multi_image_oem.elf")
            print("    uefifv       — CatEFIFV.fd, ImageFV.fd, ToolsFV.fd")
            print("    cpucp        — cpucp.elf")
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
            bv   = resolve_from_meta(meta)
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
                 f"--st=ufs"],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            return

        if loadoption == "VERIFY":
            meta = g_dbgContext.openocdConfig._openocdConfig__metaInfo
            bv   = resolve_from_meta(meta)
            print("\n=== BuildVars ===")
            for name, value in vars(bv).items():
                print(f"  {name:<30} = {value}")
            print()
            return

        res = loadbuild(debugger, loadoption, loadimages, extraoptions)
        print(res)

    except Exception:
        msg = traceback.format_exc()
        logger.error("std_loadbuild_la failed:\n%s", msg)
        result.SetError(msg)
