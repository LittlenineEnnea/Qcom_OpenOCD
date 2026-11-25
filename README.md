# Qcom_OpenOCD for macOS

Qualcomm's OpenOCD debug package (LLDB/VS Code scripts and chipset configs),
made to run natively on Apple Silicon Macs, debugging over EUD SWD.

The repository contains **sources only**. Build the binaries with
`build_macos.sh`.

## Repository layout

| Path | Contents |
|---|---|
| `src/` | OpenOCD: upstream `cfd5d19` plus Qualcomm's additions (hexagon target, aarch64 system registers, gdb server hexagon/LLDB support) and a fixed EUD SWD driver |
| `gdb/src/` | GDB 18.1 sources |
| `openocd/scripts`, `openocd/config` | Qualcomm's LLDB package, chipset configs and helper scripts |
| `gdb/bin/apss.gdb` | gdb script that attaches to OpenOCD's APSS port |
| `build_macos.sh` | Builds OpenOCD and GDB and bundles their dylibs |
| `.github/workflows/build-macos.yml` | Manually triggered CI build that produces a downloadable package |

## Building locally

### Requirements

- An Apple Silicon Mac (arm64)
- Xcode Command Line Tools: `xcode-select --install`
- [Homebrew](https://brew.sh) under `/opt/homebrew`
- The build dependencies:

  ```sh
  brew install autoconf automake libtool pkgconf texinfo \
               libusb capstone jimtcl libjaylink gmp mpfr zstd
  ```

### Build

From the repository root:

```sh
./build_macos.sh              # OpenOCD and GDB
./build_macos.sh openocd      # OpenOCD only
./build_macos.sh gdb          # GDB only
```

OpenOCD takes a few minutes and GDB takes longer. Logs are in
`.build/openocd/*.log` and `.build/gdb/build/*.log`.

### What the script does

1. **OpenOCD**
   - Runs `./bootstrap nosubmodule` in `src/` if `src/configure` does not
     exist yet. It first creates the empty `src/jimtcl` and
     `src/src/jtag/drivers/libjaylink` directories that automake expects.
   - Configures out of tree in `.build/openocd` with
     `--enable-eud --enable-jlink --enable-ftdi --enable-dummy --disable-werror`.
     jimtcl and libjaylink come from Homebrew, not from git submodules.
2. **GDB**: configures `gdb/src` in `.build/gdb` for `--target=aarch64-elf
   --enable-targets=all`, without Python, simulators or binutils, and builds
   only `gdb`.
3. **Bundling**: installs each binary into the repo, copies every non-system
   dylib it needs into a `lib/` directory next to it, rewrites the load paths
   to `@rpath`, and re-signs everything ad hoc. The script fails if anything
   still points at Homebrew.

### Output

All outputs are git-ignored:

| Path | Contents |
|---|---|
| `openocd/bin/openocd` | OpenOCD executable |
| `openocd/lib/` | Bundled libusb, capstone, jimtcl, libjaylink, OpenSSL, readline |
| `openocd/share/openocd/` | OpenOCD's own Tcl scripts |
| `gdb/bin/gdb` | GDB (aarch64-elf, all targets) |
| `gdb/lib/`, `gdb/share/gdb/` | Bundled gmp, mpfr, zstd and GDB data files |

The result does not need Homebrew at run time. You can copy the folder to
another Mac that runs the same or a newer macOS.
